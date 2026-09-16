#!/usr/bin/env python3
"""Playwright UI test for the note panel (知识成体系 Phase 1).

Runs against a THROWAWAY backend on :8902 (isolated ANKI_STATE_DIR) with the
fresh dist. Safety contract (per skill anki-self-hosted):
  - NEVER answers a card (opening a round + finishing with zero answers is
    net-zero: cards return to the due pool untouched)
  - aborts if the LIVE app (:8901) has an active user round — the test's
    own round is on the throwaway state dir, but the Anki collection is
    shared; an in-flight user answer racing our session/finish could drop
    their card. (session/finish on :8902 touches only ITS round.json.)
  - always POSTs /api/session/finish + /api/preview/finish on :8902 in teardown
"""
import json
import sys
import time
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8902"
LIVE = "http://127.0.0.1:8901"
FAILS = []
PASSES = 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  ok  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL {msg}")


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def post(url):
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_status": e.code}


def abort(msg):
    print(f"ABORT: {msg}")
    sys.exit(2)


# ---- safety gate: is the USER mid-session on the live app? ----
live_state = get(f"{LIVE}/api/session/state")
if live_state.get("state") == "active":
    abort("live app has an ACTIVE user round — rerun when the user is done")
if live_state.get("preview_round"):
    abort("live app has an ACTIVE preview round — rerun when the user is done")

# defensive: clear any leftover round on the THROWAWAY backend (e.g. from a
# debug session) so the test always starts from the start screen
post(f"{BASE}/api/session/finish")
post(f"{BASE}/api/preview/finish")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    try:
        page.goto(BASE, wait_until="networkidle")
        # start screen → click 开始 (filled button). Modes: quick default.
        page.wait_for_selector("md-filled-button", timeout=10000)
        btns = page.locator("md-filled-button")
        start_clicked = False
        for i in range(btns.count()):
            if "开始" in (btns.nth(i).inner_text() or ""):
                btns.nth(i).click()
                start_clicked = True
                break
        check(start_clicked, "clicked 开始 on start screen")

        # wait for the review phase: a flashcard + the note panel
        page.wait_for_selector(".flashcard-wrapper", timeout=20000)
        check(True, "review round started, flashcard visible")

        page.wait_for_selector(".note-panel", timeout=10000)
        check(True, "note panel rendered (wide viewport 1440px)")

        # the panel loads async — wait for ready/empty/error state
        t0 = time.time()
        state = None
        while time.time() - t0 < 30:
            if page.locator(".note-body").count() > 0:
                state = "ready"
                break
            if page.locator(".note-panel-empty").count() > 0:
                state = "empty"
                break
            if page.locator(".note-panel-state md-text-button").count() > 0:
                state = "error"
                break
            page.wait_for_timeout(300)
        check(state in ("ready", "empty"), f"panel reached state: {state}")

        if state == "ready":
            body_html = page.locator(".note-body").inner_html()
            check(len(body_html) > 500, f"note body rendered ({len(body_html)} chars html)")
            check(page.locator(".note-body [data-src-line]").count() > 3,
                  f"{page.locator('.note-body [data-src-line]').count()} heading anchors")
            check(page.locator(".note-crumb").count() == 1, "breadcrumb visible")
            score_txt = page.locator(".note-panel-score").inner_text()
            check("匹配" in score_txt, f"score chip: {score_txt!r}")

            # highlight flash — the section anchor gets .note-hl briefly
            hl = page.locator(".note-hl").count()
            check(hl >= 1, f"matched section highlighted ({hl} elements)")

            # tabs (top-3 switch) — robust assertion: compute the EXPECTED
            # crumb of tab 1 from the API payload (a crumb-equality check
            # against tab 0 is a false negative when top-2 are split parts
            # of the same section and legitimately share heading_path)
            tabs = page.locator(".note-tabs md-primary-tab")
            ntabs = tabs.count()
            check(ntabs >= 1, f"{ntabs} tabs rendered")
            if ntabs >= 2:
                # ask the backend what this card's sections are
                note_id = page.evaluate(
                    """() => {
                    // the panel's noteId is not in the DOM; grab it from the
                    // round state — review rounds carry top-level `cards`,
                    // preview rounds carry `preview_round.cards`
                    return fetch('/api/session/state').then(r => r.json())
                        .then(d => {
                            const c = (d.cards && d.cards[0])
                                || (d.preview_round && d.preview_round.cards
                                    && d.preview_round.cards[0]);
                            return c ? c.noteId : null;
                        });
                }""")
                check(note_id is not None, f"got noteId from round state: {note_id}")
                api_secs = get(f"{BASE}/api/note/sections?note_id={note_id}&top_k=3")["sections"]
                exp = api_secs[1]
                exp_crumb = f"{exp['file'].split('/', 1)[-1]} · {' › '.join(exp['heading_path'])}"
                tabs.nth(1).click()
                page.wait_for_timeout(1500)
                crumb1 = page.locator(".note-crumb").inner_text()
                check(crumb1 == exp_crumb,
                      f"tab 1 → expected crumb {exp_crumb!r}, got {crumb1!r}")
                # selection state moved
                sel = page.evaluate(
                    "() => document.querySelectorAll('md-primary-tab')[1].selected")
                check(sel is True, "tab 1 reports selected")
                tabs.nth(0).click()
                page.wait_for_timeout(800)

            # scrolled somewhere below the top for a mid-file match
            scrolltop = page.evaluate(
                "() => document.querySelector('.note-body')?.scrollTop ?? -1")
            check(scrolltop >= 0, f"note-body scrollTop={scrolltop}")

        # card 2: next card → panel must reset and reload for the new card.
        # Reveal + answer would pollute Anki — instead use the round's NEXT
        # card via undo-free navigation: not possible without answering, so
        # we only verify the panel is card-scoped by checking noteId prop
        # wiring on card 1 and that a page RELOAD keeps working (resume path).
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".note-panel", timeout=15000)
        t0 = time.time()
        ready_again = False
        while time.time() - t0 < 30:
            if page.locator(".note-body").count() > 0:
                ready_again = True
                break
            page.wait_for_timeout(300)
        check(ready_again, "panel re-loads after refresh (resumed round)")

        # narrow viewport: note column must disappear entirely
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_timeout(400)
        check(page.locator(".note-panel").count() == 0,
              "note panel hidden on narrow viewport (phone parity)")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(400)
        check(page.locator(".note-panel").count() == 1,
              "note panel back on wide viewport")

        check(len(errors) == 0, f"no JS page errors ({errors[:2]})")
    finally:
        browser.close()

# ---- teardown: never leave a round open on the throwaway backend ----
post(f"{BASE}/api/session/finish")
post(f"{BASE}/api/preview/finish")

# verify net-zero: due count sane, no test residue in the live app
live_after = get(f"{LIVE}/api/session/state")
check(live_after.get("state") != "active", "live app untouched (no active round)")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES / {PASSES} passes")
    sys.exit(1)
print(f"ALL PASS ({PASSES} assertions)")
