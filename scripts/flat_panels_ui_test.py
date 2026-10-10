#!/usr/bin/env python3
"""Verify the flat side-panel redesign (user spec 2026-09-27, updated for
the 2026-09-29 bounded-sidebar + no-header-row pass).

Spawns a throwaway uvicorn on an allocated loopback port (isolated ANKI_STATE_DIR + temp corpus,
isolated native engine) serving the REAL frontend/dist build, then drives it with
Playwright at 1920x1200 dark mode (the user's display) and asserts:

  right column (笔记 + 相关卡片 tabs, 统一双栏 2026-09-27 round 2):
    1. no md-elevated-card shell inside .note-column
    2. .note-column bg == surface-container-low (dark #1d1b20)
       != page bg (dark surface #141218)
    3. border-left AND border-right == 1px solid outline-variant (#49454f)
    4. tint stays BETWEEN the two dividers — the pixel just outside the
       right divider is page bg, and no ::after full-bleed patch exists
    5. full viewport height (100vh)
    6. no .note-panel-header / .note-panel-title row anywhere (deleted
       2026-09-29 — the tab label is the only title)
    7. two tabs; the 相关卡片 tab mounts the RelatedPanel (no separate
       .related-column — the three-column layout is retired)
    8. .related-list .similar-item bg == surface-container-high (#2b2930)

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


from native_fixture import configure, tempdir, run_closed, NativeData, reserve_loopback_port, verify_server, cleanup_after

@cleanup_after
def main():
    state = tempdir(prefix="flat-ui-state-")
    notes = Path(tempdir(prefix="flat-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE_A, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",

        "ANKI_DAILY_READ": "4",
        "REVIEW_DIST_DIR": os.environ.get("REGRESSION_DIST", str(REPO_ROOT / "frontend" / "dist")),
    })
    configure(env)
    global BASE
    server_port = reserve_loopback_port()
    BASE = f'http://127.0.0.1:{server_port}'
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", str(server_port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        verify_server(proc, BASE)
        run()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def run():
    st = wait_ready()
    check("backend up on an allocated loopback port", st.get("anki") in ("ok", "error"), st)

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
        # 统一双栏 (2026-09-27 round 2): the note column with its
        # 笔记/相关卡片 tabs is the ONLY side column — at EVERY width
        # (the 1500px three-column path is retired)
        page.wait_for_selector(".note-column .side-panel-tabs", timeout=10000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(Path(os.environ.get("TMPDIR", "/tmp")) / "flat-ui-wide.png"))
        check("L0 no separate related column (three-col retired)",
              page.locator(".related-column").count() == 0)

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
                          " return [s.borderLeftWidth, s.borderLeftStyle, s.borderLeftColor,"
                          " s.borderRightWidth, s.borderRightStyle, s.borderRightColor]; }")
        check("R3 border-left 1px solid outline-variant #49454f",
              bl[0] == "1px" and bl[1] == "solid" and bl[2] == "rgb(73, 69, 79)", bl)
        check("R3b border-right 1px solid outline-variant #49454f (2026-09-29)",
              bl[3] == "1px" and bl[4] == "solid" and bl[5] == "rgb(73, 69, 79)", bl)

        col_box = col.bounding_box()
        # 2026-09-29 bounded sidebar: the tint must STOP at the column's
        # right divider — the old full-bleed (negative margin + ::after
        # patch reaching the viewport edge) is gone.
        right_edge = col_box["x"] + col_box["width"]
        outside_bg = page.evaluate("""(x) => {
            const el = document.elementFromPoint(x, 600);
            let bg = 'none', node = el;
            while (node && node !== document.documentElement) {
                const c = getComputedStyle(node).backgroundColor;
                if (c && c !== 'rgba(0, 0, 0, 0)') { bg = c; break; }
                node = node.parentElement;
            }
            return bg;
        }""", min(right_edge + 6, 1918))
        check("R4 pixel just outside the right divider is page bg #141218",
              outside_bg == "rgb(20, 18, 24)",
              f"right edge at {right_edge}, outside bg {outside_bg}")
        check("R4b column does NOT bleed to the viewport right edge",
              right_edge < 1920 - 4, right_edge)
        after_content = page.evaluate("""() => {
            const col = document.querySelector('.note-column');
            return getComputedStyle(col, '::after').content;
        }""")
        check("R4c no ::after full-bleed patch (content: none)",
              after_content == "none", after_content)
        inside_bg = page.evaluate("""(x) => {
            const el = document.elementFromPoint(x, 600);
            let bg = 'none', node = el;
            while (node && node !== document.documentElement) {
                const c = getComputedStyle(node).backgroundColor;
                if (c && c !== 'rgba(0, 0, 0, 0)') { bg = c; break; }
                node = node.parentElement;
            }
            return bg;
        }""", right_edge - 6)
        check("R4d pixel just inside the right divider shows the tint",
              inside_bg == "rgb(29, 27, 32)", inside_bg)

        check("R5 note column is full viewport height",
              abs(col_box["height"] - 1200) <= 2, col_box["height"])
        check("R5b column starts at viewport top", col_box["y"] == 0, col_box["y"])

        title_rows = page.locator(".note-column .note-panel-header").count()
        check("R6 header title row deleted (2026-09-29)", title_rows == 0,
              title_rows)
        check("R6b no .note-panel-title anywhere",
              page.locator(".note-panel-title").count() == 0)
        crumb = page.locator(".note-column .note-crumb").first
        check("R6c breadcrumb is the first row under the tabs",
              crumb.count() == 1 and crumb.is_visible())

        # ---- 相关卡片 tab: same flat treatment inside the note column ----
        tabs = page.locator(".note-column md-primary-tab")
        check("R7 side column has 笔记/相关卡片 tabs", tabs.count() == 2,
              tabs.count())
        tabs.nth(1).click()  # 相关卡片
        page.wait_for_timeout(400)
        rel_panel = page.locator(".note-column .related-panel")
        check("L1 related panel mounted in note column",
              rel_panel.count() == 1)
        cards_in_related = page.locator(".note-column .related-panel md-elevated-card").count()
        check("L1b no card shell around related panel items",
              cards_in_related == 0, cards_in_related)

        check("L3 related panel has no header row either",
              page.locator(".note-column .related-panel .note-panel-header").count() == 0)

        # the empty state (no cards made yet) is fine — assert item styling
        # via a DOM probe the same way reading_cards_ui_test does
        probe = page.evaluate("""() => {
            const d = document.createElement('div');
            d.className = 'related-list';
            const i = document.createElement('div');
            i.className = 'similar-item related-item';
            d.appendChild(i);
            document.querySelector('.note-column .related-panel').appendChild(d);
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
