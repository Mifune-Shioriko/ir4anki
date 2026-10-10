#!/usr/bin/env python3
"""Playwright UI verification of the SINGLE daily pipeline (user spec
2026-09-24; preview stage retired 2026-09-27).

One 开始 button must chain 阅读 → 复习 and land back on the start screen.
The preview stage no longer exists: pool cards made on an EARLIER Anki day
are auto-released by the backend at session entry (new cards for today's
deal); today's cards stay suspended in the pool until tomorrow.

SELF-CONTAINED: official pylib collection, seeded native review cards and
suspended preview cards; disposable uvicorn on an allocated loopback port,
isolated state/corpus/media/backups and explicitly unconfigured remote sync.

Covers:
  1. start screen: today's plan (阅读/复习 rows from the wire — NO 预览 row),
     pool row says 明日自动放行 with 今日新制 count
  2. auto-release at page load: the pool card made YESTERDAY left the pool
     into the dealable new queue; the card made TODAY stayed suspended
  3. 开始 → reading stage (whole-file segment) → skip drains the round and
     AUTO-CHAINS STRAIGHT into the review stage (no preview stage at all)
  4. review stage: reveal + 良好 drains the batch → AUTO-CHAINS back to the
     start screen (no stats pages anywhere)
  5. no JS page errors

Run: backend/.venv/bin/python scripts/modes_ui_test.py
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8902"
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


def api(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


# Historical note ids encode creation time; review scheduling is seeded through
# Collection before the server becomes its sole writer.
def _nid(days_ago, hour=12):
    dt = datetime.now() - timedelta(days=days_ago)
    return int(dt.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp() * 1000)


# ANKI-DAY-aware nids (fixture bug fix 2026-09-28): Anki's day rolls over at
# 4 AM, so between 00:00 and 04:00 wall-clock, "yesterday noon" is STILL the
# current Anki day — _nid(1) then fails the auto-release "created on an
# EARLIER Anki day" gate and the review batch loses its released new card
# (observed: 00:50 run dealt 1 card, step 3 timed out). Anchor the pool nids
# to the CURRENT ANKI DAY (now − 4h) instead of wall-clock now.
_anki_now = datetime.now() - timedelta(hours=4)


def _anki_nid(days_back, hour):
    dt = (_anki_now - timedelta(days=days_back)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


NID_REV = [_nid(40), _nid(41), _nid(42)]        # review cards (old notes)
NID_YESTERDAY = _anki_nid(1, 12)                 # pool card, earlier Anki day
NID_TODAY = _anki_nid(0, max(5, _anki_now.hour))  # pool card, current Anki day


NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经支配。

## 二、颈筋膜

分为浅、中、深三层，各层之间形成筋膜鞘，容纳血管神经。
"""


def wait_ready(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = api("/api/status")
            if st.get("anki") == "ok":
                return st
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("test backend never came up")


def native_card(cid):
    # Read-only inspection of the disposable database; the server remains sole writer.
    import sqlite3
    with sqlite3.connect('file:' + COLLECTION_PATH + '?mode=ro', uri=True) as db:
        did, queue = db.execute('select did,queue from cards where id=?', (cid,)).fetchone()
        # Modern Anki stores decks in its native decks table.
        row = db.execute('select name from decks where id=?', (did,)).fetchone()
        return dict(deck=row[0], susp=queue == -1)

def run():
    st = wait_ready()
    check("backend up (:8902) with native pylib ok", st.get("anki") == "ok", st)
    modes = st.get("study_modes") or {}
    check("wire: single daily tier", list(modes.keys()) == ["daily"], list(modes.keys()))
    d = modes.get("daily", {})
    # 2026-10-09 pacing: new is UNLIMITED (static 0) resolved on the wire to
    # the LIVE dealable pool — at /api/status time nothing has auto-released
    # yet, so 0; review = D/divisor with divisor 1 → the whole due pile (3).
    check("wire sizes new resolved (0 before auto-release), NO read/preview/make keys",
          d.get("new") == 0 and "read" not in d and "preview" not in d
          and "make" not in d, d)
    check("wire review = 3/1 = 3 (whole due pile, one round a day)",
          d.get("review") == 3, d.get("review"))

    # /api/status does NOT run auto_release (read-only endpoint) — the first
    # session/state call does. After it: yesterday's pool card (9101) has
    # moved to deck 2026 unsuspended; today's (9102) stays in the pool.
    s0 = api("/api/session/state")
    check("auto-release ran: pool = 1 (today's card only)",
          s0.get("preview_pool") == 1, s0.get("preview_pool"))
    check("pending_release = 1 (today's card)",
          s0.get("pending_release") == 1, s0.get("pending_release"))
    check("no preview_round on the wire (stage retired)",
          "preview_round" not in s0, list(s0.keys()))
    c_y = native_card(9101)
    check("yesterday's card: deck 2026 + unsuspended",
          c_y["deck"] == "2026" and not c_y["susp"], c_y)
    c_t = native_card(9102)
    check("today's card: still 预览池 + suspended",
          c_t["deck"] == "预览池" and c_t["susp"], c_t)
    check("new_total includes the auto-released card",
          (s0.get("new_total") or 0) >= 1, s0.get("new_total"))

    # seed the reading list (whole-file seeding → 1 segment)
    r = urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/reading/list/add", method="POST",
        data=json.dumps({"path": "2026/解剖/颈部.md"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30)
    add = json.load(r)
    check("seed: file listed, 1 whole-file segment",
          add.get("ok") is True and add.get("segments") == 1, add)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")

        print("== 1. unified start screen ==")
        page.wait_for_selector(".screen-title", timeout=30000)
        title = page.locator(".screen-title").first.inner_text()
        check("lands on 开始学习", "开始学习" in title, title)
        check("NO mode tiles (tiers retired)", page.locator(".mode-tile").count() == 0)
        check("NO 现在有多少时间 picker",
              page.locator(".modes-label").count() == 0)
        stats = page.locator(".screen-stats").all_inner_texts()
        joined = " ".join(stats)
        # List-driven reading (2026-10-06): the 阅读 plan row shows the
        # round size = one segment per listed file (1 file in this fixture).
        # The 2026-10-05 goal copy (已制 X / 15 张) is retired with the
        # whole card-making quota mechanism.
        check("plan: 阅读 row is list-driven (每篇最多两段 → 1 段, whole-file seed)",
              "阅读" in joined and "1 段" in joined, joined[:200])
        check("plan: NO 预览新卡 row (stage retired)", "预览新卡" not in joined, joined[:200])
        check("plan: 复习 1 新 + 3 到期 (whole due pile)",
              "1 新 + 3 到期" in joined, joined[:300])
        check("pool row: 预览池（明日自动放行）+ 今日新制",
              "明日自动放行" in joined and "今日新制 1" in joined, joined[:300])
        check("single 开始 button",
              page.locator("md-filled-button", has_text="开始").count() == 1)
        page.screenshot(path=str(Path(os.environ.get("TMPDIR", "/tmp")) / "daily-ui-start.png"))

        print("== 2. 开始 → reading stage → skip → AUTO-CHAIN STRAIGHT to review ==")
        page.locator("md-filled-button", has_text="开始").click()
        page.wait_for_selector(".reading-chunk-body", timeout=30000)
        body = page.locator(".reading-chunk-body").inner_text()
        check("reading: whole-file segment (both sections)",
              "浅层结构" in body and "颈筋膜" in body, body[:120])
        # 跳过 drains the 1-chunk round → chain fires. There is NO preview
        # stage anymore: the very next thing must be the review card.
        page.locator("md-text-button", has_text="无需制卡，跳过").click()
        page.wait_for_selector(".action-area", timeout=30000)
        check("chained into review (no 阅读完成 stats page)",
              page.locator(".screen-title", has_text="阅读完成").count() == 0)
        check("NO preview card rendered anywhere (先想一想 gone)",
              page.locator("md-filled-tonal-button:has-text('先想一想')").count() == 0)
        check("NO preview stats page",
              page.locator(".screen-title", has_text="本轮预览完成").count() == 0)
        page.screenshot(path=str(Path(os.environ.get("TMPDIR", "/tmp")) / "daily-ui-review.png"))

        print("== 3. review stage: 3 due reviews + 1 auto-released new → drain ==")
        # batch (2026-10-09 one-round pacing) = ALL 3 due review cards + the
        # whole dealable new pool (the released 9101) = 4
        for i in range(4):
            page.wait_for_selector(
                "md-filled-tonal-button:has-text('显示答案')", timeout=20000)
            page.locator("md-filled-tonal-button", has_text="显示答案").click()
            page.wait_for_timeout(400)
            page.locator("md-filled-button.ease-good").click()
            page.wait_for_timeout(800)
        # batch drained → chainAfterReview → start screen
        page.wait_for_selector(".screen-title", timeout=30000)
        page.wait_for_timeout(1200)
        title2 = page.locator(".screen-title").first.inner_text()
        check("back on 开始学习 (no stats page)", "开始学习" in title2, title2)
        check("no DoneScreen rendered",
              page.locator(".screen-title", has_text="本轮完成").count() == 0)
        # hero summary strip (P2, 2026-09-27 round 2): this round skipped 1
        # reading segment and reviewed 4 cards (1 new) — the start card must
        # show the completion summary, and the old shortcut-hint row must
        # stay gone (user request: 开始页不要 1234/Ctrl+Z 那行字)
        check("round-summary hero shown", page.locator(".round-summary").count() == 1)
        summ = page.locator(".round-summary").inner_text().replace("\n", " ")
        check("summary: 1 reading segment processed (skipped)",
              "阅读段处理" in summ and "跳过 1 段" in summ, summ)
        check("summary: 4 cards reviewed incl 1 new",
              "卡片复习" in summ and "含新卡 1 张" in summ, summ)
        check("no screen-keys hint row (user asked to remove it)",
              page.locator(".screen-keys").count() == 0)
        page.screenshot(path=str(Path(os.environ.get("TMPDIR", "/tmp")) / "daily-ui-back-to-start.png"))

        # H-divider (column borders) can't be asserted here — the side
        # column only renders with a card on screen (centerOccupied gate);
        # flat_panels_ui_test.py covers panel geometry at 1920.
        check("no JS page errors", not errors, errors[:3])
        browser.close()

    # backend state sanity
    s = api("/api/session/state")
    check("final state idle", s.get("state") != "active", s.get("state"))
    check("today's card STILL in the pool (releases tomorrow)",
          s.get("preview_pool") == 1, s.get("preview_pool"))
    check("ledger written: released=1 today",
          json.loads((Path(os.environ["ANKI_STATE_DIR"]) / "auto_release.json")
                     .read_text()).get("released") == 1)


from native_fixture import configure, tempdir, run_closed, NativeData, reserve_loopback_port, verify_server, cleanup_after

@cleanup_after
def main():
    state = tempdir(prefix="daily-ui-state-")
    os.environ["ANKI_STATE_DIR"] = state  # for the final ledger check
    notes = Path(tempdir(prefix="daily-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE_A, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_MEDIA_DIR": tempdir(prefix="daily-ui-media-"),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "1",

        "REVIEW_DIST_DIR": os.environ.get("REGRESSION_DIST", str(REPO_ROOT / "frontend" / "dist")),
    })
    global COLLECTION_PATH
    path = configure(env)
    COLLECTION_PATH = str(path)
    from anki.collection import Collection
    col = Collection(str(path))
    try:
        for cid, nid, deck, review in [(9001,NID_REV[0],'2026',True),(9002,NID_REV[1],'2026',True),(9003,NID_REV[2],'2026',True),(9101,NID_YESTERDAY,'预览池',False),(9102,NID_TODAY,'预览池',False)]:
            note = col.new_note(col.models.by_name('问答题'))
            note['正面'], note['背面'] = f'Q{cid}', f'A{cid}'
            col.add_note(note,col.decks.id(deck))
            card = note.cards()[0]
            col.db.execute('update notes set id=? where id=?',nid,note.id)
            col.db.execute('update cards set id=?,nid=? where id=?',cid,nid,card.id)
            card = col.get_card(cid)
            if review:
                card.type=2; card.queue=2; card.due=col.sched.today; card.ivl=1; card.reps=1; card.factor=2500
                col.update_card(card)
            else: col.sched.suspend_cards([cid])
    finally: col.close()
    global BASE
    server_port = reserve_loopback_port()
    BASE = f'http://127.0.0.1:{server_port}'
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", str(server_port)],
        env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
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


if __name__ == "__main__":
    sys.exit(main())
