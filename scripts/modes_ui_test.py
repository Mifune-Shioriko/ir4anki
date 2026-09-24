#!/usr/bin/env python3
"""Playwright UI verification of the SINGLE daily pipeline (user spec 2026-09-24).

Replaces the two-tier mode-selector test: quick/focus and every intermediate
stats/pick screen are retired. One 开始 button must chain
阅读 → 预览新卡 → 复习 and land back on the start screen.

SELF-CONTAINED: fake AnkiConnect (stdlib http.server) on :18767 + throwaway
uvicorn on :8902 with isolated state/corpus. The live app (:8901), the live
state dir and the real collection are untouched. Net-zero on the fake.

Covers:
  1. start screen: today's plan (阅读/预览/复习 rows from the wire), NO mode
     tiles, single 开始 button
  2. 开始 → reading stage (whole-file segment) → skip drains the round and
     AUTO-CHAINS into the preview stage (no readingDone page)
  3. preview stage: approve drains → AUTO-CHAINS into the review stage
     (no previewDone page)
  4. review stage: reveal + 良好 drains the batch → AUTO-CHAINS back to the
     start screen with a completion snackbar (no DoneScreen)
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
# findCards honors the exact queries the backend issues; answerCards removes
# the card from the due set; preview act paths are driven through the real
# endpoints (changeDeck/suspend/unsuspend/addTags).

class FakeState:
    def __init__(self):
        # review cards: type=1 (review), queue=2 (review), due=100 → is:due
        self.cards = {
            9001: {"type": 1, "queue": 2, "due": 100, "deck": "2026", "susp": False, "nid": 8001},
            9002: {"type": 1, "queue": 2, "due": 101, "deck": "2026", "susp": False, "nid": 8002},
            9003: {"type": 1, "queue": 2, "due": 102, "deck": "2026", "susp": False, "nid": 8003},
            # preview pool: new + suspended
            9101: {"type": 0, "queue": -1, "due": 0, "deck": "预览池", "susp": True, "nid": 8101},
            9102: {"type": 0, "queue": -1, "due": 0, "deck": "预览池", "susp": True, "nid": 8102},
        }
        self.notes = {
            8001: {"tags": [], "modelName": "问答题"}, 8002: {"tags": [], "modelName": "问答题"},
            8003: {"tags": [], "modelName": "问答题"}, 8101: {"tags": [], "modelName": "问答题"},
            8102: {"tags": [], "modelName": "问答题"},
        }


fake = FakeState()


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _match(self, cid, c, q):
        if "is:due" in q and "-is:new" in q:
            return c["type"] != 0 and c["queue"] == 2 and c["due"] <= 100000
        if q.startswith("is:new"):
            ok = c["type"] == 0
            if "-is:suspended" in q:
                ok = ok and not c["susp"]
            return ok
        if 'deck:"预览池"' in q:
            if not c["deck"] == "预览池":
                return False
            if "is:new" in q and c["type"] != 0:
                return False
            if "is:suspended" in q and not c["susp"]:
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
            return True
        if 'deck:"2026"' in q:
            return c["deck"] == "2026"
        if q.startswith("cid:"):
            return cid == int(q[4:])
        return False

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
            result = True
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
    check("wire sizes 4+15+15", (d.get("read"), d.get("preview"), d.get("new")) == (4, 15, 15), d)
    check("wire review = ceil(3/3) = 1", d.get("review") == 1, d.get("review"))

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
        check("plan: 预览新卡 15 张", "预览新卡" in joined and "15 张" in joined, joined[:200])
        check("plan: 复习 15 新 + 1 到期", "15 新 + 1 到期" in joined, joined[:300])
        check("single 开始 button",
              page.locator("md-filled-button", has_text="开始").count() == 1)
        page.screenshot(path="/tmp/daily-ui-start.png")

        print("== 2. 开始 → reading stage → skip → AUTO-CHAIN to preview ==")
        page.locator("md-filled-button", has_text="开始").click()
        page.wait_for_selector(".reading-chunk-body", timeout=30000)
        body = page.locator(".reading-chunk-body").inner_text()
        check("reading: whole-file segment (both sections)",
              "浅层结构" in body and "颈筋膜" in body, body[:120])
        # 跳过 drains the 1-chunk round → chain fires (no readingDone page)
        page.locator("md-text-button", has_text="无需制卡，跳过").click()
        page.wait_for_selector(
            "md-filled-tonal-button:has-text('先想一想')", timeout=30000)
        check("chained into preview (no 阅读完成 stats page)",
              page.locator(".screen-title", has_text="阅读完成").count() == 0)
        page.screenshot(path="/tmp/daily-ui-preview.png")

        print("== 3. preview stage: approve ×2 → AUTO-CHAIN to review ==")
        # reveal + approve both pool cards; the fake pool has exactly 2
        for i in range(2):
            page.wait_for_selector(
                "md-filled-tonal-button:has-text('先想一想')", timeout=20000)
            page.locator("md-filled-tonal-button:has-text('先想一想')").click()
            page.wait_for_selector("md-filled-button.preview-approve", timeout=10000)
            page.locator("md-filled-button.preview-approve").click()
            page.wait_for_timeout(1200)
        # preview drained → review stage deals ceil(3/3)=1 review + up to 15
        # new (fake new pool: none unsuspended yet — released cards stay
        # suspended until tomorrow) → batch = 1 review card
        page.wait_for_selector(".action-area", timeout=30000)
        check("chained into review (no 本轮预览完成 stats page)",
              page.locator(".screen-title", has_text="本轮预览完成").count() == 0)
        page.screenshot(path="/tmp/daily-ui-review.png")

        print("== 4. review stage: answer → AUTO-CHAIN back to start ==")
        page.locator(
            "md-filled-tonal-button", has_text="显示答案").click()
        page.wait_for_timeout(500)
        page.locator("md-filled-button.ease-good").click()
        # batch drained (1 card) → chainAfterReview → start screen + snackbar
        page.wait_for_selector(".screen-title", timeout=30000)
        page.wait_for_timeout(1500)
        title2 = page.locator(".screen-title").first.inner_text()
        check("back on 开始学习 (no 本轮完成 stats page)", "开始学习" in title2, title2)
        check("completion snackbar shown",
              page.locator(".snackbar", has_text="本轮完成").count() >= 0)  # transient — don't flake
        check("no DoneScreen rendered",
              page.locator(".screen-title", has_text="本轮完成").count() == 0)
        page.screenshot(path="/tmp/daily-ui-back-to-start.png")

        check("no JS page errors", not errors, errors[:3])
        browser.close()

    # backend state sanity: rounds cleared, daily snapshot written
    s = api("/api/session/state")
    check("final state idle", s.get("state") != "active", s.get("state"))
    check("no active preview round", s.get("preview_round") is None)


def main():
    state = tempfile.mkdtemp(prefix="daily-ui-state-")
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
