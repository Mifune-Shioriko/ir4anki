#!/usr/bin/env python3
"""Regression tests for the SEGMENT-LEVEL reading gate (option B, 2026-09-29).

User ruling: a gated segment (its cards_created notes still inside the
preview pool) is NOT re-dealt, but it NO LONGER blocks its file — the
frontier search skips over it and later segments deal normally. Also fixes:
  - promote/demote bypass the in-round stale check (salvage API works
    mid-round); demote of a pending segment drops it from the round
    (counted as done, NOT skip).
  - new `gated_active` summary counter → reading_active funnel signal no
    longer distorted by gated TODO segments.
  - gated_frontier survives as a DISPLAY field (first held segment) even
    though it no longer blocks.

Same harness pattern as reading_edit_test.py: real FastAPI app against a
real isolated pylib collection and TEMPORARY corpus/state — prod untouched.

Run: backend/.venv/bin/python scripts/gate_segment_level_test.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from native_fixture import configure, tempdir, run_closed, NativeData

STATE = tempdir(prefix="gate-b-state-")
NOTES = Path(tempdir(prefix="gate-b-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_READING_MODE"] = "1"
os.environ["ANKI_RELEASE_DAILY_GOAL"] = "45"

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


def nid_for(days_ago: int) -> int:
    """A realistic note id (Anki = creation ms epoch) at noon `days_ago`.

    days_ago>=1 is ALWAYS an earlier Anki day (noon anchor survives the 4 AM
    rollover no matter when the suite runs — the modes_ui_test fixture bug
    taught this).
    """
    dt = (datetime.now() - timedelta(hours=4) - timedelta(days=days_ago)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


native = NativeData(backend)
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")  # keep the SPA mount away

NOTE = """# 胸部

## 一、胸壁

皮肤与浅筋膜内容，胸壁由肋骨与肋间肌构成支架结构。

## 二、胸膜

分为壁胸膜与脏胸膜两层，之间为潜在的胸膜腔隙结构。

## 三、肺与纵隔

左肺两叶右肺三叶，纵隔内有心脏大血管食管等重要结构。
"""

A_PATH = "胸.md"
B_PATH = "腹.md"
C_PATH = "盆.md"
(NOTES / A_PATH).write_text(NOTE, encoding="utf-8")
(NOTES / B_PATH).write_text(
    "# 腹部\n\n## 一、腹壁\n\n腹壁由多层肌肉与筋膜构成的保护性结构层次。\n\n"
    "## 二、腹腔\n\n腹腔内有胃肠道与肝胆胰脾等消化器官结构分布。\n",
    encoding="utf-8")
(NOTES / C_PATH).write_text(
    "# 盆部\n\n## 一、盆壁\n\n盆壁由髋骨与盆壁肌肉筋膜共同构成的骨性保护结构。\n\n"
    "## 二、盆腔\n\n盆腔内含有泌尿生殖器官与直肠末端等重要结构分布。\n",
    encoding="utf-8")


# ---- state helpers ----------------------------------------------------------

def _entry(path):
    data = backend._reading_read()
    return backend._find_entry(data, path)


def _segs(path):
    return sorted(_entry(path).get("segments") or [], key=lambda s: s["start_line"])


def _seg(path, seg_id):
    return next(s for s in _segs(path) if str(s.get("seg_id")) == str(seg_id))


def _set_seg(path, seg_id, **kw):
    """Mutate stored segment fields and persist (test-side writer)."""
    data = backend._reading_read()
    entry = backend._find_entry(data, path)
    for s in entry.get("segments") or []:
        if str(s.get("seg_id")) == str(seg_id):
            s.update(kw)
    backend._reading_write(data)


def _dealable_segs(path):
    return [s for s in _segs(path)
            if s.get("status") not in ("container", "background")]


async def _split_into_sections(c, path, whole_id, text):
    """Cut the whole-file segment into one todo segment per '## ' heading
    (chained bookmark tails — same helper as reading_edit_test.py)."""
    lines = text.split("\n")
    heads = [i + 1 for i, l in enumerate(lines) if l.startswith("## ")]
    cur = whole_id
    for k, h in enumerate(heads):
        end = (heads[k + 1] - 1) if k + 1 < len(heads) else len(lines)
        r = await c.post("/api/reading/split", json={
            "path": path, "seg_id": cur,
            "selections": [{"start_line": h, "end_line": end}],
            "gap_policy": "bookmark"})
        assert r.status_code == 200, r.text[:300]
        tail = next((x for x in r.json().get("children", []) if x.get("tail")), None)
        if tail is None:
            break
        cur = tail["seg_id"]


async def _add_and_split_prefix(c, path):
    """list/add + one bookmark cut around the LAST '## ' heading → returns
    (bg_seg_id, selected_todo_seg_id). The gap (header + first section)
    sinks to background."""
    r = await c.post("/api/reading/list/add", json={"path": path})
    assert r.status_code == 200 and r.json().get("ok"), r.text[:200]
    whole = _segs(path)[0]
    lines = (NOTES / path).read_text(encoding="utf-8").split("\n")
    h = max(i + 1 for i, l in enumerate(lines) if l.startswith("## "))
    r = await c.post("/api/reading/split", json={
        "path": path, "seg_id": whole["seg_id"],
        "selections": [{"start_line": h, "end_line": len(lines)}],
        "gap_policy": "bookmark"})
    assert r.status_code == 200, r.text[:300]
    bgs = [s for s in _segs(path) if s.get("status") == "background"]
    sel = [s for s in _segs(path) if s.get("status") == "todo"]
    assert bgs and sel, (bgs, sel)
    return bgs[0]["seg_id"], sel[0]["seg_id"]


def _deal_keys(r):
    return {ch["chunk_key"] for ch in r.json().get("chunks", [])}


def _summary(r, path):
    return next(s for s in r.json()["list"] if s["path"] == path)


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("== setup: add 胸.md + split into 3 sections ==")
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("add ok", r.json().get("ok") is True, r.text[:150])
        whole = _segs(A_PATH)[0]
        await _split_into_sections(c, A_PATH, whole["seg_id"], NOTE)
        s1, s2, s3 = _dealable_segs(A_PATH)
        k1, k2, k3 = (str(s["seg_id"]) for s in (s1, s2, s3))

        print("== 1. gated middle segment does NOT block the file (option B) ==")
        # a card made TODAY on s2 → s2 flips active + gated (pool, suspended)
        nid2 = nid_for(0)
        native.add_pool_card(nid2)
        backend._reading_record_card({"path": A_PATH, "chunk_key": k2}, nid2)
        check("s2 flipped to active by card creation",
              _seg(A_PATH, k2)["status"] == "active")
        r = await c.post("/api/reading/start?mode=daily")
        dealt = _deal_keys(r)
        # two-slot dealing (2026-10-09): UP TO TWO segments per file. s2 (the
        # only active) is gated → slot 1 falls back to the frontier s1 and
        # slot 2 takes the next dealable segment s3 (gated s2 skipped, file
        # NOT blocked — option B).
        check("round dealt {s1, s3} — gated active skipped, frontier + next take the slots",
              dealt == {k1, k3}, str(dealt))
        await c.post("/api/reading/finish")

        print("== 2. summary + funnel wire shape ==")
        r = await c.get("/api/reading/status")
        a_sum = _summary(r, A_PATH)
        check("frontier = s1 (gated s2 skipped, file NOT blocked)",
              a_sum["frontier"] and a_sum["frontier"]["chunk_key"] == k1,
              str(a_sum["frontier"]))
        check("gated = 1", a_sum["gated"] == 1, str(a_sum))
        check("gated_active = 1", a_sum.get("gated_active") == 1, str(a_sum))
        r = await c.get("/api/session/state")
        st = r.json()
        check("reading_active funnel = 0 (only active seg is gated)",
              st.get("reading_active", -1) == 0, str(st.get("reading_active")))
        check("reading_gated = 1", st.get("reading_gated") == 1,
              str(st.get("reading_gated")))
        check("reading_available = 1 (file still dealable)",
              st.get("reading_available") == 1, str(st.get("reading_available")))

        print("== 3. gated segment resurfaces after its cards release ==")
        # simulate the Anki-day rollover: the note id becomes an EARLIER day
        # so auto_release_pool (runs at reading/start) releases the card.
        nid2y = nid_for(2)
        native.age_note(nid2, nid2y)
        _set_seg(A_PATH, k2, cards_created=[nid2y])
        r = await c.post("/api/reading/start?mode=daily")
        dealt = _deal_keys(r)
        check("released s2 resurfaces (phase 0 active-first)", k2 in dealt, str(dealt))
        check("pool card auto-released to 2026 unsuspended",
              all(ci["deckName"] == "2026" and ci["queue"] == 0
                  for ci in native.cards.values() if ci["note"] == nid2y))
        await c.post("/api/reading/finish")

        print("== 4. all-gated file: frontier None + gated_frontier chip ==")
        # a NEW today-card on s2 → gated again; then finish off s1 + s3 so
        # the gated s2 is the only live segment left in the file.
        nid2b = nid_for(0) + 1
        native.add_pool_card(nid2b)
        _set_seg(A_PATH, k2, cards_created=[nid2b])
        r = await c.post("/api/reading/start?mode=daily")
        check("round dealt {s1, s3} (two-slot; gated s2 skipped)",
              _deal_keys(r) == {k1, k3}, str(_deal_keys(r)))
        # clear s3 test-side and complete s1 through the round, leaving
        # gated s2 as the only live segment
        _set_seg(A_PATH, k3, status="done")
        r = await c.post("/api/reading/act",
                         params={"path": A_PATH, "chunk_key": k1,
                                 "action": "complete"})
        check("complete s1 ok", r.json().get("ok") is True, r.text[:150])
        r = await c.get("/api/reading/status")
        a_sum = _summary(r, A_PATH)
        check("no frontier left (only the gated segment)",
              a_sum["frontier"] is None, str(a_sum))
        check("gated_frontier = s2 (display field survives option B)",
              a_sum["gated_frontier"]
              and a_sum["gated_frontier"]["chunk_key"] == k2,
              str(a_sum["gated_frontier"]))
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("empty deal + all_gated=true",
              d.get("empty") is True and d.get("all_gated") is True, str(d))

        print("== 5. promote works MID-ROUND (was silently stale before) ==")
        bg_b, sel_b = await _add_and_split_prefix(c, B_PATH)
        r = await c.post("/api/reading/start?mode=daily")
        check("round active (腹's selected section dealt)",
              not r.json().get("empty"), r.text[:150])
        r = await c.post("/api/reading/act",
                         params={"path": B_PATH, "chunk_key": str(bg_b),
                                 "action": "promote"})
        check("promote mid-round ok (not stale)",
              r.json().get("ok") is True, r.text[:200])
        check("promoted segment is todo", r.json().get("status") == "todo",
              str(r.json()))

        print("== 6. demote of a PENDING segment drops it from the round ==")
        r = await c.get("/api/reading/state")
        rd = r.json().get("round") or {}
        pend = rd.get("chunks") or []
        victim = next((p for p in pend if p["path"] == B_PATH
                       and p["status"] in ("todo", "active")), None)
        check("found a demotable pending segment", victim is not None,
              str({p["chunk_key"] for p in pend}))
        if victim is not None:
            done_before = rd.get("done") or 0
            r = await c.post("/api/reading/act",
                             params={"path": victim["path"],
                                     "chunk_key": victim["chunk_key"],
                                     "action": "demote"})
            d = r.json()
            check("demote mid-round ok", d.get("ok") is True, r.text[:200])
            check("status = background", d.get("status") == "background", str(d))
            r = await c.get("/api/reading/state")
            rd2 = r.json().get("round") or {}
            pend_after = {p["chunk_key"] for p in (rd2.get("chunks") or [])}
            check("demoted segment GONE from pending",
                  victim["chunk_key"] not in pend_after, str(pend_after))
            check("done counter +1 (drift-drop accounting, NOT skip)",
                  (rd2.get("done") or 0) == done_before + 1,
                  str((rd2.get("done"), done_before)))
            check("skipped stat NOT bumped",
                  (rd2.get("stats") or {}).get("skipped", 0) == 0,
                  str(rd2.get("stats")))
        await c.post("/api/reading/finish")

        print("== 7. gated TODO does not distort reading_active (⑤ fix) ==")
        # 盆.md: selected section gets a RELEASED card (→ active, not gated);
        # the background gap gets a TODAY pool card and is promoted → gated
        # TODO. Old formula (active - gated) would report 0 for this file;
        # the new gated_active-based one reports 1.
        bg_c, sel_c = await _add_and_split_prefix(c, C_PATH)
        backend._reading_record_card(
            {"path": C_PATH, "chunk_key": str(sel_c)}, nid2y)  # released card
        nid_c = nid_for(0)
        native.add_pool_card(nid_c)
        _set_seg(C_PATH, bg_c, cards_created=[nid_c])
        r = await c.post("/api/reading/act",
                         params={"path": C_PATH, "chunk_key": str(bg_c),
                                 "action": "promote"})
        check("promote (outside round) ok", r.json().get("ok") is True, r.text[:200])
        r = await c.get("/api/reading/status")
        c_sum = _summary(r, C_PATH)
        check("盆: selected section active + not gated",
              c_sum.get("active") == 1 and c_sum.get("gated_active", -1) == 0,
              str({k: c_sum.get(k) for k in ("active", "gated", "gated_active")}))
        check("盆: promoted segment is a gated TODO",
              c_sum["gated"] == 1 and c_sum["todo"] >= 1,
              str({k: c_sum.get(k) for k in ("todo", "gated")}))
        r = await c.get("/api/session/state")
        st = r.json()
        # 胸's only active (s2) is gated → contributes 0; 盆's active is NOT
        # gated → contributes 1. Old formula would say 0.
        check("reading_active = 1 (gated todo no longer subtracted)",
              st.get("reading_active") == 1, str(st.get("reading_active")))
        exp = sum(max(0, s.get("active", 0) - s.get("gated_active", 0))
                  for s in (await c.get("/api/reading/status")).json()["list"])
        check("reading_active == Σ max(0, active - gated_active)",
              st.get("reading_active") == exp, str((st.get("reading_active"), exp)))

        await c.post("/api/reading/finish")

    # Validate the released card through the real v3/FSRS answer path, after
    # all original dealing/gating assertions so scheduling cannot alter them.
    released = next(cid for cid, info in native.cards.items() if info['note'] == nid2y)
    check('native FSRS configured', native.native(lambda col: col.get_config('fsrs')) is True)
    answered = await backend.anki('answerCards', {'answers': [{'cardId': released, 'ease': 3}]})
    check('released card answered by native scheduler', answered == [True])
    check('native FSRS memory state persisted', native.native(
        lambda col: col.get_card(released).memory_state) is not None)
    check('native revlog records release review', native.native(
        lambda col: col.db.scalar('select count(*) from revlog where cid=?', released)) == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(run_closed(backend, main()))
