#!/usr/bin/env python3
"""Verify the flat side-panel redesign (user spec 2026-09-27).

Spawns a throwaway uvicorn on :8905 (isolated ANKI_STATE_DIR + temp corpus,
dead AnkiConnect) serving the REAL frontend/dist build, then drives it with
Playwright at 1920x1200 dark mode (the user's display) and asserts:

  right column (笔记):
    1. no md-elevated-card shell inside .note-column
    2. .note-column bg == surface-container-low (dark #1d1b20)
       != page bg (dark surface #141218)
    3. border-left == 1px solid outline-variant (#49454f) — the divider
    4. tint reaches the viewport right edge
    5. full viewport height (100vh)
    6. header title == 笔记 (reading mode too — unified)
  left column (相关卡片):
    7. no md-elevated-card shell inside .related-column
    8. .related-list .similar-item bg == surface-container-high (#2b2930)
    9. header title == 本片段已制卡片 (reading mode keeps dynamic title)

Run: backend/.venv/bin/python scripts/flat_panels_ui_test.py
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8905"
REPO_ROOT = Path(__file__).resolve().parent.parent
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, method=method, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_status": e.code}


def wait_ready(timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = api("/api/status")
            if "reading_mode" in st or st.get("anki") in ("ok", "error"):
                return st
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("test backend never came up")


NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经支配。

## 二、颈筋膜

分为浅、中、深三层，各层之间形成筋膜鞘，容纳血管神经。

## 三、颈动脉三角

境界由胸锁乳突肌前缘、肩胛舌骨肌上腹和二腹肌后腹围成。
"""


def main():
    state = tempfile.mkdtemp(prefix="flat-ui-state-")
    notes = Path(tempfile.mkdtemp(prefix="flat-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE_A, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",
        "ANKICONNECT_URL": "http://127.0.0.1:18765",  # dead — never touch live
        "ANKI_DAILY_READ": "4",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8905"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        run()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def run():
    st = wait_ready()
    check("backend up on :8905", st.get("anki") in ("ok", "error"), st)

    # seed the reading list (whole-file seeding, escape-hatch API)
    r = api("/api/reading/list/add", "POST",
            {"path": "2026/解剖/颈部.md", "fresh": True})
    check("list add ok", r.get("ok") is True or "path" in json.dumps(r), r)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1200},
                                color_scheme="dark")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".nav-rail", timeout=30000)

        # start the round from the unified start screen
        page.locator("md-filled-button", has_text="开始").first.click()
        page.wait_for_selector(".reading-crumb", timeout=30000)
        # both side columns must be up (wide3 = 1500px+, we're at 1920)
        page.wait_for_selector(".note-column", timeout=10000)
        page.wait_for_selector(".related-column", timeout=10000)
        page.wait_for_timeout(800)
        page.screenshot(path="/tmp/flat-ui-wide.png")

        # ---- right column: flat tinted sidebar ----
        cards_in_note = page.locator(".note-column md-elevated-card").count()
        check("R1 no card shell in note column", cards_in_note == 0, cards_in_note)

        col = page.locator(".note-column").first
        col_bg = col.evaluate("el => getComputedStyle(el).backgroundColor")
        body_bg = page.locator("body").evaluate("el => getComputedStyle(el).backgroundColor")
        check("R2 note column bg == container-low #1d1b20",
              col_bg == "rgb(29, 27, 32)", col_bg)
        check("R2b page bg == surface #141218 (tint differs)",
              body_bg == "rgb(20, 18, 24)", body_bg)

        bl = col.evaluate("el => { const s = getComputedStyle(el);"
                          " return [s.borderLeftWidth, s.borderLeftStyle, s.borderLeftColor]; }")
        check("R3 border-left 1px solid outline-variant #49454f",
              bl[0] == "1px" and bl[1] == "solid" and bl[2] == "rgb(73, 69, 79)", bl)

        col_box = col.bounding_box()
        check("R4 tint reaches viewport right edge",
              abs((col_box["x"] + col_box["width"]) - 1920) <= 2,
              f"right edge at {col_box['x'] + col_box['width']}")
        # ::after must paint the same tint to the edge (elementFromPoint)
        edge_bg = page.evaluate("""() => {
            const el = document.elementFromPoint(1918, 600);
            return el ? getComputedStyle(el).backgroundColor : 'none';
        }""")
        after_paints = page.evaluate("""() => {
            const col = document.querySelector('.note-column');
            const s = getComputedStyle(col, '::after');
            return [s.backgroundColor, s.position];
        }""")
        check("R4b ::after paints container-low tint (absolute)",
              after_paints[0] == "rgb(29, 27, 32)" and after_paints[1] == "absolute",
              after_paints)
        check("R4c pixel at right edge shows tint or panel content",
              edge_bg in ("rgb(29, 27, 32)",) or edge_bg != "rgb(20, 18, 24)", edge_bg)

        check("R5 note column is full viewport height",
              abs(col_box["height"] - 1200) <= 2, col_box["height"])
        check("R5b column starts at viewport top", col_box["y"] == 0, col_box["y"])

        title = page.locator(".note-column .note-panel-title").first.inner_text()
        check("R6 header title == 笔记", title == "笔记", title)

        # ---- left column: flat bg + card boxes ----
        cards_in_related = page.locator(".related-column md-elevated-card").count()
        check("L1 no card shell in related column", cards_in_related == 0,
              cards_in_related)

        rel_bg = page.locator(".related-column").first.evaluate(
            "el => getComputedStyle(el).backgroundColor")
        check("L1b related column sits on page bg (transparent/surface)",
              rel_bg in ("rgba(0, 0, 0, 0)", "rgb(20, 18, 24)"), rel_bg)

        rel_title = page.locator(".related-column .note-panel-title").first.inner_text()
        check("L3 reading-mode header == 本片段已制卡片",
              rel_title == "本片段已制卡片", rel_title)

        # the empty state (no cards made yet) is fine — assert item styling
        # via a DOM probe the same way reading_cards_ui_test does
        probe = page.evaluate("""() => {
            const d = document.createElement('div');
            d.className = 'related-list';
            const i = document.createElement('div');
            i.className = 'similar-item related-item';
            d.appendChild(i);
            document.querySelector('.related-column .note-panel').appendChild(d);
            const bg = getComputedStyle(i).backgroundColor;
            d.remove();
            return bg;
        }""")
        check("L2 related-item bg == container-high #2b2930",
              probe == "rgb(43, 41, 48)", probe)

        check("no page errors", not errors, errors[:2])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
