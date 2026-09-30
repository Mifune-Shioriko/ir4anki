#!/usr/bin/env python3
"""Playwright E2E: 分割文段 selection→line mapping + stale/sticky fixes (2026-09-30).

SELF-CONTAINED: throwaway uvicorn on :8904 with isolated ANKI_STATE_DIR, a
temporary corpus (ANKI_NOTES_DIR) and a DEAD AnkiConnect — the live app
(:8901), the real collection and ~/anki-notes are untouched.

Covers the FRONTEND half of the split-misalignment fix (the backend half is
split_guard_test.py):
  1. line-level refinement — selecting lines 2-3 of a 3-line soft-wrapped
     paragraph cuts EXACTLY those source lines (not the whole <p> block).
  2. atomic snap — selecting one line inside a ``` code block snaps to the
     WHOLE block range (a sub-range cut would corrupt the fence).
  3. atomic snap — selecting inside a $$ display formula snaps to the whole
     katex-block (the formula token used to carry NO data-src-line at all).
  4. the 切割预览 row shows the real cut range before the click.
  5. sticky-selection fix — after selecting in the card body, selecting text
     in the RIGHT-COLUMN full-file viewer clears the split selection (the
     分割 button greys out) instead of keeping the stale in-card range.

Run: backend/.venv/bin/python scripts/split_ui_test.py
(needs playwright + chromium; both present in backend/.venv)
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

BASE = "http://127.0.0.1:8904"
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


# A fixture whose paragraph soft-wraps over 3 distinct lines (no inline markup
# → one text node with embedded '\n', exactly what selectionToLines refines).
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


# --- DOM selection helpers (run in the page) ---------------------------------

def _select_paragraph_lines(page, line_from, line_to):
    """Select from the start of source line `line_from` to the end of
    `line_to` INSIDE the first <p data-src-line] of the card body. Returns the
    relative line range the app computed (read back off the preview row)."""
    return page.evaluate(
        """([from, to]) => {
          const body = document.querySelector('.reading-chunk-body');
          // the soft-wrapped 3-line paragraph is the <p> whose text has 2 '\\n'
          const p = [...body.querySelectorAll('p[data-src-line]')]
            .find(e => (e.textContent.match(/\\n/g) || []).length === 2);
          const tn = p.firstChild;  // single text node with embedded newlines
          const text = tn.textContent;
          const idxs = [];
          for (let i = 0; i < text.length; i++) if (text[i] === '\\n') idxs.push(i);
          // line k (1-based within p) starts after the (k-1)-th newline
          const lineStart = (k) => k === 1 ? 0 : idxs[k - 2] + 1;
          const lineEnd = (k) => k >= idxs.length + 1 ? text.length : idxs[k - 1];
          const r = document.createRange();
          r.setStart(tn, lineStart(from));
          r.setEnd(tn, lineEnd(to));
          const sel = window.getSelection();
          sel.removeAllRanges(); sel.addRange(r);
          sel.getRangeAt(0); // force selectionchange consumers
          return { selected: sel.toString() };
        }""",
        [line_from, line_to],
    )


def _select_in_atomic(page, kind):
    """Select text INSIDE a code block (kind='code') or a $$ formula
    (kind='math') of the card body."""
    return page.evaluate(
        """(kind) => {
          const body = document.querySelector('.reading-chunk-body');
          const host = kind === 'code'
            ? body.querySelector('pre')
            : body.querySelector('.katex-block');
          const tn = [...host.childNodes].find(n => n.nodeType === 3 && n.textContent.trim())
            || host.querySelector('*') || host;
          let node = tn, off0 = 0, off1 = 1;
          if (kind === 'code') {
            // <pre><code>text\\n</code></pre> — select inside the <code> text
            const code = host.querySelector('code');
            node = code.firstChild; off0 = 0; off1 = Math.min(4, node.textContent.length);
          } else {
            // katex renders nested spans; select the annotation text
            const ann = host.querySelector('annotation') || host.querySelector('mi') || host;
            node = ann.firstChild && ann.firstChild.nodeType === 3 ? ann.firstChild : ann;
            off0 = 0; off1 = 1;
          }
          const r = document.createRange();
          r.setStart(node, off0); r.setEnd(node, off1);
          const sel = window.getSelection();
          sel.removeAllRanges(); sel.addRange(r);
          return { hostTag: host.tagName, hostClass: host.className };
        }""",
        kind,
    )


def _select_any_in_body(page):
    """Select a few chars inside ANY paragraph of the card body (section 4
    just needs an in-body selection to exist before testing the sticky clear;
    it must not assume the 3-line paragraph is on the current card)."""
    return page.evaluate(
        """() => {
          const body = document.querySelector('.reading-chunk-body');
          if (!body) return { ok: false };
          const ps = [...body.querySelectorAll('p[data-src-line]')];
          for (const p of ps) {
            // walk to the first real TEXT node (firstChild may be an inline
            // element like a katex span, where offsets count children)
            const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
            let tn = w.nextNode();
            while (tn && !tn.textContent.trim()) tn = w.nextNode();
            if (tn) {
              const r = document.createRange();
              r.setStart(tn, 0);
              r.setEnd(tn, Math.min(3, tn.textContent.length));
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(r);
              return { ok: true, text: sel.toString() };
            }
          }
          return { ok: false };
        }"""
    )


def _select_in_right_column(page):
    """Select text in the RIGHT-COLUMN full-file viewer (.note-column)."""
    return page.evaluate(
        """() => {
          const body = document.querySelector('.note-column .note-body');
          if (!body) return { ok: false };
          const ps = [...body.querySelectorAll('p[data-src-line]')];
          for (const p of ps.reverse()) {
            const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
            let tn = w.nextNode();
            while (tn && !tn.textContent.trim()) tn = w.nextNode();
            if (tn) {
              const r = document.createRange();
              r.setStart(tn, 0);
              r.setEnd(tn, Math.min(3, tn.textContent.length));
              const sel = window.getSelection();
              sel.removeAllRanges(); sel.addRange(r);
              return { ok: true, text: sel.toString() };
            }
          }
          return { ok: false };
        }"""
    )


def _preview(page):
    """Read the 切割预览 row (or null when absent)."""
    return page.evaluate(
        """() => {
          const el = document.querySelector('.split-preview__range');
          return el ? el.textContent : null;
        }"""
    )


def _split_enabled(page):
    return page.evaluate(
        """() => {
          const b = document.querySelector('md-icon-button[data-aria-label^=\"分割文段\"]');
          return b ? !b.disabled : null;
        }"""
    )


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
        page.wait_for_selector(".reading-chunk-body", timeout=30000)
        check("reading card body rendered", True)
        # the whole-file segment covers 1..18 → chunk.line_start == 1, so the
        # preview's RELATIVE lines equal absolute file lines (base = 0).
        check("dealt chunk is the whole file (line_start 1)",
              chunk["line_start"] == 1, chunk.get("line_start"))

        print("== 1. line-level refinement: select para lines 2-3 ==")
        # source lines 4-5 (para occupies 3-5; its line 2 = src 4, line 3 = src 5)
        sel = _select_paragraph_lines(page, 2, 3)
        check("paragraph lines 2-3 selected", "己庚辛壬癸" in sel["selected"]
              and "子丑寅卯辰" in sel["selected"]
              and "甲乙丙丁戊" not in sel["selected"], sel["selected"])
        page.wait_for_function("() => document.querySelector('.split-preview__range')", timeout=5000)
        pv = _preview(page)
        check("preview shows lines 4–5 (NOT whole para 3–5)",
              pv is not None and "4" in pv and "5" in pv and "3" not in pv, pv)
        # click split, verify the persisted child range via state
        page.locator('md-icon-button[data-aria-label^="分割文段"]').click()
        page.wait_for_timeout(800)
        st = api("/api/reading/state")
        round_segs = st.get("round", {}).get("chunks", [])
        # the selected todo child should be exactly source lines 4-5
        todo = [c for c in round_segs
                if c["path"] == A_REL and c.get("status") == "todo"]
        cut = next((c for c in todo if c["line_start"] == 4 and c["line_end"] == 5), None)
        check("persisted cut = lines 4-5 (line-level, not whole block)",
              cut is not None, [(c["line_start"], c["line_end"]) for c in todo])
        if cut:
            check("cut text is exactly para lines 2-3",
                  "己庚辛壬癸" in cut["text"] and "子丑寅卯辰" in cut["text"]
                  and "甲乙丙丁戊" not in cut["text"], cut["text"])

        print("== 2. atomic snap: one line inside a ``` code block ==")
        # re-deal so a fresh whole-file segment is on screen
        api("/api/reading/finish", "POST")
        d = api("/api/reading/start?mode=focus", "POST")
        # the code block may be its own segment after the previous cut; find a
        # chunk whose text contains the fence and select inside it
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".reading-chunk-body", timeout=30000)
        # advance to a card that shows the code block
        found = False
        for _ in range(8):
            body_txt = page.locator(".reading-chunk-body").first.inner_text()
            if "echo line-one" in body_txt or "```" in body_txt or "E = mc" in body_txt:
                found = True
                break
            nxt = page.locator('md-text-button:has-text("下一张")')
            if nxt.count() and nxt.first.is_enabled():
                nxt.first.click(); page.wait_for_timeout(400)
            else:
                break
        has_code = page.evaluate(
            "() => !!document.querySelector('.reading-chunk-body pre')")
        if has_code:
            _select_in_atomic(page, "code")
            page.wait_for_timeout(300)
            pv = _preview(page)
            # the fence spans 3 source lines within its own segment; the cut
            # must be the WHOLE block, so the preview range covers ≥3 lines
            # (start..end with end-start+1 >= 3) OR the whole segment.
            check("code-block selection → preview present (atomic snap)",
                  pv is not None, pv)
            # verify it snapped: selecting 4 chars still yields the full block
            if pv:
                import re
                nums = re.findall(r"\d+", pv)
                if len(nums) >= 2:
                    a, b = int(nums[0]), int(nums[1])
                    check("code-block snap spans the whole fence (>=3 lines)",
                          b - a + 1 >= 3, pv)
        else:
            check("code block present in some dealt card", False, "no <pre> found")

        print("== 3. atomic snap: $$ display formula ==")
        has_math = page.evaluate(
            "() => !!document.querySelector('.reading-chunk-body .katex-block')")
        if not has_math:
            # advance to the card carrying the formula
            for _ in range(8):
                if page.evaluate("() => !!document.querySelector('.reading-chunk-body .katex-block')"):
                    has_math = True
                    break
                nxt = page.locator('md-text-button:has-text("下一张")')
                if nxt.count() and nxt.first.is_enabled():
                    nxt.first.click(); page.wait_for_timeout(400)
                else:
                    break
        check("katex-block rendered WITH data-src-line (P3b tagging)",
              page.evaluate("""() => {
                const k = document.querySelector('.reading-chunk-body .katex-block');
                return !!k && !!k.getAttribute('data-src-line');
              }"""),
              "no data-src-line on .katex-block")
        if has_math:
            _select_in_atomic(page, "math")
            page.wait_for_timeout(300)
            pv = _preview(page)
            check("formula selection → preview present (atomic snap, non-null)",
                  pv is not None, pv)

        print("== 4. sticky fix: right-column selection clears the split ==")
        # make sure we're on a card with selectable body text
        for _ in range(8):
            if _select_any_in_body(page).get("ok"):
                break
            nxt = page.locator('md-text-button:has-text("下一张")')
            if nxt.count() and nxt.first.is_enabled():
                nxt.first.click(); page.wait_for_timeout(400)
            else:
                break
        body_ok = _select_any_in_body(page).get("ok")
        check("found a card with selectable body paragraph", body_ok, body_ok)
        page.wait_for_timeout(300)
        enabled_in_card = _split_enabled(page)
        check("split button ENABLED after in-card selection",
              enabled_in_card is True, enabled_in_card)
        rc = _select_in_right_column(page)
        if rc.get("ok"):
            page.wait_for_timeout(300)
            enabled_after = _split_enabled(page)
            preview_after = _preview(page)
            check("right-column selection DISABLES split (stale range cleared)",
                  enabled_after is False, f"enabled={enabled_after} preview={preview_after}")
            check("preview row gone after outside selection",
                  preview_after is None, preview_after)
        else:
            check("right column selectable (note-body present at 1920)",
                  False, "could not select in .note-column")

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
