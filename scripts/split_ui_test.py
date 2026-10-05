#!/usr/bin/env python3
"""Playwright E2E: 分割文段 LINE-HANDLE picker (2026-10-01).

SUPERSEDES the selection→line-mapping test: the split flow no longer reads
the DOM selection. Every rendered source line carries a small handle button
(.line-handle, injected by lib/split-selection.ts annotateLines); atomic
blocks (fence/table/hr/$$ formula) get ONE handle for the whole block
(.line-anchor). Click first line → click last line → inline confirm bar with
range + HEAD and TAIL previews → 确认分割 → POST /api/reading/split.

SELF-CONTAINED: throwaway uvicorn on :8904 with isolated ANKI_STATE_DIR, a
temporary corpus (ANKI_NOTES_DIR) and a DEAD AnkiConnect — the live app
(:8901), the real collection and ~/anki-notes are untouched.

Covers:
  1. handle rendering — one handle per non-blank source line; a 3-line
     soft-wrapped paragraph gets THREE handles (line-level granularity);
     atomic blocks get ONE handle spanning the whole block range.
  2. two-click range pick — click line 4 then line 5 → confirm bar shows
     4–5, both lines tint (.line-sel), 头/尾 previews show the right text.
  3. 取消 clears the pick (confirm bar gone, no tint).
  4. 确认分割 persists the exact whole-line child (lines 4–5) via the API.
  5. post-split rounds: the soft-paragraph child re-renders with per-line
     handles; the tail child shows the fence and katex-block as single
     whole-block handles; a cross-block pick tints every overlapped item.
  6. the old selection-driven scissors button is GONE.
  7. WHOLE-LINE click target (2026-10-03): clicking the line TEXT (not the
     dot) picks it; hovering shows a pointer; a text-selection DRAG never
     picks (it stays the 添加挖空 cloze seed); clicking inside an atomic
     block picks the whole block.

Run: backend/.venv/bin/python scripts/split_ui_test.py
(needs playwright + chromium; both present in backend/.venv)
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8904"
REPO_ROOT = Path(__file__).resolve().parent.parent
PASS = 0
FAIL = 0


def check(name: str, cond: Any, detail: Any = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail!r}")


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


# Fixture (whole-file seeding → ONE segment covering 1..18).
# 1-based source lines:
#   1 # 测试笔记
#   2 (blank)
#   3 第一段第一行甲乙丙丁戊
#   4 第一段第二行己庚辛壬癸
#   5 第一段第三行子丑寅卯辰
#   6 (blank)
#   7 ## 小节
#   8 (blank)
#   9 ```bash
#  10 echo line-one
#  11 echo line-two
#  12 ```
#  13 (blank)
#  14 $$
#  15 E = mc^2
#  16 $$
#  17 (blank)
#  18 结尾正文一行。
NOTE = (
    "# 测试笔记\n"
    "\n"
    "第一段第一行甲乙丙丁戊\n"
    "第一段第二行己庚辛壬癸\n"
    "第一段第三行子丑寅卯辰\n"
    "\n"
    "## 小节\n"
    "\n"
    "```bash\n"
    "echo line-one\n"
    "echo line-two\n"
    "```\n"
    "\n"
    "$$\n"
    "E = mc^2\n"
    "$$\n"
    "\n"
    "结尾正文一行。\n"
)
A_REL = "2026/解剖/分割测试.md"


def _seed_and_deal():
    """Add the file (whole-file seed = ONE segment covering 1..N) and deal a
    daily round. Returns the dealt chunk payload."""
    r = api("/api/reading/list/add", "POST", {"path": A_REL})
    check("seed: file added", r.get("ok") is True, r)
    d = api("/api/reading/start?mode=focus", "POST")
    chunks = d.get("chunks", [])
    check("seed: whole-file deal = 1 chunk", len(chunks) == 1, chunks)
    return chunks[0] if chunks else None


# --- line-handle helpers (run against the page) ------------------------------

def _handles(page):
    """All handle ranges on the current card, in DOM order."""
    return page.evaluate("""() => Array.from(
        document.querySelectorAll('.reading-chunk-body .line-handle')
    ).map(h => [
        parseInt(h.dataset.lineStart), parseInt(h.dataset.lineEnd),
    ])""")


def _click_handle(page, start, end=None):
    sel = f'.reading-chunk-body .line-handle[data-line-start="{start}"]'
    if end is not None:
        sel += f'[data-line-end="{end}"]'
    loc = page.locator(sel)
    assert loc.count() >= 1, f"no handle {sel}"
    loc.first.click()
    page.wait_for_timeout(150)


def _confirm(page):
    """{range, previews[], n_sel} of the inline confirm bar, or None."""
    return page.evaluate("""() => {
        const bar = document.querySelector('.split-confirm');
        if (!bar) return null;
        return {
            range: (bar.querySelector('.split-confirm__range') || {}).textContent || '',
            previews: Array.from(bar.querySelectorAll('.split-confirm__preview'))
                .map(e => e.textContent),
            n_sel: document.querySelectorAll(
                '.reading-chunk-body .line-sel').length,
        };
    }""")


def _range_nums(txt):
    m = re.search(r"第\s*(\d+)–(\d+)\s*行（(\d+)\s*行）", txt or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _cancel(page):
    page.locator(".split-confirm__cancel").click()
    page.wait_for_timeout(150)


def _confirm_ok(page):
    page.locator(".split-confirm__ok").click()
    page.wait_for_timeout(900)


def _goto_card_with(page, needle, max_next=8):
    """Click 下一张 until the card body contains needle (or options run out)."""
    for _ in range(max_next):
        txt = page.locator(".reading-chunk-body").first.inner_text()
        if needle in txt:
            return True
        nxt = page.locator('md-text-button:has-text("下一张")')
        if nxt.count() and nxt.first.is_enabled():
            nxt.first.click()
            page.wait_for_timeout(400)
        else:
            break
    return needle in page.locator(".reading-chunk-body").first.inner_text()


def run(notes: Path):
    chunk = _seed_and_deal()
    if not chunk:
        print("seed failed; aborting browser phase")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        js_errors = []
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_selector(".reading-chunk-body .line-handle", timeout=30000)
        check("reading card rendered WITH line handles", True)
        check("dealt chunk is the whole file (line_start 1)",
              chunk["line_start"] == 1, chunk.get("line_start"))

        print("== 1. handle rendering ==")
        hs = _handles(page)
        # 8 handles: h1(1) + para lines(3,4,5) + h2(7) + fence(9-12, ONE)
        #          + katex(14-16, ONE) + closing line(18)
        expect = [[1, 1], [3, 3], [4, 4], [5, 5], [7, 7],
                  [9, 12], [14, 16], [18, 18]]
        check("one handle per source line; atomic blocks ONE whole-block handle",
              hs == expect, hs)
        soft = page.evaluate("""() => {
            const p = document.querySelector('.reading-chunk-body p');
            return p ? p.querySelectorAll(':scope > span.line > .line-handle').length : 0;
        }""")
        check("3-line soft paragraph has 3 handles (line granularity)",
              soft == 3, soft)
        anchors = page.evaluate("""() => Array.from(
            document.querySelectorAll('.reading-chunk-body .line-anchor')
        ).map(a => [
            a.firstElementChild ? a.firstElementChild.tagName : '',
            parseInt(a.dataset.lineStart), parseInt(a.dataset.lineEnd),
            a.querySelectorAll(':scope > .line-handle').length,
        ])""")
        check("fence + katex wrapped in .line-anchor with exactly 1 handle each",
              len(anchors) == 2
              and all(a[3] == 1 for a in anchors)
              and {tuple(a[1:3]) for a in anchors} == {(9, 12), (14, 16)},
              anchors)
        check("old selection-driven 分割文段 scissors button is GONE",
              page.locator('md-icon-button[data-aria-label^="分割文段"]').count() == 0)
        check("old .split-preview row is GONE",
              page.locator(".split-preview").count() == 0)

        print("== 2. two-click pick: line 4 then line 5 ==")
        _click_handle(page, 4)
        c = _confirm(page)
        check("single click → confirm bar with 1-line range 4–4",
              c is not None and _range_nums(c["range"]) == (4, 4, 1), c)
        check("head preview = line 4 text",
              c is not None and any("己庚辛壬癸" in p_ for p_ in c["previews"]),
              c and c["previews"])
        check("no tail preview for a 1-line cut",
              c is not None and len(c["previews"]) == 1, c and c["previews"])
        check("clicked line tinted", c is not None and c["n_sel"] == 1, c)

        _click_handle(page, 5)
        c = _confirm(page)
        check("second click extends range to 4–5 (2 lines)",
              c is not None and _range_nums(c["range"]) == (4, 5, 2), c)
        check("head preview = 己庚辛壬癸 AND tail preview = 子丑寅卯辰",
              c is not None and len(c["previews"]) == 2
              and "己庚辛壬癸" in c["previews"][0]
              and "子丑寅卯辰" in c["previews"][1],
              c and c["previews"])
        check("both lines tinted (.line-sel == 2)",
              c is not None and c["n_sel"] == 2, c)
        # re-adjust: clicking line 3 moves the far end back
        _click_handle(page, 3)
        c = _confirm(page)
        check("further click re-adjusts far end → 3–5",
              c is not None and _range_nums(c["range"]) == (3, 5, 3), c)

        print("== 3. 取消 clears the pick ==")
        _cancel(page)
        c = _confirm(page)
        check("confirm bar gone after 取消", c is None, c)
        check("no tint left after 取消",
              page.locator(".reading-chunk-body .line-sel").count() == 0)

        print("== 3b. whole-line click target (2026-10-03) ==")
        # click the TEXT of line 4 (not the dot) → picks 4–4
        loc4 = page.locator(
            '.reading-chunk-body span.line[data-line-start="4"]')
        box = loc4.first.bounding_box()
        assert box, "no box for span.line 4"
        page.mouse.click(box["x"] + box["width"] * 0.6,
                         box["y"] + box["height"] / 2)
        page.wait_for_timeout(150)
        c = _confirm(page)
        check("clicking line TEXT (not the dot) picks the line → 4–4",
              c is not None and _range_nums(c["range"]) == (4, 4, 1), c)
        _cancel(page)

        # hover a line → pointer cursor (whole-line affordance)
        page.hover('.reading-chunk-body span.line[data-line-start="5"]')
        page.wait_for_timeout(100)
        cur = page.evaluate("""() => getComputedStyle(document.querySelector(
            '.reading-chunk-body span.line[data-line-start="5"]')).cursor""")
        check("hovering a line shows pointer cursor", cur == "pointer", cur)

        # DRAG (text selection) must NOT pick — it is the 添加挖空 cloze seed
        b3 = page.locator(
            '.reading-chunk-body span.line[data-line-start="3"]'
        ).first.bounding_box()
        b5 = page.locator(
            '.reading-chunk-body span.line[data-line-start="5"]'
        ).first.bounding_box()
        assert b3 and b5, "no boxes for drag"
        page.mouse.move(b3["x"] + 30, b3["y"] + b3["height"] / 2)
        page.mouse.down()
        page.mouse.move(b5["x"] + b5["width"] - 10,
                        b5["y"] + b5["height"] / 2, steps=8)
        page.mouse.up()
        page.wait_for_timeout(200)
        c = _confirm(page)
        check("drag-selecting text does NOT open the confirm bar",
              c is None, c)
        sel_len = page.evaluate(
            "() => (window.getSelection()||'').toString().length")
        check("drag leaves a real text selection for 添加挖空",
              sel_len > 5, sel_len)
        page.evaluate("() => window.getSelection().removeAllRanges()")

        # click inside an ATOMIC block (fence) picks the whole block
        pbox = page.locator(
            '.reading-chunk-body .line-anchor pre').first.bounding_box()
        assert pbox, "no box for fence"
        page.mouse.click(pbox["x"] + pbox["width"] / 2,
                         pbox["y"] + pbox["height"] / 2)
        page.wait_for_timeout(150)
        c = _confirm(page)
        check("clicking inside the fence picks the WHOLE block 9–12",
              c is not None and _range_nums(c["range"]) == (9, 12, 4), c)
        _cancel(page)

        print("== 4. 确认分割 persists the exact whole-line child ==")
        _click_handle(page, 4)
        _click_handle(page, 5)
        _confirm_ok(page)
        st = api("/api/reading/state")
        round_segs = st.get("round", {}).get("chunks", [])
        todo = [c2 for c2 in round_segs
                if c2["path"] == A_REL and c2.get("status") == "todo"]
        cut = next((c2 for c2 in todo
                    if c2["line_start"] == 4 and c2["line_end"] == 5), None)
        check("persisted child = lines 4–5", cut is not None,
              [(c2["line_start"], c2["line_end"]) for c2 in todo])
        if cut:
            check("child text is exactly the two clicked lines",
                  "己庚辛壬癸" in cut["text"] and "子丑寅卯辰" in cut["text"]
                  and "甲乙丙丁戊" not in cut["text"], cut["text"][:80])
        bg = [c2 for c2 in round_segs if c2.get("status") == "background"]
        check("prefix NOT dealt in-round (bookmark sinks it to file queue)",
              len(bg) == 0, [(c2["line_start"], c2["line_end"]) for c2 in bg])
        # the read prefix (1–3) sank to BACKGROUND in the FILE's segment store
        # (not the round) — verify via the list summary's background count.
        entry = next((s for s in st.get("list", [])
                      if s.get("path") == A_REL), None)
        check("prefix (1–3) counted as background (bookmark policy)",
              entry is not None and entry.get("background") == 1,
              entry and {k: entry.get(k) for k in
                         ("background", "todo", "active", "done")})

        print("== 5. post-split rounds: children re-render handles ==")
        # List-driven dealing (2026-10-06): ONE segment per file per round.
        # After the bookmark split the file is prefix(bg) + cut(4–5,todo) +
        # tail(6–18,todo), so the frontier = the cut child. Deal it first…
        api("/api/reading/finish", "POST")
        d = api("/api/reading/start?mode=focus", "POST")
        cut_key = d["chunks"][0]["chunk_key"]
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".reading-chunk-body .line-handle", timeout=30000)
        # first dealt child = lines 4–5 (the cut): one <p> with 2 soft lines
        hs = _handles(page)
        check("cut child card: 2 per-line handles (rel 1–2)",
              hs == [[1, 1], [2, 2]], hs)
        _click_handle(page, 1)
        _click_handle(page, 2)
        c = _confirm(page)
        check("child pick 1–2 shows head 己庚 + tail 子丑",
              c is not None and _range_nums(c["range"]) == (1, 2, 2)
              and "己庚辛壬癸" in c["previews"][0]
              and "子丑寅卯辰" in c["previews"][1], c)
        _cancel(page)

        # …then complete the cut child so the frontier advances to the tail,
        # and deal a fresh round (the tail is now the file's one segment).
        _r = api("/api/reading/act?path=" + urllib.parse.quote(A_REL)
                 + "&chunk_key=" + urllib.parse.quote(cut_key)
                 + "&action=complete", "POST")
        check("completed the cut child", _r.get("ok") is True, _r)
        api("/api/reading/finish", "POST")
        api("/api/reading/start?mode=focus", "POST")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".reading-chunk-body .line-handle", timeout=30000)
        # tail child = src lines 6–18 → rel: h2=2, fence=4–7, katex=9–11,
        # closing line=13. It is now the frontier (the only dealt segment).
        check("tail child is now the dealt card (frontier advanced)",
              "echo line-one" in page.locator(".reading-chunk-body").first.inner_text(),
              page.locator(".reading-chunk-body").first.inner_text()[:80])
        hs = _handles(page)
        expect_tail = [[2, 2], [4, 7], [9, 11], [13, 13]]
        check("tail card handles: heading + fence(1) + katex(1) + closing",
              hs == expect_tail, hs)
        # atomic whole-block pick
        _click_handle(page, 4, 7)
        c = _confirm(page)
        check("fence handle picks the WHOLE block (4 lines)",
              c is not None and _range_nums(c["range"]) == (4, 7, 4), c)
        _cancel(page)
        # cross-block pick tints every overlapped item
        _click_handle(page, 2)
        _click_handle(page, 13)
        c = _confirm(page)
        check("cross-block pick 2–13 tints all 4 items",
              c is not None and _range_nums(c["range"]) == (2, 13, 12)
              and c["n_sel"] == 4, c)
        check("cross-block head=小节 tail=结尾",
              c is not None and "小节" in c["previews"][0]
              and "结尾正文一行" in c["previews"][1], c and c["previews"])
        _cancel(page)

        check("no JS page errors", not js_errors, js_errors[:3])
        browser.close()


def main():
    state = tempfile.mkdtemp(prefix="split-ui-state-")
    notes = Path(tempfile.mkdtemp(prefix="split-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / A_REL).write_text(NOTE, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",
        "ANKI_DAILY_READ": "50",
        "ANKICONNECT_URL": "http://127.0.0.1:18765",  # dead — never touch live
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    py = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8904"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        st = wait_ready()
        check("backend up on :8904", st.get("anki") in ("ok", "error"), st)
        run(notes)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(main())
