#!/usr/bin/env python3
"""Playwright UI verification of reading mode (渐进制卡, 2026-09-19).

SELF-CONTAINED: spawns a throwaway uvicorn on :8902 with an isolated
ANKI_STATE_DIR, a temporary markdown corpus (ANKI_NOTES_DIR) and a DEAD
AnkiConnect URL (127.0.0.1:18765) — the live app (:8901), the live state
dir, the real collection and ~/anki-notes are all untouched. Preview mode
is OFF so the funnel is reading → review and no release-budget logic mixes
in.

Covers (single daily pipeline, user spec 2026-09-24 + round-3 layout: LEFT
NAV RAIL + progress RING + 文件 browser):
  1. nav rail: three destinations (学习/文件/阅读清单), NO old top bar
  2. 阅读清单 section: TREE picker (folder rows collapse/expand, file rows
     add), reorder buttons render, progress rows (whole-file seeding = 0/1)
  3. 文件 section: folder tree + read-only file viewer (markdown renders)
  4. unified start screen: NO mode tiles (quick/focus retired), today's plan
     from the wire, single 开始 button
  5. 开始 → reading stage: header icon buttons 添加卡片/添加挖空, bottom row =
     state buttons ONLY, WHOLE-FILE chunk card + status chip; right panel
     renders the file with anchors; progress RING in the rail footer
  6. 制卡完成 works straight from 未读 (no active gate); complete advances
  7. refresh mid-round resumes the same chunk
  8. cloze dialog opens (添加挖空 icon) and degrades gracefully with dead
     AnkiConnect (error shown, closable)
  9. narrow viewport: no right column during reading
 10. draining the reading round AUTO-CHAINS into the next stage (no
     readingDone stats page; the chain's deal fails against the dead
     AnkiConnect, proving the transition fired)
 11. backend state after the chain: reading round no longer active; list
     progress updated (1 segment per file)

Run: python scripts/reading_ui_test.py   (needs playwright + the backend venv deps)
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
        "ANKI_DAILY_READ": "4",
        "REVIEW_DIST_DIR": str(REPO_ROOT / "frontend" / "dist"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "--app-dir", str(REPO_ROOT / "backend"),
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

        # ---- 1. nav rail (round 3): three destinations, old top bar gone ----
        page.wait_for_selector(".nav-rail", timeout=30000)
        items = page.locator(".nav-rail__item")
        check("nav rail renders 3 destinations", items.count() == 3, items.count())
        rail_text = page.locator(".nav-rail").inner_text()
        check("rail labels 学习/文件/阅读清单",
              "学习" in rail_text and "文件" in rail_text and "阅读清单" in rail_text,
              rail_text)
        check("old Anki top bar is gone", page.locator(".top-bar").count() == 0)
        check("学习 destination starts active",
              "nav-rail__item--active" in items.nth(0).get_attribute("class"))
        title = page.locator(".screen-title").first.inner_text()
        print(f"  (study funnel landed on: {title})")
        # start screens stay SINGLE-column (2026-09-22 gating; the old
        # note-panel top-edge alignment check died with the idle placeholder)
        check("start screen: no note column (single-column idle)",
              page.locator(".note-column").count() == 0)
        check("no ring on start screens (no active round)",
              page.locator(".progress-ring").count() == 0)

        # ---- 2. 文件 section: tree + read-only viewer ----
        items.nth(1).click()  # 文件
        page.wait_for_selector(".files-tree", timeout=15000)
        fdirs = page.locator(".files-tree-dir")
        ffiles = page.locator(".files-tree-file")
        check("files tree renders folders", fdirs.count() >= 2, fdirs.count())
        check("files tree renders 2 notes", ffiles.count() == 2, ffiles.count())
        check("viewer starts empty (no file selected)",
              page.locator(".files-viewer-empty").count() == 1)
        page.locator(".files-tree-file", has_text="颈部").click()
        page.wait_for_selector(".files-viewer-body", timeout=15000)
        viewer = page.locator(".files-viewer-body").inner_text()
        check("viewer renders the WHOLE file",
              "颈阔肌" in viewer and "颈动脉三角" in viewer, viewer[:100])
        check("viewer markdown → headings rendered",
              page.locator(".files-viewer-body h2").count() >= 2)
        check("viewer crumb shows the path",
              "颈部" in page.locator(".files-viewer-crumb").inner_text())
        check("文件 destination now active",
              "nav-rail__item--active" in items.nth(1).get_attribute("class"))
        page.screenshot(path="/tmp/reading-ui-files.png")

        # ---- 3. 阅读清单 section: TREE picker + add + reorder ----
        items.nth(2).click()  # 阅读清单
        page.wait_for_selector(".reading-list-empty", timeout=15000)
        check("empty list placeholder", "清单还是空的" in
              page.locator(".reading-list-empty").inner_text())
        check("no 返回 button (rail section, not a phase)",
              page.locator("md-text-button", has_text="返回").count() == 0)
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
        # rail badge shows the list size (round 3)
        check("rail 阅读清单 badge shows (2)",
              "(2)" in page.locator(".nav-rail").inner_text(),
              page.locator(".nav-rail").inner_text())
        # row progress by name (add order = 颈部 then 上肢). Whole-file
        # seeding (2026-09-24): each file = ONE segment until split.
        neck_row = page.locator(".reading-list-item", has_text="颈部").first
        arm_row = page.locator(".reading-list-item", has_text="上肢").first
        check("颈部 row 进度 0 / 1 (whole-file seed)",
              "进度 0 / 1" in neck_row.inner_text(),
              neck_row.inner_text())
        check("上肢 row 进度 0 / 1 (whole-file seed)",
              "进度 0 / 1" in arm_row.inner_text(),
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
        # back to the study funnel via the rail (the old 返回 button is gone)
        items.nth(0).click()  # 学习
        page.wait_for_selector(".screen-title", timeout=15000)

        # ---- 3. unified start screen (single daily pacing, 2026-09-24) ----
        page.wait_for_timeout(800)
        title = page.locator(".screen-title").first.inner_text()
        check("funnel lands on the unified 开始学习 start screen",
              "开始学习" in title, title)
        # the old per-stage start screens + mode tiles are retired
        check("mode tiles are gone", page.locator(".mode-tile").count() == 0)
        check("渐进制卡 start screen is gone",
              page.locator(".screen-title", has_text="渐进制卡").count() == 0)
        plan = page.locator(".screen-stats").first.inner_text()
        check("plan shows 阅读 4 段 (wire)", "阅读" in plan and "4 段" in plan, plan)
        check("single 开始 button",
              page.locator("md-filled-button", has_text="开始").count() == 1)
        page.screenshot(path="/tmp/reading-ui-start.png")

        # ---- 4. 开始 → reading stage: WHOLE-FILE segments (no pre-chunking),
        # 上肢 first (置顶 priority). preview mode off → the chain after
        # reading goes straight to the review stage.
        page.locator("md-filled-button", has_text="开始").click()
        page.wait_for_selector(".reading-crumb", timeout=20000)
        crumb = page.locator(".reading-crumb").inner_text()
        check("crumb = 上肢 first (置顶 priority)", "上肢" in crumb, crumb)
        body = page.locator(".reading-chunk-body").inner_text()
        check("chunk = WHOLE file (both sections in one card)",
              "腋动脉" in body and "臂前区" in body, body[:120])
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
        # 下一张: text only, NO leading icon (user 2026-09-20)
        check("下一张 button has no icon", page.locator(
            "md-text-button", has_text="下一张").first.locator("md-icon").count() == 0)
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
        # progress RING (2026-09-20): moved into the nav rail's bottom-left
        # corner; the strip above the columns is gone entirely
        check("progress ring rendered", page.locator(".progress-ring").count() == 1)
        check("ring lives in the nav rail footer",
              page.locator(".nav-rail__footer .progress-ring").count() == 1)
        check("ring shows 0/2", "0/2" in
              page.locator(".progress-ring__text").inner_text(),
              page.locator(".progress-ring__text").inner_text())
        check("round strip is gone", page.locator(".round-strip").count() == 0)
        # ring really sits at the bottom of the rail (viewport bottom-left)
        ring_box = page.locator(".progress-ring").bounding_box()
        rail_box = page.locator(".nav-rail").bounding_box()
        check("ring near rail bottom (within 120px of rail bottom edge)",
              rail_box["y"] + rail_box["height"] - (ring_box["y"] + ring_box["height"]) < 120,
              f"rail={rail_box} ring={ring_box}")
        check("ring inside rail's horizontal bounds",
              ring_box["x"] >= rail_box["x"] and
              ring_box["x"] + ring_box["width"] <= rail_box["x"] + rail_box["width"] + 1,
              f"rail={rail_box} ring={ring_box}")
        # top-edge alignment: card box and note panel share the same top
        card_top = page.locator(".flashcard-wrapper md-elevated-card").bounding_box()["y"]
        note_top = page.locator(".note-column .note-panel").first.bounding_box()["y"]
        check("card/note top edges aligned (mid-round)", abs(card_top - note_top) <= 2,
              f"card_top={card_top} note_top={note_top}")
        page.screenshot(path="/tmp/reading-ui-card.png")

        # ---- 5. 制卡完成 straight from 未读 (no active gate) ----
        page.locator("md-filled-button", has_text="制卡完成").click()
        page.wait_for_timeout(1200)
        crumb2 = page.locator(".reading-crumb").inner_text()
        check("complete from todo advances to 颈部 一", "颈部" in crumb2, crumb2)
        check("progress ring shows 1/2",
              "1/2" in page.locator(".progress-ring__text").inner_text(),
              page.locator(".progress-ring__text").inner_text())

        # ---- 6. refresh resumes the round on the pending chunk ----
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".reading-crumb", timeout=30000)
        check("reload resumes reading round",
              "颈部" in page.locator(".reading-crumb").inner_text(),
              page.locator(".reading-crumb").inner_text())
        check("reload keeps progress 1/2",
              "1/2" in page.locator(".progress-ring__text").inner_text())

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

        # ---- 8. narrow viewport: no right column while reading ----
        page.set_viewport_size({"width": 414, "height": 900})
        page.wait_for_timeout(500)
        check("narrow: chunk card renders",
              page.locator(".reading-chunk-body").count() == 1)
        check("narrow: NO right column",
              page.locator(".note-column").count() == 0,
              page.locator(".note-column").count())
        page.screenshot(path="/tmp/reading-ui-narrow.png")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_timeout(300)

        # ---- 9. skip the last chunk → round drains → AUTO-CHAIN into the
        # next stage (2026-09-24: no readingDone stats page). preview off →
        # the chain lands on the review deal, which fails against the dead
        # AnkiConnect — the error screen PROVES the chain fired.
        page.locator("md-text-button", has_text="无需制卡，跳过").click()
        page.wait_for_function(
            "() => document.body.textContent.includes('加载失败')", timeout=20000)
        check("reading drained → chained into review stage (load error vs dead Anki)",
              page.locator(".screen-title", has_text="阅读完成").count() == 0)
        page.screenshot(path="/tmp/reading-ui-chain.png")

        # ---- 10. backend state: round cleared by the chain's finish ----
        r = api("/api/reading/state")
        rd_state = r.get("round")
        check("reading round no longer active",
              rd_state is None or rd_state.get("status") != "active", rd_state)

        # list manager reflects progress: whole-file segments — 颈部 = 1
        # skipped, 上肢 = 1 done (one segment each, 2026-09-24 seeding)
        r = api("/api/reading/status")
        by = {s["path"]: s for s in r["list"]}
        check("颈部 progress: skipped=1 done=0",
              by[a_path]["done"] == 0 and by[a_path]["skipped"] == 1, by[a_path])
        check("上肢 progress: done=1 skipped=0",
              by[b_path]["done"] == 1 and by[b_path]["skipped"] == 0, by[b_path])

        check("no JS page errors", not errors, errors[:3])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
