#!/usr/bin/env python3
"""Playwright check: 阅读页左栏「已制卡片」list (user spec 2026-09-20).

THROWAWAY stack: fake AnkiConnect (stdlib http.server) on :18766 +
uvicorn backend on :8903 with temp corpus/state. Live app, live collection
and ~/anki-notes untouched.

Covers:
  1. /api/reading/cards over real HTTP (qa + cloze shapes)
  2. reading round page renders 已制卡片 list styled like 相关卡片
     (.similar-item boxes, badges 问答 / 挖空 ×1, tags row)
  3. cloze card renders 正面 with [answer] cloze-q spans
  4. live update: adding a cloze via the dialog grows the list WITHOUT reload
  5. no JS page errors
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

BASE = "http://127.0.0.1:8903"
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
        return {"_status": e.code, "_body": e.read().decode()[:200]}


# ---- fake AnkiConnect -------------------------------------------------------

class FakeAnkiState:
    def __init__(self):
        self.notes = {}      # nid -> {modelName, fields, tags}
        self.cards = {}      # cid -> nid
        self.next_note = 1790000000000
        self.next_card = 1790000000100

fake = FakeAnkiState()


class FakeAnkiHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        action = req.get("action")
        p = req.get("params") or {}
        result = None
        if action == "addNotes":
            note = (p.get("notes") or [{}])[0]
            nid = fake.next_note
            fake.next_note += 1
            fake.notes[nid] = {
                "modelName": note.get("modelName", "问答题"),
                "fields": dict(note.get("fields") or {}),
                "tags": list(note.get("tags") or []),
            }
            cid = fake.next_card
            fake.next_card += 1
            fake.cards[cid] = nid
            result = [nid]
        elif action == "findCards":
            q = p.get("query", "")
            if q.startswith("nid:"):
                nid = int(q[4:])
                result = [c for c, nn in fake.cards.items() if nn == nid]
            else:
                result = []
        elif action == "notesInfo":
            out = []
            for nid in p.get("notes", []):
                nn = fake.notes.get(nid)
                if nn is None:
                    out.append({})
                    continue
                out.append({
                    "noteId": nid,
                    "tags": nn["tags"],
                    "modelName": nn["modelName"],
                    "fields": {k: {"value": v, "order": i}
                               for i, (k, v) in enumerate(nn["fields"].items())},
                    "cards": [c for c, x in fake.cards.items() if x == nid],
                })
            result = out
        elif action == "modelFieldNames":
            result = (["文字", "背面额外"] if p.get("modelName") == "填空题"
                      else ["正面", "背面"])
        elif action == "deckNames":
            result = ["2026", "预览池"]
        elif action == "getTags":
            result = []
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"result": result, "error": None}).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


NOTE = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内有颈阔肌，由面神经颈支支配。

## 二、颈筋膜

分为浅、中、深三层，形成筋膜鞘与间隙。
"""


def main():
    state = tempfile.mkdtemp(prefix="rdcards-state-")
    notes = Path(tempfile.mkdtemp(prefix="rdcards-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE, encoding="utf-8")

    srv = ThreadingHTTPServer(("127.0.0.1", 18766), FakeAnkiHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",
        "ANKICONNECT_URL": "http://127.0.0.1:18766",
        "ANKI_QUICK_READ": "2",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8903"],
        env=env, stdout=open("/tmp/rdcards_backend.log", "w"),
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


def run():
    # wait for backend
    t0 = time.time()
    while time.time() - t0 < 45:
        try:
            st = api("/api/status")
        except Exception:
            st = {}
        if st.get("reading_mode"):
            break
        time.sleep(0.5)
    else:
        print("backend never came up; log tail:")
        try:
            print(Path("/tmp/rdcards_backend.log").read_text()[-2000:])
        except OSError:
            pass
        sys.exit(1)
    check("backend up on :8903", api("/api/status").get("reading_mode") is True)

    # ---- seed: list the file, start a round, create 2 cards on chunk 0 ----
    r = api("/api/reading/list/add", "POST", {"path": "2026/解剖/颈部.md"})
    check("file listed", r.get("ok") is True, r)
    rd = api("/api/reading/start?mode=quick", "POST")
    chunks = rd.get("chunks") or []
    # whole-file seeding (2026-09-24 预切片退役): a listed file deals as ONE
    # segment covering the whole .md (consumption is via recursive splits)
    check("round dealt 1 whole-file chunk", len(chunks) == 1, rd)
    tgt = chunks[0]

    qa = api("/api/card/add", "POST", {
        "fields": {"正面": "颈阔肌由什么神经支配？", "背面": "面神经颈支"},
        "tags": [], "kind": "qa",
        "reading_source": {"path": tgt["path"], "chunk_key": tgt["chunk_key"]},
    })
    check("qa card added", "noteId" in qa, qa)
    cl = api("/api/card/add", "POST", {
        "fields": {"文字": "颈筋膜分为{{c1::浅、中、深}}三层。", "背面额外": ""},
        "tags": ["ui-test"], "kind": "cloze",
        "reading_source": {"path": tgt["path"], "chunk_key": tgt["chunk_key"]},
    })
    check("cloze card added", "noteId" in cl, cl)

    # ---- 1. endpoint over real HTTP ----
    rc = api(f"/api/reading/cards?notes={qa['noteId']},{cl['noteId']}")
    cards = rc.get("cards") or []
    check("reading/cards returns 2", len(cards) == 2, rc)
    check("qa shape", cards and cards[0]["kind"] == "qa"
          and cards[0]["fields"]["正面"] == "颈阔肌由什么神经支配？", cards[:1])
    check("cloze shape", len(cards) > 1 and cards[1]["kind"] == "cloze"
          and "{{c1::" in cards[1]["fields"]["文字"], cards[1:2])

    # ---- 2-3. page render ----
    js_errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        # funnel should land on the active reading round
        check("reading round page shown",
              page.locator(".reading-crumb").count() > 0)
        check("count line shows 2",
              page.locator(".reading-cards-made",
                           has_text="已从本片段制卡 2 张").count() == 1)
        sec = page.locator(".reading-made-section")
        check("已制卡片 section renders", sec.count() == 1)
        check("section title", sec.locator(".similar-title", has_text="已制卡片").count() == 1)
        items = sec.locator(".similar-item")
        check("2 similar-item boxes", items.count() == 2, items.count())
        # styling parity with 相关卡片: same class family + non-transparent bg
        check("uses .similar-item class", items.first.evaluate(
            "el => el.classList.contains('similar-item')"))
        bg = items.first.evaluate(
            "el => getComputedStyle(el).backgroundColor")
        check("item has surface-container bg", bg and bg != "rgba(0, 0, 0, 0)", bg)
        radius = items.first.evaluate(
            "el => getComputedStyle(el).borderRadius")
        check("item has rounded corners", radius not in ("0px", ""), radius)

        # qa item: badge + question + answer
        qa_item = items.nth(0)
        check("qa badge 问答", qa_item.locator(".similar-score", has_text="问答").count() == 1)
        check("qa question text",
              qa_item.locator(".similar-q", has_text="颈阔肌由什么神经支配").count() == 1)
        check("qa answer text",
              qa_item.locator(".similar-a", has_text="面神经颈支").count() == 1)

        # cloze item: badge, cloze-q rendered markers, tag row
        cl_item = items.nth(1)
        check("cloze badge 挖空 ×1",
              cl_item.locator(".similar-score", has_text="挖空 ×1").count() == 1)
        check("cloze front hides answer as cloze-q span",
              cl_item.locator(".similar-q .cloze-q").count() == 1)
        check("cloze-q text is […] (no hint)",
              cl_item.locator(".similar-q .cloze-q").inner_text().strip() == "[…]",
              cl_item.locator(".similar-q .cloze-q").inner_text())
        check("cloze answer line reveals content",
              cl_item.locator(".similar-a .cloze-a", has_text="浅、中、深").count() == 1)
        check("tag row renders",
              cl_item.locator(".reading-made-tags", has_text="#ui-test").count() == 1)

        page.screenshot(path="/tmp/reading_made_cards.png", full_page=False)

        # ---- 4. live update: add a cloze via the dialog, no reload ----
        page.locator('md-icon-button[data-aria-label="添加挖空"]').click()
        page.wait_for_selector(".cloze-dialog", timeout=10000)
        page.wait_for_timeout(800)
        ta = page.locator(".cloze-dialog textarea.cloze-field").first
        ta.fill("测试 live update {{c1::胸锁乳突肌}}前缘。")
        page.locator(".cloze-dialog md-filled-button", has_text="添加挖空卡").click()
        page.wait_for_selector(".cloze-dialog", state="detached", timeout=10000)
        page.wait_for_timeout(1200)
        check("count line now 3 (no reload)",
              page.locator(".reading-cards-made",
                           has_text="已从本片段制卡 3 张").count() == 1)
        check("list now has 3 items",
              page.locator(".reading-made-section .similar-item").count() == 3)
        new_item = page.locator(".reading-made-section .similar-item").nth(2)
        check("new cloze rendered live",
              new_item.locator(".similar-q .cloze-q").count() == 1)

        # ---- 5. STORED FIELD IS HTML (markdown preservation 2026-09-20):
        # the dialog converts md → HTML on save (Anki renders fields
        # natively) — the fake AnkiConnect echoes stored fields verbatim,
        # so /api/reading/cards must show <p>…</p> + the marker inside.
        rc3 = api("/api/reading/state")
        chunk3 = (rc3.get("round") or {}).get("chunks", [{}])[0]
        ids3 = chunk3.get("cards_created") or []
        rcards = api(f"/api/reading/cards?notes={','.join(str(i) for i in ids3 if i)}")
        stored = [x for x in rcards.get("cards", [])
                  if x["kind"] == "cloze" and "胸锁乳突肌" in (x["fields"].get("文字") or "")]
        check("dialog-saved cloze field stored as HTML",
              stored and stored[0]["fields"]["文字"].startswith("<p>")
              and "{{c1::胸锁乳突肌}}" in stored[0]["fields"]["文字"],
              stored[0]["fields"] if stored else rcards)

        # ---- 6. CONTEXT SEED (user fix 2026-09-20): selecting text in the
        # chunk body then 添加挖空 must seed the dialog with the WHOLE chunk's
        # RAW MARKDOWN (tables/bold preserved — the field is HTML-converted
        # on SAVE, user spec "卡片看起来就是直接在 chunk 上挖空") and the
        # selection wrapped IN PLACE — not just {{c1::selection}} (which
        # made unrecallable "[…]"-only cards).
        # NOTE: lastSel is STICKY per chunk by design (round-2: the selection
        # must survive the toolbar click clearing it) — so the no-selection
        # case must be checked BEFORE any selection is made.

        # ---- 6a. no selection yet → whole chunk seeded as raw markdown ----
        page.locator('md-icon-button[data-aria-label="添加挖空"]').click()
        page.wait_for_selector(".cloze-dialog", timeout=10000)
        page.wait_for_timeout(800)
        seed2 = page.locator(".cloze-dialog textarea.cloze-field").first.input_value()
        check("no-selection seed = whole raw chunk, no auto-cloze",
              "浅筋膜内有颈阔肌" in seed2 and "{{c1::" not in seed2
              and seed2.lstrip().startswith("#"), repr(seed2))
        page.get_by_text("取消", exact=True).click()
        page.wait_for_selector(".cloze-dialog", state="detached", timeout=10000)

        # ---- 6b. select 颈阔肌 → seed wraps it in place with context ----
        sel_ok = page.evaluate("""() => {
          const body = document.querySelector('.reading-chunk-body');
          if (!body) return false;
          const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {
            const idx = (node.textContent || '').indexOf('颈阔肌');
            if (idx >= 0) {
              const r = document.createRange();
              r.setStart(node, idx);
              r.setEnd(node, idx + 3);
              const sel = window.getSelection();
              sel.removeAllRanges();
              sel.addRange(r);
              return true;
            }
          }
          return false;
        }""")
        check("selection created in chunk body", sel_ok)
        page.wait_for_timeout(300)  # selectionchange fires async
        page.locator('md-icon-button[data-aria-label="添加挖空"]').click()
        page.wait_for_selector(".cloze-dialog", timeout=10000)
        page.wait_for_timeout(800)
        seed = page.locator(".cloze-dialog textarea.cloze-field").first.input_value()
        check("seed wraps selection IN CONTEXT",
              "浅筋膜内有{{c1::颈阔肌}}，由面神经颈支支配" in seed, repr(seed))
        check("seed keeps the whole chunk (not selection-only)",
              seed != "{{c1::颈阔肌}}" and "一、浅层结构" in seed, repr(seed))
        check("seed KEEPS raw markdown (## header) — converted on save",
              "## 一、浅层结构" in seed, repr(seed))
        page.get_by_text("取消", exact=True).click()
        page.wait_for_selector(".cloze-dialog", state="detached", timeout=10000)

        check("no JS page errors", not js_errors, js_errors[:3])
        page.screenshot(path="/tmp/reading_made_cards_after_add.png")
        browser.close()

    # ---- cleanup: finish the round (backend state is throwaway anyway) ----
    api("/api/reading/finish", "POST")


if __name__ == "__main__":
    sys.exit(main())
