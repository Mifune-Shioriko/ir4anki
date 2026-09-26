#!/usr/bin/env python3
"""Playwright UI verification of the SINGLE daily pipeline (user spec
2026-09-24; preview stage retired 2026-09-27).

One 开始 button must chain 阅读 → 复习 and land back on the start screen.
The preview stage no longer exists: pool cards made on an EARLIER Anki day
are auto-released by the backend at session entry (new cards for today's
deal); today's cards stay suspended in the pool until tomorrow.

SELF-CONTAINED: fake AnkiConnect (stdlib http.server) on :18767 + throwaway
uvicorn on :8902 with isolated state/corpus. The live app (:8901), the live
state dir and the real collection are untouched. Net-zero on the fake.

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


# ---- fake AnkiConnect -------------------------------------------------------
# Minimal but coherent: 3 due review cards + 2 pool (suspended new) cards.
# NOTE IDs are REAL ms timestamps (today / yesterday) — the backend's
# auto_release_pool derives the creation day from the noteId, so the fake
# must use plausible ids or the timestamp gate is untestable.
# findCards honors the exact queries the backend issues; answerCards removes
# the card from the due set.

def _nid(days_ago, hour=12):
    dt = datetime.now() - timedelta(days=days_ago)
    return int(dt.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp() * 1000)


NID_REV = [_nid(40), _nid(41), _nid(42)]        # review cards (old notes)
NID_YESTERDAY = _nid(1)                          # pool card made yesterday
NID_TODAY = _nid(0, hour=max(5, datetime.now().hour))  # pool card made today (post-4AM)


class FakeState:
    def __init__(self):
        # review cards: type=1 (review), queue=2 (review), due=100 → is:due
        self.cards = {
            9001: {"type": 1, "queue": 2, "due": 100, "deck": "2026", "susp": False, "nid": NID_REV[0]},
            9002: {"type": 1, "queue": 2, "due": 101, "deck": "2026", "susp": False, "nid": NID_REV[1]},
            9003: {"type": 1, "queue": 2, "due": 102, "deck": "2026", "susp": False, "nid": NID_REV[2]},
            # preview pool: new + suspended — one from yesterday (releases at
            # the first entry point), one from today (stays until tomorrow)
            9101: {"type": 0, "queue": -1, "due": 0, "deck": "预览池", "susp": True, "nid": NID_YESTERDAY},
            9102: {"type": 0, "queue": -1, "due": 0, "deck": "预览池", "susp": True, "nid": NID_TODAY},
        }
        self.notes = {nid: {"tags": [], "modelName": "问答题"} for nid in
                      [*NID_REV, NID_YESTERDAY, NID_TODAY]}


fake = FakeState()


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _match(self, cid, c, q):
        # evaluate ALL query tokens (no early returns — the backend composes
        # deck + is:new + is:suspended + tag: filters in one query)
        if 'deck:"预览池"' in q and c["deck"] != "预览池":
            return False
        if 'deck:"2026"' in q and c["deck"] != "2026":
            return False
        if "is:new" in q and "-is:new" not in q and c["type"] != 0:
            return False
        if "-is:new" in q and c["type"] == 0:
            return False
        if "is:suspended" in q and "-is:suspended" not in q and not c["susp"]:
            return False
        if "-is:suspended" in q and c["susp"]:
            return False
        if "is:due" in q:
            if not (c["type"] != 0 and c["queue"] == 2 and c["due"] <= 100000):
                return False
        for tok in q.split():
            if tok.startswith("tag:"):
                want = tok[4:]
                tags = fake.notes[c["nid"]]["tags"]
                if want.endswith("*"):
                    if not any(t.startswith(want[:-1]) for t in tags):
                        return False
                elif want not in tags:
                    return False
        if q.startswith("cid:"):
            return cid == int(q[4:])
        # bare "is:new -is:suspended" / composed queries fall through here
        return True

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        action = req.get("action")
        p = req.get("params") or {}
        result = None
        if action == "version":
            result = 6
        elif action == "findCards":
            q = p.get("query", "")
            result = [cid for cid, c in fake.cards.items() if self._match(cid, c, q)]
        elif action == "cardsInfo":
            out = []
            for cid in p.get("cards", []):
                c = fake.cards.get(cid)
                if not c:
                    out.append({})
                    continue
                out.append({
                    "cardId": cid, "noteId": c["nid"], "type": c["type"],
                    "queue": c["queue"], "due": c["due"], "deckName": c["deck"],
                    "modelName": "问答题", "question": f"<p>Q{cid}</p>",
                    "answer": f"<p>A{cid}</p>", "css": "",
                    "interval": 1, "factor": 2500, "reps": 1, "lapses": 0,
                    "left": 0, "isNew": c["type"] == 0,
                    # real AnkiConnect: `note` is the note ID (int)
                    "note": c["nid"],
                })
            result = out
        elif action == "answerCards":
            for a in p.get("answers", []):
                cid = a.get("cardId")
                if cid in fake.cards:
                    # push the card out of the due set (learning → far future)
                    fake.cards[cid]["due"] = 200000
            result = [True] * len(p.get("answers", []))
        elif action == "changeDeck":
            for cid in p.get("cards", []):
                if cid in fake.cards:
                    fake.cards[cid]["deck"] = p["deck"]
            result = None
        elif action == "suspend":
            for cid in p.get("cards", []):
                if cid in fake.cards:
                    fake.cards[cid]["susp"] = True
                    fake.cards[cid]["queue"] = -1
            result = True
        elif action == "unsuspend":
            for cid in p.get("cards", []):
                if cid in fake.cards:
                    fake.cards[cid]["susp"] = False
                    fake.cards[cid]["queue"] = 0 if fake.cards[cid]["type"] == 0 else 2
            result = True
        elif action in ("addTags", "removeTags"):
            for nid in p.get("notes", []):
                tags = fake.notes.setdefault(nid, {"tags": [], "modelName": "问答题"})["tags"]
                for t in (p.get("tags") or "").split():
                    if action == "addTags":
                        if t not in tags:
                            tags.append(t)
                    elif t in tags:
                        tags.remove(t)
            result = True
        elif action == "setSpecificValueOfCard":
            result = [True]
        elif action == "notesInfo":
            result = [
                {"noteId": nid, "tags": list(fake.notes.get(nid, {}).get("tags", [])),
                 "fields": {"正面": {"value": "x", "order": 0},
                            "背面": {"value": "y", "order": 1}},
                 "modelName": "问答题"}
                for nid in p.get("notes", [])
            ]
        elif action == "sync":
            result = None
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"result": result, "error": None}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


def run():
    st = wait_ready()
    check("backend up (:8902) with fake Anki ok", st.get("anki") == "ok", st)
    modes = st.get("study_modes") or {}
    check("wire: single daily tier", list(modes.keys()) == ["daily"], list(modes.keys()))
    d = modes.get("daily", {})
    check("wire sizes read=4 new=15, NO preview key",
          (d.get("read"), d.get("new")) == (4, 15) and "preview" not in d, d)
    check("wire review = ceil(3/3) = 1", d.get("review") == 1, d.get("review"))

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
    c_y = fake.cards[9101]
    check("yesterday's card: deck 2026 + unsuspended",
          c_y["deck"] == "2026" and not c_y["susp"], c_y)
    c_t = fake.cards[9102]
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
        check("plan: 阅读 4 段", "阅读" in joined and "4 段" in joined, joined[:200])
        check("plan: NO 预览新卡 row (stage retired)", "预览新卡" not in joined, joined[:200])
        check("plan: 复习 15 新 + 1 到期", "15 新 + 1 到期" in joined, joined[:300])
        check("pool row: 预览池（明日自动放行）+ 今日新制",
              "明日自动放行" in joined and "今日新制 1" in joined, joined[:300])
        check("single 开始 button",
              page.locator("md-filled-button", has_text="开始").count() == 1)
        page.screenshot(path="/tmp/daily-ui-start.png")

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
        page.screenshot(path="/tmp/daily-ui-review.png")

        print("== 3. review stage: 1 due review + 1 auto-released new → drain ==")
        # batch = 1 review card (9001) + the released new card (9101) = 2
        for i in range(2):
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
        page.screenshot(path="/tmp/daily-ui-back-to-start.png")

        # H-divider (left column borders) can't be asserted here — the side
        # columns only render with a card on screen at ≥1500px viewport;
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


def main():
    state = tempfile.mkdtemp(prefix="daily-ui-state-")
    os.environ["ANKI_STATE_DIR"] = state  # for the final ledger check
    notes = Path(tempfile.mkdtemp(prefix="daily-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE_A, encoding="utf-8")

    srv = ThreadingHTTPServer(("127.0.0.1", 18767), FakeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_MEDIA_DIR": tempfile.mkdtemp(prefix="daily-ui-media-"),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "1",
        "ANKICONNECT_URL": "http://127.0.0.1:18767",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8902"],
        env=env, stdout=open("/tmp/daily_ui_backend.log", "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        run()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv.shutdown()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
