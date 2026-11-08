#!/usr/bin/env python3
"""Tests for the split stale-coordinate guard + empty-selection fix (2026-09-30).

Same harness as reading_test.py: real FastAPI app against a real isolated pylib collection
and a TEMPORARY corpus/state — the live app, ~/anki-notes and reading.db are
untouched.

Covers /api/reading/split:
  1. happy path WITH guard fields (fingerprint + line_start match) → 200
  2. stale fingerprint → 409 SPLIT_STALE
  3. stale line_start → 409 SPLIT_STALE
  4. guard fields omitted (old client) → still splits (backwards compatible)
  5. external .md edit (pure line shift ABOVE the segment) → split re-anchors
     and cuts the RIGHT lines (no fingerprint/line_start sent = fuse path)
  6. external CONTENT edit of the segment itself → needs_resync → 409
  7. empty selection (blank lines only) → 400 「选区没有实质内容」
  8. heading-only selection (< MIN_READING_BODY body) → 400
  9. selection with trailing blank lines → trimmed (child ends on real text)
 10. chunk payload + /api/reading/source both carry `fingerprint`

Run: backend/.venv/bin/python scripts/split_guard_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from native_fixture import configure, tempdir, run_closed, NativeData

STATE = tempdir(prefix="split-guard-state-")
NOTES = Path(tempdir(prefix="split-guard-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_READING_MODE"] = "1"
# single "daily" tier since 2026-09-24 — ANKI_DAILY_READ is the live knob
# (legacy ANKI_FOCUS_READ/QUICK are ignored). 50 = deal everything dealable.
os.environ["ANKI_DAILY_READ"] = "50"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402
configure()
import app as backend  # noqa: E402

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


backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")


def write_note(rel, text):
    p = NOTES / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# A note with a multi-line paragraph so line-level cuts and blank trimming
# are meaningful. Lines (1-based):
#   1 # 标题
#   2 (blank)
#   3 ## 小节一
#   4 (blank)
#   5 aaa 第一句内容在这一行
#   6 bbb 第二句内容在这一行
#   7 ccc 第三句内容在这一行
#   8 (blank)
#   9 ## 小节二
#  10 (blank)
#  11 ddd 另一节的正文内容
NOTE = (
    "# 标题\n"
    "\n"
    "## 小节一\n"
    "\n"
    "aaa 第一句内容在这一行\n"
    "bbb 第二句内容在这一行\n"
    "ccc 第三句内容在这一行\n"
    "\n"
    "## 小节二\n"
    "\n"
    "ddd 另一节的正文内容\n"
)
A = "2026/解剖/测试.md"


async def add_and_deal(c):
    """Add the file (whole-file seed) and deal a round; return the single
    whole-file chunk payload."""
    await c.post("/api/reading/list/add", json={"path": A})
    r = await c.post("/api/reading/start?mode=focus")
    chunks = r.json().get("chunks", [])
    return chunks[0] if chunks else None


async def main():
    write_note(A, NOTE)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("== 0. seed: whole-file deal + payload carries fingerprint ==")
        ch = await add_and_deal(c)
        check("dealt 1 whole-file chunk", ch is not None, ch)
        check("chunk payload has fingerprint",
              bool(ch and ch.get("fingerprint")), ch and ch.get("fingerprint"))
        check("chunk line_start == 1", ch and ch["line_start"] == 1, ch)
        fp = ch["fingerprint"]
        sid = ch["seg_id"]

        print("== obsolete policy rejects without persistence ==")
        for policy in ("extract", "unknown"):
            before = {str(p): p.read_bytes() for p in Path(STATE).rglob("*") if p.is_file()}
            r = await c.post("/api/reading/split", json={
                "path": A, "seg_id": sid,
                "selections": [{"start_line": 5, "end_line": 7}],
                "gap_policy": policy})
            check(f"{policy} rejected at validation", r.status_code == 422, r.text)
            after = {str(p): p.read_bytes() for p in Path(STATE).rglob("*") if p.is_file()}
            check(f"{policy} left persisted state unchanged", before == after)

        print("== 1. happy path with matching guard → 200 ==")
        # cut lines 5-7 (the aaa/bbb/ccc paragraph body) — bookmark keeps the
        # tail todo. Guard echoes the parent fingerprint + line_start.
        r = await c.post("/api/reading/split", json={
            "path": A, "seg_id": sid,
            "selections": [{"start_line": 5, "end_line": 7}],
            "gap_policy": "bookmark",
            "fingerprint": fp, "line_start": ch["line_start"],
        })
        check("split with matching guard 200", r.status_code == 200, r.text[:200])
        kids = r.json().get("children", [])
        todo = [k for k in kids if k["status"] == "todo" and not k.get("tail")]
        check("one selected todo child (5-7)",
              len(todo) == 1 and todo[0]["start_line"] == 5 and todo[0]["end_line"] == 7,
              kids)

        print("== 2/3. stale fingerprint / line_start → 409 ==")
        # the parent is now a container; use a fresh file to exercise the guard
        # on a live parent. Re-add a second file.
        write_note("2026/解剖/测试2.md", NOTE)
        B = "2026/解剖/测试2.md"
        await c.post("/api/reading/list/add", json={"path": B})
        # finish the current round so start deals B cleanly
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        bch = next((x for x in r.json().get("chunks", []) if x["path"] == B), None)
        check("B dealt", bch is not None, r.json().get("chunks"))
        if bch:
            bsid, bfp, bls = bch["seg_id"], bch["fingerprint"], bch["line_start"]
            r = await c.post("/api/reading/split", json={
                "path": B, "seg_id": bsid,
                "selections": [{"start_line": 5, "end_line": 7}],
                "fingerprint": "deadbeef" * 4, "line_start": bls,
            })
            check("stale fingerprint → 409", r.status_code == 409, r.text[:120])
            check("409 detail is SPLIT_STALE",
                  r.status_code == 409 and "stale" in (r.json().get("detail", "")),
                  r.json() if r.status_code == 409 else "")
            r = await c.post("/api/reading/split", json={
                "path": B, "seg_id": bsid,
                "selections": [{"start_line": 5, "end_line": 7}],
                "fingerprint": bfp, "line_start": bls + 99,
            })
            check("stale line_start → 409", r.status_code == 409, r.text[:120])

        print("== 4. guard fields omitted (old client) → still splits ==")
        r = await c.post("/api/reading/split", json={
            "path": B, "seg_id": bsid,
            "selections": [{"start_line": 5, "end_line": 7}],
            "gap_policy": "bookmark",
        })
        check("no-guard split 200 (backwards compatible)",
              r.status_code == 200, r.text[:160])

        print("== 5. external pure-shift edit → split re-anchors, cuts right lines ==")
        # prepend 3 lines to a THIRD file (pure shift below → fingerprint intact)
        write_note("2026/解剖/测试3.md", NOTE)
        C = "2026/解剖/测试3.md"
        await c.post("/api/reading/list/add", json={"path": C})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        cch = next((x for x in r.json().get("chunks", []) if x["path"] == C), None)
        csid = cch["seg_id"]
        # external edit: insert 3 comment-ish lines at the TOP (before line 1)
        shifted = "插入行一\n插入行二\n插入行三\n" + NOTE
        write_note(C, shifted)
        # split WITHOUT guard: the fuse must re-anchor the whole-file segment
        # down by 3 lines, so selecting the aaa..ccc body (now at 8-10) cuts it.
        # We send the OLD relative intent as absolute NEW lines (8-10).
        r = await c.post("/api/reading/split", json={
            "path": C, "seg_id": csid,
            "selections": [{"start_line": 8, "end_line": 10}],
            "gap_policy": "bookmark",
        })
        check("shifted split 200", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            todo = [k for k in r.json()["children"]
                    if k["status"] == "todo" and not k.get("tail")]
            check("re-anchored cut = lines 8-10",
                  len(todo) == 1 and todo[0]["start_line"] == 8
                  and todo[0]["end_line"] == 10, r.json()["children"])
            # verify the cut text is really aaa..ccc in the SHIFTED file
            lines = shifted.split("\n")
            cut = "\n".join(lines[7:10])
            check("cut text is the aaa/bbb/ccc body",
                  "aaa" in cut and "ccc" in cut, cut)

        print("== 6. external CONTENT edit of the segment → needs_resync 409 ==")
        write_note("2026/解剖/测试4.md", NOTE)
        D = "2026/解剖/测试4.md"
        await c.post("/api/reading/list/add", json={"path": D})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        dch = next((x for x in r.json().get("chunks", []) if x["path"] == D), None)
        dsid = dch["seg_id"]
        # rewrite the whole body (content, not a pure shift) → fingerprint lost
        write_note(D, "# 完全不一样\n\n整篇内容都被替换掉了，无法重锚定。\n")
        r = await c.post("/api/reading/split", json={
            "path": D, "seg_id": dsid,
            "selections": [{"start_line": 1, "end_line": 2}],
        })
        check("content-edited file → 409 (needs resync or stale)",
              r.status_code == 409, r.text[:160])

        print("== 7/8. empty / heading-only selection → 400 ==")
        write_note("2026/解剖/测试5.md", NOTE)
        E = "2026/解剖/测试5.md"
        await c.post("/api/reading/list/add", json={"path": E})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        ech = next((x for x in r.json().get("chunks", []) if x["path"] == E), None)
        esid = ech["seg_id"]
        # line 8 is blank (between the paragraph and 小节二 heading)
        r = await c.post("/api/reading/split", json={
            "path": E, "seg_id": esid,
            "selections": [{"start_line": 8, "end_line": 8}],
        })
        check("blank-line selection → 400", r.status_code == 400, r.text[:160])
        check("400 mentions 选区没有实质内容",
              r.status_code == 400 and "实质内容" in r.json().get("detail", ""),
              r.json() if r.status_code == 400 else "")
        # line 3 is a heading only (## 小节一) — body < MIN_READING_BODY
        r = await c.post("/api/reading/split", json={
            "path": E, "seg_id": esid,
            "selections": [{"start_line": 3, "end_line": 3}],
        })
        check("heading-only selection → 400", r.status_code == 400, r.text[:160])

        print("== 9. selection with trailing blank lines → trimmed ==")
        r = await c.post("/api/reading/split", json={
            "path": E, "seg_id": esid,
            # 5-8: the aaa..ccc body PLUS the trailing blank line 8
            "selections": [{"start_line": 5, "end_line": 8}],
            "gap_policy": "bookmark",
        })
        check("trailing-blank split 200", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            todo = [k for k in r.json()["children"]
                    if k["status"] == "todo" and not k.get("tail")]
            check("child trimmed to 5-7 (blank line 8 dropped)",
                  len(todo) == 1 and todo[0]["start_line"] == 5
                  and todo[0]["end_line"] == 7, r.json()["children"])

        print("== 10. /api/reading/source carries fingerprint ==")
        # make a card against a live segment, then look up its source
        write_note("2026/解剖/测试6.md", NOTE)
        F = "2026/解剖/测试6.md"
        await c.post("/api/reading/list/add", json={"path": F})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        fch = next((x for x in r.json().get("chunks", []) if x["path"] == F), None)
        r = await c.post("/api/card/add", json={
            "fields": {"正面": "Q源片段", "背面": "A"},
            "tags": [],
            "reading_source": {"path": F, "chunk_key": str(fch["seg_id"])},
        })
        nid = r.json().get("noteId")
        check("card added with provenance", bool(nid), r.json())
        if nid:
            r = await c.get(f"/api/reading/source?note_id={nid}")
            check("source has fingerprint",
                  r.status_code == 200 and bool(r.json().get("fingerprint")),
                  r.json().get("fingerprint") if r.status_code == 200 else r.text[:120])
            check("source fingerprint == chunk fingerprint",
                  r.status_code == 200
                  and r.json().get("fingerprint") == fch.get("fingerprint"))

        print("== 11. reading_reseed & nested bottom-up container re-anchoring ==")
        # G starts whole (1..11), split twice to form a 2-level container hierarchy:
        # root container (1..11) -> child todo (5..7) + tail container (8..11) -> leaf (9..11)
        write_note("2026/解剖/测试7.md", NOTE)
        G = "2026/解剖/测试7.md"
        await c.post("/api/reading/list/add", json={"path": G})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        gch = next((x for x in r.json().get("chunks", []) if x["path"] == G), None)
        gsid = gch["seg_id"]
        # Split 1: select lines 5..7 -> tail is lines 8..11
        r1 = await c.post("/api/reading/split", json={
            "path": G, "seg_id": gsid,
            "selections": [{"start_line": 5, "end_line": 7}],
        })
        check("G split 1 ok", r1.status_code == 200, r1.text[:120])
        tail1 = next(x for x in r1.json()["children"] if x.get("tail"))
        # Split 2 on tail1 (8..11): select lines 9..11
        r2 = await c.post("/api/reading/split", json={
            "path": G, "seg_id": tail1["seg_id"],
            "selections": [{"start_line": 9, "end_line": 11}],
        })
        check("G split 2 ok", r2.status_code == 200, r2.text[:120])
        # Externally edit the middle leaf (lines 5..7) and add 2 lines inside it
        edited_note = NOTE.replace(
            "aaa 第一句内容在这一行\nbbb 第二句内容在这一行\nccc 第三句内容在这一行",
            "aaa 外部重写第一行\nbbb 外部重写第二行\nccc 外部重写第三行\nddd 外部新增第四行\neee 外部新增第五行",
        )
        write_note(G, edited_note)
        # Trigger _reanchor_entry via status check
        st_res = await c.get("/api/reading/status")
        g_sum = next(s for s in st_res.json()["list"] if s["path"] == G)
        check("G flagged needs_resync after external leaf edit", g_sum["needs_resync"] is True, g_sum)
        # Verify nested container (tail1) relocated to shifted leaf (10..14) and root container (gsid) spans 1..14
        g_entry = next(e for e in backend._reading_read()["list"] if e["path"] == G)
        g_segs = {s["seg_id"]: s for s in g_entry["segments"]}
        check("nested container bottom-up span updated to 10..14",
              (g_segs[tail1["seg_id"]]["start_line"], g_segs[tail1["seg_id"]]["end_line"]) == (10, 14),
              g_segs[tail1["seg_id"]])
        check("root container bottom-up span updated to 1..14",
              (g_segs[gsid]["start_line"], g_segs[gsid]["end_line"]) == (1, 14),
              g_segs[gsid])
        # Now call /api/reading/reseed: the uncovered edited hole (5..9) must become 1 fresh todo segment
        # even though root container (1..13) covers those lines!
        reseed_res = await c.post("/api/reading/reseed", json={"path": G})
        check("reseed 200", reseed_res.status_code == 200, reseed_res.text[:120])
        check("reseed created 1 fresh todo segment for uncovered hole",
              reseed_res.json().get("fresh") == 1, reseed_res.json())
        g_entry_after = next(e for e in backend._reading_read()["list"] if e["path"] == G)
        fresh_todos = [
            s for s in g_entry_after["segments"]
            if s["status"] == "todo" and s["start_line"] == 5 and s["end_line"] == 10
        ]
        check("fresh todo segment covers uncovered lines 5..10",
              len(fresh_todos) == 1, g_entry_after["segments"])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


sys.exit(asyncio.run(run_closed(backend, main())))
