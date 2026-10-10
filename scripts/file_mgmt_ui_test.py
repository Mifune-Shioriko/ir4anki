#!/usr/bin/env python3
"""Playwright UI test: 文件管理 (user spec 2026-10-06).

Verifies the FilesScreen management UI in a real browser: current-directory
strip (upload target), upload buttons + file chooser round-trip, inline row
actions, rename dialog (.md auto-append + 清单 warning), move dialog (folder
picker + self-move disabled), delete dialog (typed-name 防呆: confirm button
stays disabled until the basename matches), and the 409 upload-conflict
dialog with its 跳过/改名/覆盖 retry policies.

SELF-CONTAINED: throwaway uvicorn on an allocated loopback port, isolated ANKI_STATE_DIR, temp
corpus, real isolated pylib collection. Live app/collection/notes untouched.

Run: python3 /tmp/run_ui_test.py scripts/file_mgmt_ui_test.py
"""
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8907"
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


def set_text_field(page, locator_sel: str, text: str):
    """Set an md-outlined-text-field's value the way a user typing would.

    It's a custom element (shadow DOM) — locator.fill() refuses it. The
    host carries the `value` property and Solid's onInput listens for the
    bubbling `input` event, so set value + dispatch input on the host.
    """
    page.locator(locator_sel).first.evaluate(
        """(el, text) => {
            el.value = text
            el.dispatchEvent(new Event('input', { bubbles: true, composed: true }))
        }""", text)


NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经支配。
"""
NOTE_B = """# 上肢

## 一、腋窝

四壁一顶一底。内容：腋动脉、腋静脉、臂丛。
"""
A_REL = "2026/解剖/颈部.md"
B_REL = "2026/解剖/上肢.md"
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
           "nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


def run(notes: Path, uploads: Path):
    js_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: js_errors.append(str(e)))

        print("== 1. 文件 section: tree + management chrome ==")
        page.goto(BASE + "/", wait_until="networkidle")
        page.locator(".nav-rail__item", has_text="文件").click()
        page.wait_for_selector(".files-screen", timeout=15000)
        check("files screen rendered",
              page.locator(".files-tree-card").count() == 1)
        check("upload file button present",
              page.locator(".files-upload-file").count() == 1)
        check("upload folder button present",
              page.locator(".files-upload-dir").count() == 1)
        check("current-dir strip shows root by default",
              "语料库根目录" in page.locator(".files-current-dir").inner_text(),
              page.locator(".files-current-dir").inner_text())
        check("both seeded notes in the tree",
              page.locator(".files-tree-file", has_text="颈部").count() == 1
              and page.locator(".files-tree-file", has_text="上肢").count() == 1)

        print("== 2. folder click = current dir (upload target) ==")
        page.locator(".files-tree-dir", has_text="解剖").first.click()
        page.wait_for_timeout(200)
        cd = page.locator(".files-current-dir").inner_text()
        check("current dir follows the folder click",
              "2026/解剖" in cd, cd)
        check("folder row shows the --current highlight",
              page.locator(".files-tree-dir--current", has_text="解剖").count() >= 1)
        page.locator(".files-current-dir__root").click()
        page.wait_for_timeout(200)
        check("回到根目录 button resets to root",
              "语料库根目录" in page.locator(".files-current-dir").inner_text())

        print("== 3. upload via file chooser lands in the current dir ==")
        # pick 解剖 as the target, then drive the hidden <input type=file>
        page.locator(".files-tree-dir", has_text="解剖").first.click()
        page.wait_for_timeout(200)
        with page.expect_file_chooser() as fc:
            page.locator(".files-upload-file").click()
        fc.value.set_files(str(uploads / "盆腔.md"))
        page.wait_for_selector(".snackbar", timeout=15000)
        snack = page.locator(".snackbar").inner_text()
        check("upload snackbar shows 已上传 1 个文件", "已上传 1" in snack, snack)
        page.wait_for_timeout(4300)  # let the snackbar auto-dismiss
        check("uploaded file appears in the tree under 解剖",
              page.locator(".files-tree-file", has_text="盆腔").count() == 1)
        check("uploaded file is on disk in the target dir",
              (notes / "2026" / "解剖" / "盆腔.md").exists())

        print("== 4. row hover actions + rename dialog ==")
        neck = page.locator(".files-tree-file", has_text="颈部").first
        neck.hover()
        page.wait_for_timeout(300)
        check("hover reveals 3 row actions",
              neck.locator(".files-row-action").count() == 3,
              neck.locator(".files-row-action").count())
        neck.locator('md-icon-button[data-aria-label="重命名"]').click()
        page.wait_for_selector(".files-dialog", timeout=10000)
        check("rename dialog open with the path shown",
              "颈部.md" in page.locator(".files-dialog-path").first.inner_text(),
              page.locator(".files-dialog-path").first.inner_text())
        # 清单 warning only shows for listed files — 颈部 is NOT in the list
        check("rename dialog has NO 清单 note for an unlisted file",
              page.locator(".files-dialog-note").count() == 0)
        tf_sel = ".files-dialog md-outlined-text-field"
        set_text_field(page, tf_sel, "颈前区")
        page.locator('.files-dialog md-filled-button:has-text("重命名")').click()
        page.wait_for_selector(".files-dialog", state="detached", timeout=10000)
        page.wait_for_timeout(600)
        check("renamed file in the tree (.md auto-appended)",
              page.locator(".files-tree-file", has_text="颈前区").count() == 1)
        check("renamed on disk", (notes / "2026" / "解剖" / "颈前区.md").exists())
        check("old name gone from disk", not (notes / A_REL).exists())

        print("== 5. rename dialog scrim-guarded (no accidental close) ==")
        page.locator(".files-tree-file", has_text="颈前区").first.hover()
        page.locator(".files-tree-file", has_text="颈前区").first.locator(
            'md-icon-button[data-aria-label="重命名"]').click()
        page.wait_for_selector(".files-dialog", timeout=10000)
        # click the scrim (far corner of the viewport) → dialog must survive
        page.mouse.click(10, 10)
        page.wait_for_timeout(400)
        check("scrim click does NOT close the rename dialog",
              page.locator(".files-dialog").count() == 1)
        # Escape DOES close (dialog-guard re-implements it)
        page.keyboard.press("Escape")
        page.wait_for_selector(".files-dialog", state="detached", timeout=5000)
        check("Escape closes the rename dialog", True)

        print("== 6. move dialog: folder picker + move ==")
        page.locator(".files-tree-file", has_text="颈前区").first.hover()
        page.locator(".files-tree-file", has_text="颈前区").first.locator(
            'md-icon-button[data-aria-label="移动到"]').click()
        page.wait_for_selector(".files-dialog", timeout=10000)
        check("move dialog lists 根目录 + folders",
              page.locator(".files-move-dir", has_text="根目录").count() == 1
              and page.locator(".files-move-dir", has_text="2026").count() >= 1)
        check("move button disabled until a target is picked",
              page.locator('.files-dialog md-filled-button:has-text("移动")')
              .first.evaluate("el => el.disabled") is True)
        # pick the 2027 folder (created in the fixture)
        page.locator(".files-move-dir", has_text="2027").first.click()
        page.wait_for_timeout(200)
        check("picked folder gets the --pick highlight",
              page.locator(".files-move-dir--pick", has_text="2027").count() == 1)
        page.locator('.files-dialog md-filled-button:has-text("移动")').click()
        page.wait_for_selector(".files-dialog", state="detached", timeout=10000)
        page.wait_for_timeout(600)
        check("file moved on disk to 2027/",
              (notes / "2027" / "颈前区.md").exists())
        check("moved out of 解剖", not (notes / "2026" / "解剖" / "颈前区.md").exists())

        print("== 7. delete dialog: typed-name 防呆 ==")
        page.locator(".files-tree-file", has_text="上肢").first.hover()
        page.locator(".files-tree-file", has_text="上肢").first.locator(
            'md-icon-button[data-aria-label="删除"]').click()
        page.wait_for_selector(".files-dialog", timeout=10000)
        check("delete dialog shows the permanent-delete warning",
              "永久删除" in page.locator(".files-delete-warn").first.inner_text(),
              page.locator(".files-delete-warn").first.inner_text())
        del_btn = page.locator('.files-dialog md-filled-button:has-text("永久删除")')
        check("delete button DISABLED before typing",
              del_btn.first.evaluate("el => el.disabled") is True)
        dtf_sel = ".files-dialog md-outlined-text-field"
        set_text_field(page, dtf_sel, "错的名字.md")
        page.wait_for_timeout(200)
        check("delete button still DISABLED on a wrong name",
              del_btn.first.evaluate("el => el.disabled") is True)
        set_text_field(page, dtf_sel, "上肢.md")
        page.wait_for_timeout(200)
        check("delete button ENABLED once the basename matches",
              del_btn.first.evaluate("el => el.disabled") is False)
        del_btn.click()
        page.wait_for_selector(".files-dialog", state="detached", timeout=10000)
        page.wait_for_timeout(600)
        check("file hard-deleted from disk", not (notes / B_REL).exists())
        check("deleted file gone from the tree",
              page.locator(".files-tree-file", has_text="上肢").count() == 0)

        print("== 8. listed-file delete shows the progress warning ==")
        api("/api/reading/list/add", "POST", {"path": "2026/解剖/盆腔.md"})
        page.reload(wait_until="networkidle")
        page.locator(".nav-rail__item", has_text="文件").click()
        page.wait_for_selector(".files-screen", timeout=15000)
        check("listed file shows the 清单 badge",
              page.locator(".files-tree-badge", has_text="清单").count() >= 1)
        pel = page.locator(".files-tree-file", has_text="盆腔").first
        pel.hover()
        pel.locator('md-icon-button[data-aria-label="删除"]').click()
        page.wait_for_selector(".files-dialog", timeout=10000)
        warn = page.locator(".files-delete-warn--listed")
        check("listed-file delete warns about reading progress",
              warn.count() == 1 and "阅读进度" in warn.inner_text(),
              warn.count() and warn.inner_text())
        check("listed-file delete promises to keep Anki cards",
              "已制的 Anki 卡片会保留" in warn.inner_text(), warn.inner_text())
        page.keyboard.press("Escape")
        page.wait_for_selector(".files-dialog", state="detached", timeout=5000)

        print("== 9. upload .md conflict → dialog with retry policies ==")
        # target dir must be 解剖 where dup.md already lives (reload in §8
        # reset the current dir to root)
        page.locator(".files-tree-dir", has_text="解剖").first.click()
        page.wait_for_timeout(200)
        with page.expect_file_chooser() as fc:
            page.locator(".files-upload-file").click()
        fc.value.set_files(str(uploads / "dup.md"))
        page.wait_for_selector(".files-dialog", timeout=15000)
        check("conflict dialog headline 文件名冲突",
              page.locator(".files-dialog").first.locator(
                  '[slot="headline"]').inner_text() == "文件名冲突")
        check("conflict dialog lists the clashing name",
              page.locator(".files-conflict-item", has_text="dup.md").count() == 1)
        for label in ("跳过重名", "改名保留两者", "覆盖"):
            check(f"conflict dialog offers 「{label}」",
                  page.locator(".files-dialog").first.locator(
                      f'md-outlined-button:has-text("{label}"), '
                      f'md-filled-button:has-text("{label}")').count() == 1)
        page.locator('md-outlined-button:has-text("改名保留两者")').click()
        page.wait_for_selector(".files-dialog", state="detached", timeout=10000)
        page.wait_for_timeout(600)
        check("rename policy created dup (1).md on disk",
              (notes / "2026" / "解剖" / "dup (1).md").exists())

        check("no JS page errors", not js_errors, js_errors[:3])
        browser.close()


from native_fixture import configure, tempdir, run_closed, NativeData, reserve_loopback_port, verify_server, cleanup_after

@cleanup_after
def main():
    state = tempdir(prefix="filemgmt-ui-state-")
    notes = Path(tempdir(prefix="filemgmt-ui-notes-"))
    uploads = Path(tempdir(prefix="filemgmt-ui-uploads-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2027").mkdir(parents=True)
    (notes / A_REL).write_text(NOTE_A, encoding="utf-8")
    (notes / B_REL).write_text(NOTE_B, encoding="utf-8")
    # the note that will clash on upload (same target dir + name)
    (notes / "2026" / "解剖" / "dup.md").write_text(
        "# 已存在的重名笔记\n\n原有内容原有内容。\n", encoding="utf-8")
    # upload payloads (kept OUTSIDE the corpus)
    (uploads / "盆腔.md").write_text(
        "# 盆腔\n\n## 一、盆壁\n\n盆壁由髋骨与盆壁肌围成，内衬盆壁筋膜。\n",
        encoding="utf-8")
    (uploads / "dup.md").write_text(
        "# 新上传的重名笔记\n\n新的内容新的内容。\n", encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",

        "REVIEW_DIST_DIR": os.environ.get("REGRESSION_DIST", str(REPO_ROOT / "frontend" / "dist")),
    })
    py = Path(sys.executable)  # use the test venv, never install into live venv
    configure(env)
    global BASE
    server_port = reserve_loopback_port()
    BASE = f'http://127.0.0.1:{server_port}'
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
         "app:app", "--host", "127.0.0.1", "--port", str(server_port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        verify_server(proc, BASE)
        st = wait_ready()
        check("backend up on an allocated loopback port", st.get("anki") in ("ok", "error"), st)
        run(notes, uploads)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(main())
