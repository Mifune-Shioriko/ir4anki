#!/usr/bin/env python3
"""Playwright UI verification of the two-tier pacing mode selector.

Targets the THROWAWAY backend on :8902 (isolated state dir), never the live
:8901. Read-only w.r.t. the collection: we only load screens and click the
mode tiles + navigate; any started round/preview is finished via the API in
teardown.
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8902"
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


def api(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = api("/api/status")
            if st.get("anki") in ("ok", "error"):
                return st
        except Exception:
            time.sleep(1)
    raise RuntimeError("test backend never came up")


def main():
    st = wait_ready()
    check("test backend up (:8902)", st.get("anki") == "ok", st)
    check("wire has study_modes", "study_modes" in st)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE, wait_until="networkidle")
        # initial resync may take a moment (session/state does a sync-less read)
        page.wait_for_selector(".mode-tile", timeout=30000)

        tiles = page.locator(".mode-tile")
        check("two mode tiles rendered", tiles.count() == 2, tiles.count())

        # --- copy checks (numbers must come from the wire, never hardcoded)
        quick_tile = tiles.nth(0)
        focus_tile = tiles.nth(1)
        check("tile 1 = 快速", "快速" in quick_tile.inner_text())
        check("tile 2 = 专注", "专注" in focus_tile.inner_text())
        qt = quick_tile.inner_text()
        ft = focus_tile.inner_text()
        check("quick shows 5/5/20", all(x in qt for x in ["预览 5", "新卡 5", "复习 20"]), qt)
        check("focus shows 10/10/30", all(x in ft for x in ["预览 10", "新卡 10", "复习 30"]), ft)
        check("quick time estimate present", "分钟" in qt, qt)
        check("focus time estimate present", "分钟" in ft, ft)

        # --- selection state + persistence
        check("quick selected by default", "mode-tile--selected" in (quick_tile.get_attribute("class") or ""))
        focus_tile.click()
        page.wait_for_timeout(200)
        check("focus selected after click", "mode-tile--selected" in (focus_tile.get_attribute("class") or ""))
        check("quick deselected", "mode-tile--selected" not in (quick_tile.get_attribute("class") or ""))
        stored = page.evaluate("localStorage.getItem('anki-review-app.study-mode')")
        check("localStorage persists choice", stored == "focus", stored)

        page.screenshot(path="/tmp/modes-ui-start.png")

        # --- reload: pre-selection survives
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".mode-tile", timeout=30000)
        # may land on previewStart (pool>0) or start — either shows tiles
        focus_after = page.locator(".mode-tile--selected").inner_text()
        check("selection survives reload", "专注" in focus_after, focus_after)

        # --- which screen are we on?
        title = page.locator(".screen-title").inner_text()
        print(f"  (landed on: {title})")

        if "预览" in title:
            # previewStart screen also carries the selector
            check("preview start screen has mode selector",
                  page.locator(".mode-tile").count() == 2)
            # daily 放行 progress bar (2026-09-16): between the explainer
            # copy and the mode selector, goal 40 from the wire
            bar = page.locator(".release-progress")
            check("release progress bar rendered", bar.count() == 1, bar.count())
            if bar.count() == 1:
                cnt = page.locator(".release-progress__count").inner_text()
                check("release goal = 40 on wire text", "/ 40 张" in cnt, cnt)
                check("release bar has md-linear-progress",
                      page.locator(".release-progress md-linear-progress").count() == 1)
            # start a focus preview round → up to 10 cards
            page.locator("md-filled-button").first.click()
            page.wait_for_selector(".preview-card, .pv-card, md-elevated-card", timeout=120000)
            page.wait_for_timeout(1500)
            s = api("/api/session/state")
            pr = s.get("preview_round") or {}
            check("preview round active with mode=focus", pr.get("mode") == "focus", pr)
            check("preview round total <= 10", (pr.get("total") or 0) <= 10, pr.get("total"))
            check("wire preview_per_round=10", s.get("preview_per_round") == 10, s.get("preview_per_round"))
            page.screenshot(path="/tmp/modes-ui-preview.png")
            # teardown: finish WITHOUT acting (net-zero)
            api("/api/preview/finish", "POST")
        else:
            # start screen: begin a focus review round
            page.locator("md-filled-button").first.click()
            page.wait_for_timeout(3000)
            s = api("/api/session/state")
            check("review round active with mode=focus", s.get("mode") == "focus", s.get("mode"))
            check("focus batch > quick max (25)", (s.get("total") or 0) > 25 or (st.get("due_review", 0) + st.get("new_total", 0)) <= 25, s.get("total"))
            page.screenshot(path="/tmp/modes-ui-review.png")
            api("/api/session/finish", "POST")

        # --- switch back to quick and start: small batch
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".mode-tile", timeout=30000)
        page.locator(".mode-tile").nth(0).click()  # 快速
        page.wait_for_timeout(150)
        t2 = page.locator(".screen-title").inner_text()
        if "预览" not in t2:
            page.locator("md-filled-button").first.click()
            page.wait_for_timeout(2500)
            s2 = api("/api/session/state")
            if s2.get("state") == "active":
                check("quick round mode", s2.get("mode") == "quick", s2.get("mode"))
                check("quick batch <= 25", (s2.get("total") or 0) <= 25, s2.get("total"))
                api("/api/session/finish", "POST")
        else:
            page.locator("md-filled-button").first.click()
            page.wait_for_timeout(2500)
            s2 = api("/api/session/state")
            pr2 = s2.get("preview_round") or {}
            if pr2:
                check("quick preview total <= 5", (pr2.get("total") or 0) <= 5, pr2.get("total"))
                check("quick preview mode", pr2.get("mode") == "quick", pr2.get("mode"))
            api("/api/preview/finish", "POST")

        # final net-zero: nothing active on either state file
        s3 = api("/api/session/state")
        check("final: no active review round", s3.get("state") != "active", s3.get("state"))
        check("final: no active preview round", s3.get("preview_round") is None)

        browser.close()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
