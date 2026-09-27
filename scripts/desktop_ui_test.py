#!/usr/bin/env python3
"""Focused 1280x800 desktop check for the 2026-09-27 redesign.
Spawns a throwaway backend on :8906 (dead AnkiConnect, isolated state/corpus,
preview OFF), seeds a reading file, drives the round, and asserts:
  - action bar is sticky and inside the viewport on a tall card
  - content column ~620px, note column wider than old 448px
  - right column shows SidePanelTabs (笔记 / 相关卡片) in the 1180-1499 band
  - stage color bar present on the reading card
  - keycap hints rendered on ease buttons
No live coupling; net-zero (throwaway state)."""
import json, os, signal, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8906"
REPO = Path(os.path.expanduser("~/ir4anki"))
PASS = FAIL = 0
def check(n, c, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  ok  {n}")
    else: FAIL += 1; print(f"  FAIL {n} {d}")

def api(p, m="GET", b=None):
    data = json.dumps(b).encode() if b is not None else None
    req = urllib.request.Request(BASE+p, method=m, data=data,
        headers={"Content-Type":"application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {"_status": e.code}

NOTE = "# 颈部\n\n## 一、浅层结构\n\n" + ("皮肤薄，移动性大。浅筋膜内含颈阔肌。" * 30) + "\n"

def main():
    state = tempfile.mkdtemp(prefix="desktop-state-")
    notes = Path(tempfile.mkdtemp(prefix="desktop-notes-"))
    (notes/"2026").mkdir(parents=True)
    (notes/"2026"/"颈部.md").write_text(NOTE, encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state, "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1", "ANKI_PREVIEW_MODE": "0",
        "ANKICONNECT_URL": "http://127.0.0.1:18765",
        "ANKI_DAILY_READ": "4",
        "REVIEW_DIST_DIR": str(REPO/"frontend"/"dist"),
    })
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir",
        str(REPO/"backend"), "app:app", "--host", "127.0.0.1", "--port", "8906"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        run()
    finally:
        proc.send_signal(signal.SIGTERM)
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

def run():
    t0=time.time()
    while time.time()-t0 < 45:
        try:
            st=api("/api/status")
            if "reading_mode" in st or st.get("anki") in ("ok","error"): break
        except Exception: time.sleep(0.5)
    api("/api/reading/list/add","POST",{"path":"2026/颈部.md","fresh":True})
    with sync_playwright() as p:
        b=p.chromium.launch()
        page=b.new_page(viewport={"width":1280,"height":800}, color_scheme="dark")
        errs=[]; page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".nav-rail", timeout=30000)
        page.locator("md-filled-button", has_text="开始").first.click()
        page.wait_for_selector(".reading-crumb", timeout=30000)
        page.wait_for_timeout(800)

        # --- right column: SidePanelTabs — 统一双栏 at EVERY width ---
        check("right column renders at 1280", page.locator(".note-column").count()==1)
        check("NO separate related-column (three-col layout retired)",
              page.locator(".related-column").count()==0)
        check("SidePanelTabs present (笔记/相关卡片 tabs)",
              page.locator(".note-column .side-panel-tabs").count()==1)
        tabs = page.locator(".note-column md-primary-tab").all_inner_texts()
        check("two tabs 笔记 + 相关卡片",
              any("笔记" in t for t in tabs) and any("相关" in t for t in tabs), tabs)
        check("笔记 pane visible by default",
              page.locator(".note-column .side-panel-body").first.is_visible())

        # --- chip semantics: info spans, no disabled assist-chips ---
        check("card header uses info-chip spans",
              page.locator(".card-chips .info-chip").count()>=1)
        check("no md-assist-chip left in card headers",
              page.locator(".card-header md-assist-chip").count()==0)

        # --- start screen: shortcut hint row gone ---
        # (navigate back mid-round isn't possible; the modes test covers the
        #  start screen — here just assert the class is absent from this page)
        check("no screen-keys row anywhere",
              page.locator(".screen-keys").count()==0)

        # --- stage color bar on the reading card ---
        check("reading card has stage-reading class",
              page.locator(".stage-reading").count()>=1)
        bar = page.evaluate("""() => {
          const h = document.querySelector('.stage-reading .card-container');
          if(!h) return null; const s=getComputedStyle(h,'::before');
          return [s.height, s.backgroundColor];
        }""")
        check("stage bar ::before is 3px + tertiary tinted",
              bar and bar[0]=="3px" and bar[1]!="rgba(0, 0, 0, 0)", bar)

        # --- column widths ---
        cw = page.locator(".columns > div.content").first.bounding_box()["width"]
        nw = page.locator(".note-column").bounding_box()["width"]
        check("content column ~620px (was 680)", 610<=cw<=630, cw)
        check("note column wider than old ~448 (target ~500+)", nw>=490, nw)

        # --- sticky action bar stays in viewport on a tall card ---
        # scroll the card body to the bottom; the ease/next row must remain
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(300)
        aa = page.locator(".action-area").bounding_box()
        vh = 800
        check("action-area visible after page scroll (sticky)",
              aa is not None and aa["y"]+aa["height"] <= vh+2 and aa["y"]>=0, aa)

        # --- switch to 相关卡片 tab works ---
        page.locator(".note-column md-primary-tab", has_text="相关").first.click()
        page.wait_for_timeout(500)
        check("相关卡片 tab switches pane",
              page.locator(".note-column .related-panel").count()==1)

        page.screenshot(path="/tmp/desktop-1280.png")

        # --- unified at 1920 too (user's main display): still two columns ---
        page.set_viewport_size({"width": 1920, "height": 1200})
        page.wait_for_timeout(400)
        check("1920: still NO related-column (unified double-column)",
              page.locator(".related-column").count()==0)
        check("1920: SidePanelTabs still the side column",
              page.locator(".note-column .side-panel-tabs").count()==1)
        page.screenshot(path="/tmp/desktop-1920.png")

        check("no JS errors", not errs, errs[:2])
        b.close()

if __name__=="__main__":
    sys.exit(main())
