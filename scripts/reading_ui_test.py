#!/usr/bin/env python3
"""Playwright UI verification of reading mode (渐进制卡, 2026-09-19).

SELF-CONTAINED: spawns a throwaway uvicorn on :8902 with an isolated
ANKI_STATE_DIR, a temporary markdown corpus (ANKI_NOTES_DIR) and a DEAD
AnkiConnect URL (127.0.0.1:18765) — the live app (:8901), the live state
dir, the real collection and ~/anki-notes are all untouched. Preview mode
is OFF so the funnel is reading → review and no release-budget logic mixes
in.

Covers (2026-09-19 round-2 layout: header icon actions + state-only bottom
row + folder-tree picker + cloze dialog):
  1. gate on: start screen shows the 阅读清单 entry
  2. list manager: TREE picker (folder rows collapse/expand, file rows add),
     reorder buttons render, progress rows
  3. readingStart screen: stats, mode tiles carry 阅读 N from the wire
  4. reading round: header icon buttons 添加卡片/添加挖空, bottom row =
     state buttons ONLY (no 开始制卡 anywhere), chunk markdown + breadcrumb
     + status chip; right panel renders the WHOLE file with anchors
  5. 制卡完成 works straight from 未读 (no active gate); complete advances
  6. refresh mid-round resumes the same chunk
  7. cloze dialog opens (添加挖空 icon) and degrades gracefully with dead
     AnkiConnect (error shown, closable)
  8. skip advances; readingDone shows separated stats; 再读一轮 deals next
  9. narrow viewport: no right column during reading
 10. finish clears the round (backend state)

Run: /tmp/pw-venv2/bin/python ~/anki-review-app/scripts/reading_ui_test.py
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

BASE = "http://127.0.0.1:8902"
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
        return {"_status": e.code}


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

NOTE_B = """# 上肢

## 一、腋窝

四壁一顶一底。内容物包括腋动脉、腋静脉与臂丛各束。

## 二、臂前区

肌皮神经支配喙肱肌、肱二头肌与肱肌三个肌肉。
"""


def main():
    state = tempfile.mkdtemp(prefix="reading-ui-state-")
    notes = Path(tempfile.mkdtemp(prefix="reading-ui-notes-"))
    (notes / "2026" / "解剖").mkdir(parents=True)
    (notes / "2026" / "解剖" / "颈部.md").write_text(NOTE_A, encoding="utf-8")
    (notes / "2026" / "解剖" / "上肢.md").write_text(NOTE_B, encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "ANKI_STATE_DIR": state,
        "ANKI_NOTES_DIR": str(notes),
        "ANKI_READING_MODE": "1",
        "ANKI_PREVIEW_MODE": "0",
        "ANKICONNECT_URL": "http://127.0.0.1:18765",  # dead — never touch live
        "ANKI_QUICK_READ": "2",
        "ANKI_FOCUS_READ": "5",
        "REVIEW_DIST_DIR": "/home/shioriko/anki-review-app/frontend/dist",
    })
    proc = subprocess.Popen(
        ["/home/shioriko/anki-server/review-app/.venv/bin/uvicorn",
         "--app-dir", "/home/shioriko/anki-review-app/backend",
         "app:app", "--host", "127.0.0.1", "--port", "8902"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        run(state)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def run(state):
    st = wait_ready()
    check("backend up on :8902", st.get("anki") in ("ok", "error"), st)
    rs = api("/api/reading/status")
    check("reading flag on + isolated empty list",
          rs.get("reading_mode") is True and rs.get("list") == [], rs)

    a_path = "2026/解剖/颈部.md"
    b_path = "2026/解剖/上肢.md"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")

        # ---- 1. landing screen (dead AnkiConnect → preview shows as the
        # default entry; the 阅读清单 entry lives on start/previewStart, the
        # 管理阅读清单 button on readingStart) ----
        page.wait_for_selector(".screen-title", timeout=30000)
        title = page.locator(".screen-title").first.inner_text()
        print(f"  (landed on: {title})")
        manage = page.locator("md-text-button", has_text="管理阅读清单")
        entry = page.locator("md-text-button", has_text="阅读清单")
        check("阅读清单 entry rendered somewhere",
              manage.count() + entry.count() >= 1,
              (manage.count(), entry.count()))

        # ---- 2. list manager: TREE picker + add + reorder ----
        (manage if manage.count() else entry).first.click()
        page.wait_for_selector(".reading-list-empty", timeout=15000)
        check("empty list placeholder", "清单还是空的" in
              page.locator(".reading-list-empty").inner_text())
        page.locator("md-filled-tonal-button", has_text="添加文件").click()
        page.wait_for_selector(".reading-tree", timeout=15000)
        # tree = folder rows (2026, 解剖) + file rows (颈部, 上肢), all
        # pre-expanded (openPicker expands every folder)
        dirs = page.locator(".reading-tree-dir")
        files = page.locator(".reading-tree-file")
        check("tree renders 2 folder rows", dirs.count() == 2, dirs.count())
        check("tree renders 2 file rows", files.count() == 2, files.count())
        check("folder row shows name",
              dirs.first.inner_text().strip() in ("2026", "解剖"),
              dirs.first.inner_text())
        # collapse/expand: click the DEEPEST folder (解剖) → its files hide
        deep = page.locator(".reading-tree-dir", has_text="解剖").first
        deep.click()
        page.wait_for_timeout(300)
        check("collapsing 解剖 hides its files",
              page.locator(".reading-tree-file").count() == 0,
              page.locator(".reading-tree-file").count())
        deep.click()
        page.wait_for_timeout(300)
        check("re-expanding 解剖 shows files again",
              page.locator(".reading-tree-file").count() == 2)
        # already-added files show 已加入 — add BOTH via their row buttons
        # (dialog stays open across adds for batch adding)
        page.locator(".reading-tree-file", has_text="颈部").locator(
            "md-text-button", has_text="加入清单").click()
        page.wait_for_timeout(700)
        added = page.locator(".reading-tree-file", has_text="颈部").inner_text()
        check("added file row shows 已加入", "已加入" in added, added)
        page.locator(".reading-tree-file", has_text="上肢").locator(
            "md-text-button", has_text="加入清单").click()
        page.wait_for_timeout(700)
        page.locator("md-text-button", has_text="关闭").click()
        page.wait_for_timeout(400)
        rows = page.locator(".reading-list-item")
        check("both files in list", rows.count() == 2, rows.count())
        # row progress by name (add order = 颈部 then 上肢)
        neck_row = page.locator(".reading-list-item", has_text="颈部").first
        arm_row = page.locator(".reading-list-item", has_text="上肢").first
        check("颈部 row 进度 0 / 3", "进度 0 / 3" in neck_row.inner_text(),
              neck_row.inner_text())
        check("上肢 row 进度 0 / 2", "进度 0 / 2" in arm_row.inner_text(),
              arm_row.inner_text())
        check("frontier label 未读", "未读" in neck_row.inner_text())
        # md-icon-button is a custom element: disabled is a PROPERTY — read
        # it via evaluate
        top_disabled = rows.first.locator(
            'md-icon-button[data-aria-label="置顶"]').evaluate("el => el.disabled")
        check("置顶 button disabled for first row", top_disabled is True, top_disabled)
        # move 上肢 to the top → rounds deal 上肢 first
        arm_row.locator('md-icon-button[data-aria-label="置顶"]').click()
        page.wait_for_timeout(700)
        first_title = page.locator(".reading-list-item__title").first.inner_text()
        check("置顶 reorders list (上肢 now first)", "上肢" in first_title, first_title)
        page.screenshot(path="/tmp/reading-ui-list.png")
        page.locator("md-text-button", has_text="返回").click()
        page.wait_for_selector(".screen-title", timeout=15000)

        # ---- 3. readingStart screen ----
        page.wait_for_timeout(800)
        title = page.locator(".screen-title").first.inner_text()
        check("funnel lands on 渐进制卡 start", "渐进制卡" in title, title)
        tiles = page.locator(".mode-tile")
        check("mode tiles render", tiles.count() == 2, tiles.count())
        check("quick tile shows 阅读 2 (wire)",
              "阅读 2" in tiles.nth(0).inner_text(), tiles.nth(0).inner_text())
        check("focus tile shows 阅读 5 (wire)",
              "阅读 5" in tiles.nth(1).inner_text(), tiles.nth(1).inner_text())
        page.screenshot(path="/tmp/reading-ui-start.png")

        # ---- 4. reading round: two columns, round-2 action layout ----
        page.locator("md-filled-button", has_text="开始阅读").click()
        page.wait_for_selector(".reading-crumb", timeout=20000)
        crumb = page.locator(".reading-crumb").inner_text()
        check("crumb = 上肢 first (置顶 priority)", "上肢" in crumb, crumb)
        body = page.locator(".reading-chunk-body").inner_text()
        check("chunk text rendered (markdown → text)", "腋动脉" in body, body[:80])
        check("status chip 未读", page.locator(".reading-status-chip",
                                              has_text="未读").count() >= 1)
        # header icon actions (添加卡片 / 添加挖空) — aria-label hoists to
        # data-aria-label after element upgrade
        check("header: 添加卡片 icon button", page.locator(
            '.card-header-actions md-icon-button[data-aria-label="添加卡片"]').count() == 1)
        check("header: 添加挖空 icon button", page.locator(
            '.card-header-actions md-icon-button[data-aria-label="添加挖空"]').count() == 1)
        # bottom row = state buttons ONLY; 开始制卡 must be GONE everywhere
        check("开始制卡 is gone", page.locator("md-filled-button",
                                          has_text="开始制卡").count() == 0)
        check("bottom: 制卡完成 filled", page.locator(
            "md-filled-button", has_text="制卡完成").count() == 1)
        check("bottom: 无需制卡，跳过 text", page.locator(
            "md-text-button", has_text="无需制卡，跳过").count() == 1)
        check("bottom: 下一张（稍后继续） text", page.locator(
            "md-text-button", has_text="下一张").count() == 1)
        check("bottom: 结束阅读 text", page.locator(
            "md-text-button", has_text="结束阅读").count() == 1)
        check("no 添加卡片 button in bottom row", page.locator(
            ".action-area md-outlined-button").count() == 0)
        # right panel: whole file, anchored
        page.wait_for_selector(".note-panel .note-body", timeout=20000)
        right = page.locator(".note-panel .note-body").inner_text()
        check("right panel = WHOLE file (all sections)",
              "腋动脉" in right and "臂前区" in right, right[:100])
        anchors = page.locator(".note-panel .note-body [data-src-line]")
        check("right panel has source anchors", anchors.count() >= 2, anchors.count())
        page.screenshot(path="/tmp/reading-ui-card.png")

        # ---- 5. 制卡完成 straight from 未读 (no active gate) ----
        page.locator("md-filled-button", has_text="制卡完成").click()
        page.wait_for_timeout(1200)
        crumb2 = page.locator(".reading-crumb").inner_text()
        check("complete from todo advances to 颈部 一", "颈部" in crumb2, crumb2)
        check("progress strip shows 1/2",
              "1/2" in page.locator(".progress-text").inner_text(),
              page.locator(".progress-text").inner_text())

        # ---- 6. refresh resumes the round on the pending chunk ----
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".reading-crumb", timeout=30000)
        check("reload resumes reading round",
              "颈部" in page.locator(".reading-crumb").inner_text(),
              page.locator(".reading-crumb").inner_text())
        check("reload keeps progress 1/2",
              "1/2" in page.locator(".progress-text").inner_text())

        # ---- 7. cloze dialog opens; dead AnkiConnect → graceful error ----
        page.locator('.card-header-actions md-icon-button[data-aria-label="添加挖空"]').click()
        page.wait_for_selector(".cloze-dialog", timeout=15000)
        check("cloze dialog headline",
              "添加挖空卡" in page.locator(".cloze-dialog").inner_text())
        page.wait_for_timeout(1500)  # let the addInfo fetch fail
        dlg_text = page.locator(".cloze-dialog").inner_text()
        check("cloze dialog degrades gracefully (error, no crash)",
              "HTTP 500" in dlg_text or "加载失败" in dlg_text
              or "后端响应异常" in dlg_text or "失败" in dlg_text, dlg_text[:200])
        check("cloze dialog falls back to 文字 field",
              "挖空正文" in dlg_text, dlg_text[:200])
        check("cloze toolbar renders (挖空选中 / 取消挖空)",
              "挖空选中" in dlg_text and "取消挖空" in dlg_text)
        check("cloze preview columns render",
              "正面（提问）" in dlg_text and "背面（答案）" in dlg_text)
        page.screenshot(path="/tmp/reading-ui-cloze.png")
        page.locator(".cloze-dialog md-text-button", has_text="取消").last.click()
        page.wait_for_timeout(500)
        check("cloze dialog closes", page.locator(".cloze-dialog").count() == 0)
        check("still on the same chunk after closing dialog",
              "颈部" in page.locator(".reading-crumb").inner_text())

        # ---- 8. skip → round done → separated stats ----
        page.locator("md-text-button", has_text="无需制卡，跳过").click()
        page.wait_for_selector(".screen-title", timeout=15000)
        page.wait_for_timeout(600)
        title = page.locator(".screen-title").first.inner_text()
        check("readingDone screen", "阅读完成" in title, title)
        stats_txt = page.locator(".screen-stats").first.inner_text()
        check("stats separate 完成/跳过",
              "1 段" in stats_txt and "制卡完成" in stats_txt and "跳过" in stats_txt,
              stats_txt)
        page.screenshot(path="/tmp/reading-ui-done.png")

        # 再读一轮: round 2 = [上肢 二, 颈部 二] (breadth, list order)
        page.locator("md-text-button", has_text="再读一轮").click()
        page.wait_for_selector(".reading-crumb", timeout=20000)
        crumb3 = page.locator(".reading-crumb").inner_text()
        check("round 2 deals 上肢 二、臂前区", "臂前区" in crumb3, crumb3)
        # skip both to drain the round
        for _ in range(2):
            skip_btn = page.locator("md-text-button", has_text="跳过").last
            if skip_btn.count():
                skip_btn.click()
                page.wait_for_timeout(900)
            if page.locator(".reading-crumb").count() == 0:
                break

        # ---- 9. narrow viewport: no right column. From readingDone, set
        # narrow FIRST, then 再读一轮 (round 3 = 颈部三 only; 上肢 drained) ----
        page.set_viewport_size({"width": 414, "height": 900})
        page.wait_for_timeout(500)
        btn = page.locator("md-text-button", has_text="再读一轮")
        check("readingDone offers 再读一轮", btn.count() == 1, btn.count())
        if btn.count():
            btn.click()
            page.wait_for_selector(".reading-crumb", timeout=20000)
            check("narrow: chunk card renders",
                  page.locator(".reading-chunk-body").count() == 1)
            crumb4 = page.locator(".reading-crumb").inner_text()
            check("round 3 = 颈部三 颈动脉三角", "颈动脉三角" in crumb4, crumb4)
            check("narrow: NO right column",
                  page.locator(".note-column").count() == 0,
                  page.locator(".note-column").count())
            page.screenshot(path="/tmp/reading-ui-narrow.png")

        # ---- 10. finish clears the round on the backend ----
        page.set_viewport_size({"width": 1280, "height": 900})
        r = api("/api/reading/finish", method="POST")
        check("finish clears round", r.get("ok") is True, r)
        r = api("/api/reading/state")
        check("state round null after finish", r.get("round") is None, r.get("round"))

        # list manager reflects progress: 颈部 = 2 skipped (一二), 上肢 =
        # 1 done (一) + 1 skipped (二)
        r = api("/api/reading/status")
        by = {s["path"]: s for s in r["list"]}
        check("颈部 progress: skipped=2 done=0",
              by[a_path]["done"] == 0 and by[a_path]["skipped"] == 2, by[a_path])
        check("上肢 progress: done=1 skipped=1",
              by[b_path]["done"] == 1 and by[b_path]["skipped"] == 1, by[b_path])

        check("no JS page errors", not errors, errors[:3])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
