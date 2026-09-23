#!/usr/bin/env python3
"""Playwright E2E: 片段编辑器 + note images (user spec 2026-09-23, P5).

SELF-CONTAINED: spawns a throwaway uvicorn on :8903 with an isolated
ANKI_STATE_DIR, a temporary markdown corpus (ANKI_NOTES_DIR) and a DEAD
AnkiConnect URL — the live app (:8901), the live state dir, the real
collection and ~/anki-notes are all untouched.

Covers (round + trace reading card 编辑片段 pencil → SegEditDialog):
  1. pencil icon present + enabled on a dealt reading card
  2. dialog: CM6 lazy chunk loads (.cm-editor), preview renders heading+text,
     editor pane ≈ one three-column-layout column wide
  3. typing updates the preview live (debounce ~150ms)
  4. 插入表格 button → GFM template in source → <table> in preview
  5. 保存 → dialog closes, center card + RIGHT column (原文上下文) show the
     new text, snackbar confirms, on-disk .md rewritten, other sections
     intact, no needs_resync (edit is a transaction, not drift)
  6. image round-trip: toolbar upload (file chooser) → stored in _assets →
     `![](filename)` inserted → preview <img> loads via /api/reading/media
     → Ctrl+Enter saves → card body renders the note image
  7. split → parent container: backend edit guard 409

Run: backend/.venv/bin/python scripts/seg_edit_ui_test.py
(needs playwright + chromium: backend/.venv has playwright; browsers in
~/.cache/ms-playwright)
"""
import base64
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


NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经支配。

## 二、颈筋膜

分为浅、中、深三层，各层之间形成筋膜鞘，容纳血管神经。

## 三、颈动脉三角

境界由胸锁乳突肌前缘、肩胛舌骨肌上腹和二腹肌后腹围成。
"""

A_REL = "2026/解剖/颈部.md"
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
           "nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


def run(notes: Path):
    a_file = notes / A_REL
    png = notes / ".." / "test-upload.png"
    png = png.resolve()
    png.write_bytes(base64.b64decode(PNG_B64))

    # seed: add to the reading list + deal a focus round via API
    api("/api/reading/list/add", "POST", {"path": A_REL})
    d = api("/api/reading/start?mode=focus", "POST")
    check("seed: 3 segments dealt", len(d.get("chunks", [])) == 3, d)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        page.goto(BASE + "/", wait_until="networkidle")

        print("== 1. round renders + pencil icon ==")
        page.wait_for_selector(".reading-chunk-body", timeout=20000)
        # @material/web hoists aria-label → data-aria-label after upgrade
        pencil = page.locator('md-icon-button[data-aria-label="编辑片段"]')
        check("pencil icon present", pencil.count() == 1, pencil.count())
        check("pencil enabled", not pencil.first.evaluate("el => el.disabled"))
        body_text = page.locator(".reading-chunk-body").first.inner_text()
        check("first segment on screen (浅层结构)", "浅层结构" in body_text,
              body_text[:60])

        print("== 2. dialog opens, CM6 lazy chunk loads, preview renders ==")
        pencil.first.click()
        page.wait_for_selector(".seg-edit-dialog", timeout=10000)
        page.wait_for_selector(".seg-edit-dialog .cm-editor", timeout=20000)
        check("cm-editor mounted",
              page.locator(".seg-edit-dialog .cm-editor").count() == 1)
        cm_text = page.locator(".seg-edit-dialog .cm-content").first.inner_text()
        check("CM6 holds the segment source", "浅层结构" in cm_text, cm_text[:60])
        # regression (user report 2026-09-23): CM6 mounted during the dialog
        # open animation cached garbage metrics → gutter line numbers desynced
        # from content (行号在上内容在下), broken scrolling, clipped text.
        # Assert: first gutter number vertically aligned with first content
        # line, and gutter line-count == doc line-count.
        align = page.evaluate("""() => {
            const gutEls = [...document.querySelectorAll('.seg-edit-dialog .cm-lineNumbers .cm-gutterElement')]
                .filter(e => getComputedStyle(e).visibility !== 'hidden');
            const l = document.querySelector('.seg-edit-dialog .cm-line');
            if (!gutEls.length || !l) return null;
            const g = gutEls[0];
            const gb = g.getBoundingClientRect(), lb = l.getBoundingClientRect();
            const lines = document.querySelectorAll('.seg-edit-dialog .cm-content .cm-line').length;
            const sc = document.querySelector('.seg-edit-dialog .cm-scroller');
            return { dTop: Math.abs(gb.top - lb.top), dH: Math.abs(gb.height - lb.height),
                     lines, gutters: gutEls.length, firstNum: g.textContent,
                     display: getComputedStyle(sc).display };
        }""")
        check("gutter #1 aligned with first content line",
              align is not None and align["dTop"] < 3 and align["firstNum"] == "1",
              str(align))
        check("scroller is flex (CM6 base theme mounted in the right root)",
              align is not None and align["display"] == "flex", str(align))
        # scrolling works: the editor scroller must not clip content when the
        # doc is taller than the pane (type enough lines to overflow, scroll
        # to the bottom, last line fully visible)
        page.locator(".seg-edit-dialog .cm-content").click()
        page.keyboard.press("Control+End")
        for _ in range(30):
            page.keyboard.press("Enter")
        page.keyboard.type("滚动到底部标记ABC")
        scrolled = page.evaluate("""() => {
            const s = document.querySelector('.seg-edit-dialog .cm-scroller');
            s.scrollTop = s.scrollHeight;
            return new Promise(r => requestAnimationFrame(() => {
                const lines = [...document.querySelectorAll('.seg-edit-dialog .cm-content .cm-line')];
                const last = lines[lines.length - 1];
                const sb = s.getBoundingClientRect(), lb = last.getBoundingClientRect();
                r({ lastVisible: lb.bottom <= sb.bottom + 2 && lb.height > 4,
                      hasScroll: s.scrollHeight > s.clientHeight });
            }));
        }""")
        check("editor scrolls to bottom, last line fully visible",
              scrolled and scrolled["hasScroll"] and scrolled["lastVisible"], scrolled)
        # undo the scroll-test edits so later steps see the original segment
        page.keyboard.press("Control+z")
        page.wait_for_function(
            "() => !document.querySelector('.seg-edit-dialog .cm-content')"
            "?.textContent.includes('滚动到底部标记ABC')", timeout=5000)
        page.wait_for_selector(".seg-edit-preview h2", timeout=10000)
        pv = page.locator(".seg-edit-preview").first.inner_text()
        check("preview renders heading+text",
              "浅层结构" in pv and "颈阔肌" in pv, pv[:80])
        w = page.locator(".seg-edit-pane--editor").first.bounding_box()["width"]
        check(f"editor pane ≈ column width ({w:.0f}px)", 450 < w < 760, w)

        print("== 3. typing updates the preview (debounce) ==")
        page.locator(".seg-edit-dialog .cm-content").click()
        page.keyboard.press("Control+End")
        page.keyboard.type("\n\n编辑测试标记XYZ。")
        page.wait_for_function(
            "() => document.querySelector('.seg-edit-preview')"
            "?.textContent.includes('编辑测试标记XYZ')",
            timeout=5000)
        check("preview updated after typing", True)

        print("== 4. table button → template → <table> in preview ==")
        page.locator(
            '.seg-edit-toolbar md-icon-button[data-aria-label="插入表格"]'
        ).click()
        page.wait_for_function(
            "() => document.querySelector('.seg-edit-preview table') !== null",
            timeout=5000)
        check("GFM table renders in preview", True)
        cm_text = page.locator(".seg-edit-dialog .cm-content").first.inner_text()
        check("table template in source", "| 列1 | 列2 | 列3 |" in cm_text,
              cm_text[-120:])

        print("== 5. 保存 → card + right column refresh, file on disk ==")
        page.locator('.seg-edit-dialog md-filled-button:has-text("保存")').click()
        page.wait_for_selector(".seg-edit-dialog", state="detached", timeout=10000)
        check("dialog closed after save", True)
        page.wait_for_function(
            "() => document.querySelector('.reading-chunk-body')"
            "?.textContent.includes('编辑测试标记XYZ')",
            timeout=8000)
        check("center card shows edited text", True)
        snack = page.locator(".snackbar, md-snackbar, .md-snackbar").first
        check("save snackbar shown",
              snack.count() > 0 and "保存" in (snack.inner_text() or ""),
              snack.inner_text() if snack.count() else "none")
        # .note-panel exists on BOTH columns — scope to the right one
        page.wait_for_function(
            "() => document.querySelector('.note-column .note-body')"
            "?.textContent.includes('编辑测试标记XYZ')",
            timeout=8000)
        check("right column (原文上下文) refreshed", True)
        disk = a_file.read_text(encoding="utf-8")
        check("file on disk contains edit", "编辑测试标记XYZ" in disk)
        check("file on disk contains table", "| 列1 | 列2 | 列3 |" in disk)
        check("other sections intact", "颈动脉三角" in disk and "颈筋膜" in disk)
        st_after = next(s for s in api("/api/reading/status")["list"]
                        if s["path"] == A_REL)
        check("status: no needs_resync after edit",
              not st_after.get("needs_resync"), st_after)
        check("status: chunk count unchanged (3)",
              st_after["total_chunks"] == 3, st_after)

        print("== 6. image upload round-trip ==")
        page.locator(
            'md-icon-button[data-aria-label="编辑片段"]').first.click()
        page.wait_for_selector(".seg-edit-dialog .cm-editor", timeout=20000)
        with page.expect_file_chooser() as fc:
            page.locator(
                '.seg-edit-toolbar md-icon-button[data-aria-label="插入图片"]'
            ).click()
        fc.value.set_files(str(png))
        page.wait_for_function(
            "() => /!\\[\\]\\(note-/.test(document.querySelector("
            "'.seg-edit-dialog .cm-content')?.textContent || '')",
            timeout=10000)
        check("![](note-…png) inserted into source", True)
        page.wait_for_function(
            "() => { const i = document.querySelector('.seg-edit-preview img');"
            " return i && i.src.includes('/api/reading/media/') && i.naturalWidth > 0 }",
            timeout=10000)
        check("preview img loads via /api/reading/media", True)
        assets = list((notes / "_assets").glob("note-*.png"))
        check("file stored in _assets", len(assets) == 1,
              [str(a) for a in assets])
        # Ctrl+Enter saves (keyboard path)
        page.locator(".seg-edit-dialog .cm-content").click()
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".seg-edit-dialog", state="detached", timeout=10000)
        check("Ctrl+Enter saved + closed", True)
        page.wait_for_function(
            "() => { const i = document.querySelector('.reading-chunk-body img');"
            " return i && i.src.includes('/api/reading/media/') && i.naturalWidth > 0 }",
            timeout=10000)
        check("card body renders the note image", True)
        disk = a_file.read_text(encoding="utf-8")
        check("image ref saved to .md", "![](note-" in disk)

        print("== 7. split → container edit guard ==")
        state = api("/api/reading/state")
        chunks = (state.get("round") or {}).get("chunks") or []
        if chunks:
            sid = chunks[0]["seg_id"]
            r = api("/api/reading/split", "POST", {
                "path": A_REL, "seg_id": sid,
                "selections": [{"start_line": chunks[0]["line_start"],
                                "end_line": chunks[0]["line_start"] + 1}],
                "gap_policy": "bookmark"})
            check("split ok", r.get("ok") is True, r)
            r2 = api("/api/reading/edit", "POST", {
                "path": A_REL, "seg_id": sid, "new_text": "x"})
            check("container edit → 409 (backend guard)",
                  r2.get("_status") == 409, r2)

        browser.close()


def main():
    state = tempfile.mkdtemp(prefix="seg-edit-state-")
    notes = Path(tempfile.mkdtemp(prefix="seg-edit-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / A_REL).write_text(NOTE_A, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",
        "ANKICONNECT_URL": "http://127.0.0.1:18765",  # dead — never touch live
        "ANKI_QUICK_READ": "2",
        "ANKI_FOCUS_READ": "5",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    py = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", "8903"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        st = wait_ready()
        check("backend up on :8903", st.get("anki") in ("ok", "error"), st)
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
