#!/usr/bin/env python3
"""Playwright regression: the 2026-10-05 UX round (three user specs).

THROWAWAY stack: fake AnkiConnect (stdlib http.server) on :18770 +
uvicorn backend on :8906 with temp corpus/state. Live app, live collection
and ~/anki-notes untouched.

Covers:
  A. GOAL-BASED READING (ANKI_DAILY_MAKE=2, PREVIEW_MODE on):
     1. start screen plan row shows 已制 N / 2 张 (goal row, not "4 段")
     2. reading ring tracks made/goal (not batch position)
     3. crossing the goal at a SEGMENT BOUNDARY (制卡完成) chains straight
        into review — no second reading batch, no mid-card cutoff
     4. 开始 again with the goal met → skips reading entirely (review)
  B. EDITOR DIALOGS = WIDE CENTERED + SCRIM-GUARDED:
     5. the add-card dialog is centered (top gap ≈ bottom gap, not a sheet)
     6. scrim click does NOT dismiss (add + cloze dialogs); Esc does
     7. dialog width matches .seg-edit-dialog's formula (≈ 2 columns)
  C. LINE PICKER VISUALS:
     8. selected lines have NO background tint (荧光 removed)
     9. hovering the GAP between wrapped visual rows of one source line
        keeps its dot lit (.line-hover — one line = one block)

Run: backend/.venv/bin/python scripts/goal_ui_test.py
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

BASE = "http://127.0.0.1:8906"
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
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


# ---- fake AnkiConnect ------------------------------------------------------
# ANKI-day-aware note ids (modes_ui_test fixture lesson: anchor to now-4h so
# "today" survives the 00:00–04:00 window too).
_anki_now = datetime.now() - timedelta(hours=4)


def _nid(days_back, hour=12):
    dt = (_anki_now - timedelta(days=days_back)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


class FakeState:
    def __init__(self):
        NID_REV = _nid(40)
        NID_TODAY_1 = _nid(0, max(5, _anki_now.hour))
        NID_TODAY_2 = _nid(0, max(5, _anki_now.hour) + 1)
        # one due review card (the review stage needs something to deal) +
        # TWO pool cards made today = goal (ANKI_DAILY_MAKE=2) already met
        self.cards = {
            9001: {"type": 1, "queue": 2, "due": 100, "deck": "2026",
                   "susp": False, "nid": NID_REV},
            9101: {"type": 0, "queue": -1, "due": 0, "deck": "预览池",
                   "susp": True, "nid": NID_TODAY_1},
            9102: {"type": 0, "queue": -1, "due": 0, "deck": "预览池",
                   "susp": True, "nid": NID_TODAY_2},
        }
        self.notes = {nid: {"tags": [], "modelName": "问答题"} for nid in
                      (NID_REV, NID_TODAY_1, NID_TODAY_2)}
        self.next_note = _nid(0) + 10_000_000
        self.next_cid = 9200


fake = FakeState()


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _match(self, cid, c, q):
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
            if tok.startswith("tag:released-*"):
                if not any(t.startswith("released-")
                           for t in fake.notes[c["nid"]]["tags"]):
                    return False
        if q.startswith("nid:"):
            return c["nid"] == int(q[4:])
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
            result = [cid for cid, c in fake.cards.items()
                      if self._match(cid, c, q)]
        elif action == "cardsInfo":
            out = []
            for cid in p.get("cards", []):
                c = fake.cards.get(cid)
                if not c:
                    out.append({})
                    continue
                out.append({
                    "cardId": cid, "note": c["nid"], "type": c["type"],
                    "queue": c["queue"], "due": c["due"],
                    "deckName": c["deck"], "modelName": "问答题",
                    "question": f"<p>Q{cid}</p>", "answer": f"<p>A{cid}</p>",
                    "css": "", "interval": 1, "factor": 2500, "reps": 1,
                    "lapses": 0, "left": 0, "isNew": c["type"] == 0,
                })
            result = out
        elif action == "notesInfo":
            out = []
            for nid in p.get("notes", []):
                if nid in fake.notes:
                    out.append({
                        "noteId": nid,
                        "tags": list(fake.notes[nid]["tags"]),
                        "modelName": fake.notes[nid]["modelName"],
                        "fields": {"正面": {"value": "x", "order": 0},
                                   "背面": {"value": "y", "order": 1}},
                        "cards": [cid for cid, c in fake.cards.items()
                                  if c["nid"] == nid],
                    })
                else:
                    out.append({})
            result = out
        elif action == "addNotes":
            note = (p.get("notes") or [{}])[0]
            nid = fake.next_note
            fake.next_note += 1
            fake.notes[nid] = {"tags": list(note.get("tags") or []),
                               "modelName": note.get("modelName", "问答题")}
            cid = fake.next_cid
            fake.next_cid += 1
            fake.cards[cid] = {"type": 0, "queue": 0, "due": 0,
                               "deck": "2026", "susp": False, "nid": nid}
            result = [nid]
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
                    fake.cards[cid]["queue"] = (
                        0 if fake.cards[cid]["type"] == 0 else 2)
            result = True
        elif action == "answerCards":
            for a in p.get("answers", []):
                cid = a.get("cardId")
                if cid in fake.cards:
                    fake.cards[cid]["due"] = 200000
            result = [True] * len(p.get("answers", []))
        elif action == "modelFieldNames":
            result = (["文字", "背面额外"] if p.get("modelName") == "填空题"
                      else ["正面", "背面"])
        elif action == "getTags":
            result = ["解剖"]
        elif action == "deckNames":
            result = ["2026", "预览池"]
        elif action == "updateNoteFields":
            result = None
        elif action in ("addTags", "removeTags"):
            result = True
        elif action == "sync":
            result = None
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"result": result, "error": None}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


NOTE = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经颈支支配，收缩时牵拉口角。

## 二、颈筋膜

分为浅、中、深三层，各层之间形成筋膜鞘与潜在间隙，容纳血管神经束的通行。
"""
A_REL = "2026/解剖/颈部.md"


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".screen-title", timeout=30000)

        print("== A. goal-based reading ==")
        stats = " ".join(page.locator(".screen-stats").all_inner_texts())
        check("start screen: goal row 已制 2 / 2 张",
              "已制 2 / 2 张" in stats, stats[:200])
        check("start screen: detail copy explains the goal",
              "制满" in page.locator(".screen-detail").inner_text())

        # goal already met → 开始 skips reading, lands on the review card
        page.locator("md-filled-button", has_text="开始").click()
        page.wait_for_selector(".action-area", timeout=30000)
        check("goal met → chained straight into review (no reading card)",
              page.locator(".reading-chunk-body").count() == 0)
        check("review card rendered",
              page.locator("md-filled-tonal-button", has_text="显示答案").count() == 1)

        # back to start; then UN-meet the goal (release one pool card) so
        # reading deals again — the boundary-crossing path
        api("/api/session/finish", "POST")
        fake.cards[9102]["deck"] = "2026"
        fake.cards[9102]["susp"] = False
        fake.cards[9102]["queue"] = 0
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".screen-title", timeout=30000)
        stats = " ".join(page.locator(".screen-stats").all_inner_texts())
        check("start screen: goal row now 已制 1 / 2 张",
              "已制 1 / 2 张" in stats, stats[:200])
        page.locator("md-filled-button", has_text="开始").click()
        page.wait_for_selector(".reading-chunk-body", timeout=30000)
        page.wait_for_selector(".nav-rail__footer .progress-ring", timeout=15000)
        ring = page.locator(".progress-ring__text").inner_text()
        check("reading ring tracks the GOAL (1/2), not batch position",
              "1/2" in ring, ring)

        print("== B/C need a card made: add one via the dialog ==")
        # add-card dialog = the wide centered scrim-guarded dialog
        page.locator('md-icon-button[data-aria-label="添加卡片"]').first.click()
        page.wait_for_selector(".edit-dialog .rich-field", timeout=20000)
        page.wait_for_timeout(600)

        print("== B. dialogs: centered + scrim-guarded ==")
        box = page.evaluate("""() => {
            const h = document.querySelector('.edit-dialog');
            const inner = h.shadowRoot.querySelector('dialog');
            const r = inner.getBoundingClientRect();
            return {top: r.top, bottom: innerHeight - r.bottom,
                    width: r.width, left: r.left, vw: innerWidth};
        }""")
        check("dialog is VERTICALLY centered (not a bottom sheet)",
              abs(box["top"] - box["bottom"]) < 40, box)
        check("dialog is horizontally centered",
              abs((box["vw"] - box["width"]) / 2 - box["left"]) < 4, box)
        seg_w = page.evaluate("""() => {
            const h = document.createElement('md-dialog');
            h.className = 'seg-edit-dialog';
            document.body.appendChild(h);
            const w = getComputedStyle(h).width;
            h.remove();
            return w;
        }""")
        check(f"dialog width matches the seg-edit formula ({seg_w})",
              abs(float(seg_w.replace('px', '')) - box["width"]) < 2,
              f"seg={seg_w} edit={box['width']}")

        # scrim click (top-left corner, outside the dialog) must NOT close
        page.mouse.click(30, 30)
        page.wait_for_timeout(500)
        check("scrim click does NOT dismiss the dialog",
              page.locator(".edit-dialog").count() == 1)
        # type into the front field + save — the card is made, the goal is
        # crossed, but NOTHING may happen until a segment boundary
        page.locator(".edit-dialog .rich-field").first.click()
        page.keyboard.type("测试问题：颈阔肌由什么神经支配？")
        page.locator(".edit-dialog .rich-field").nth(1).click()
        page.keyboard.type("测试答案：面神经颈支。")
        page.locator('.edit-dialog md-filled-button:has-text("添加")').click()
        page.wait_for_selector(".edit-dialog", state="detached", timeout=15000)
        check("dialog closed after 添加 (explicit exit works)", True)
        page.wait_for_timeout(500)
        check("still on the READING card after crossing the goal mid-segment"
              " (no mid-card cutoff)",
              page.locator(".reading-chunk-body").count() == 1)
        ring = page.locator(".progress-ring__text").inner_text()
        check("ring moved to 2/2 live", "2/2" in ring, ring)

        # cloze dialog: scrim-guarded too, Esc closes
        page.locator('md-icon-button[data-aria-label="添加挖空"]').first.click()
        page.wait_for_selector(".cloze-dialog", timeout=20000)
        page.mouse.click(30, 30)
        page.wait_for_timeout(400)
        check("cloze dialog survives a scrim click",
              page.locator(".cloze-dialog").count() == 1)
        page.keyboard.press("Escape")
        page.wait_for_selector(".cloze-dialog", state="detached", timeout=10000)
        check("Esc closes the cloze dialog", True)

        print("== C. line picker visuals ==")
        page.evaluate("""() => {
            document.querySelectorAll('.reading-chunk-body .line')
                .forEach(e => { if (e.dataset.lineStart === '1')
                    e.classList.add('line-sel') })
        }""")
        bg = page.evaluate("""() => getComputedStyle(
            document.querySelector('.reading-chunk-body .line-sel')
        ).backgroundColor""")
        check("selected line has NO background tint (荧光 removed)",
              bg in ("rgba(0, 0, 0, 0)", "transparent"), bg)
        dot_bg = page.evaluate("""() => getComputedStyle(
            document.querySelector('.reading-chunk-body .line-sel > .line-handle')
        ).backgroundColor""")
        check("selected line's DOT is lit (primary)",
              dot_bg not in ("rgba(0, 0, 0, 0)", "transparent"), dot_bg)
        page.evaluate("""() => document.querySelectorAll(
            '.reading-chunk-body .line-sel'
        ).forEach(e => e.classList.remove('line-sel'))""")

        # hover the GAP between two wrapped visual rows of source line 5
        # (the long paragraph). resolveLineAt must keep ONE dot lit across
        # the whole band — sample 5 y-offsets inside the paragraph box.
        lit_counts = page.evaluate("""() => {
            const lines = Array.from(
                document.querySelectorAll('.reading-chunk-body .line'));
            const long = lines.find(l => l.getBoundingClientRect().height > 40);
            return long ? parseInt(long.dataset.lineStart) : null;
        }""")
        check("fixture has a soft-wrapped (multi-row) line", lit_counts is not None,
              lit_counts)
        if lit_counts is not None:
            band = page.evaluate("""(n) => {
                const el = document.querySelector(
                    `.reading-chunk-body .line[data-line-start="${n}"]`);
                const r = el.getBoundingClientRect();
                return {x: r.x + 60, top: r.top, h: r.height};
            }""", lit_counts)
            stays_lit = []
            for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
                page.mouse.move(band["x"], band["top"] + band["h"] * frac)
                page.wait_for_timeout(120)
                n_lit = page.evaluate(
                    "() => document.querySelectorAll('.line-hover').length")
                stays_lit.append(n_lit)
            check("dot stays lit at EVERY height inside the wrapped line's"
                  " band (gap flicker gone)",
                  all(n == 1 for n in stays_lit), stays_lit)
            # moving fully out of the body clears it
            page.mouse.move(5, 5)
            page.wait_for_timeout(200)
            check("hover clears outside the body",
                  page.evaluate(
                      "() => document.querySelectorAll('.line-hover').length"
                  ) == 0)

        print("== A3. goal boundary → chain into review ==")
        # 制卡完成 = segment boundary; goal (2/2) is met → the backend parks
        # the round and the frontend chains into the review stage
        page.locator("md-filled-button", has_text="制卡完成").click()
        page.wait_for_selector(".action-area", timeout=30000)
        page.wait_for_timeout(600)
        check("boundary with goal met → chained into review",
              page.locator("md-filled-tonal-button",
                           has_text="显示答案").count() == 1)
        check("no reading card left on screen",
              page.locator(".reading-chunk-body").count() == 0)
        st = api("/api/reading/state")
        rnd = st.get("round")
        check("round parked as complete",
              rnd is None or rnd.get("status") == "complete", rnd)
        # the skipped remainder must keep its statuses (todo — re-dealt)
        segs = api("/api/reading/status")["list"][0]
        check("file still has a frontier (nothing was force-completed)",
              segs.get("frontier") is not None, segs)

        check("no JS page errors", not errors, errors[:3])
        browser.close()


def main():
    state = tempfile.mkdtemp(prefix="goal-ui-state-")
    notes = Path(tempfile.mkdtemp(prefix="goal-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / A_REL).write_text(NOTE, encoding="utf-8")

    srv = ThreadingHTTPServer(("127.0.0.1", 18770), FakeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "1",
        "ANKICONNECT_URL": "http://127.0.0.1:18770",
        "ANKI_DAILY_READ": "2",
        "ANKI_DAILY_MAKE": "2",
        "ANKI_RELEASE_DAILY_GOAL": "45",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    py = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8906"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(90):
            try:
                if api("/api/status").get("anki") in ("ok", "error"):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("test backend never came up")
        r = api("/api/reading/list/add", "POST", {"path": A_REL})
        check("seed: file listed", r.get("ok") is True, r)
        # cut the whole-file segment into TWO todo sections (bookmark split:
        # selection = section 1 → todo child, tail after it → todo), so the
        # goal-boundary park has a survivor to assert on (frontier must
        # remain: nothing was force-completed)
        st0 = api("/api/reading/status")["list"][0]
        whole_key = st0["frontier"]["chunk_key"]
        lines = NOTE.split("\n")
        h1 = next(i + 1 for i, l in enumerate(lines) if l.startswith("## 一"))
        h2 = next(i + 1 for i, l in enumerate(lines) if l.startswith("## 二"))
        r = api("/api/reading/split", "POST", {
            "path": A_REL, "seg_id": int(whole_key),
            "selections": [{"start_line": h1, "end_line": h2 - 1}],
            "gap_policy": "bookmark"})
        check("seed: split into 2 todo sections", r.get("ok") is True, r)
        st0 = api("/api/reading/status")["list"][0]
        check("seed: 2 todo segments dealable", st0.get("todo") == 2, st0)
        run()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(main())
