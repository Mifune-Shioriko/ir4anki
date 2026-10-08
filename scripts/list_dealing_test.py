#!/usr/bin/env python3
"""Regression tests for LIST-DRIVEN TWO-SLOT dealing (user spec 2026-10-06,
raised to TWO segments per file on 2026-10-09).

Every 阅读清单 file contributes UP TO TWO segments per round — slot 1 = its
first non-gated `active` (else its frontier); slot 2 = the second active, else
the next dealable segment in line order — so an N-file list deals up to 2N
segments (每篇文章推两次), with NO floor and NO cap. All card-making quotas are
retired. This suite pins the pure dealing semantics; the gate interaction
(gated active → frontier takes the slot; all-gated file contributes nothing)
is covered by gate_segment_level_test.py.

Harness pattern = gate_segment_level_test.py: real FastAPI app, FAKE
AnkiConnect, TEMPORARY corpus/state. PREVIEW_MODE is OFF so _reading_gates
returns an empty set and dealing never touches AnkiConnect.

Run: backend/.venv/bin/python scripts/list_dealing_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="list-deal-state-")
NOTES = Path(tempfile.mkdtemp(prefix="list-deal-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "0"   # gate off — pure dealing semantics
os.environ["ANKI_READING_MODE"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402
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


class FakeAnki:
    """Minimal fake — PREVIEW_MODE off means dealing never calls AnkiConnect;
    this only absorbs the incidental _wire_study_modes due-count probe."""
    async def __call__(self, action, params=None, timeout=30):
        if action == "findCards":
            return []
        return None


fake = FakeAnki()
backend.anki = fake
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")  # keep the SPA mount away

# ---- corpus: three multi-section files ------------------------------------
NOTE_A = """# 胸部

## 一、胸壁

皮肤与浅筋膜内容，胸壁由肋骨与肋间肌构成支架结构。

## 二、胸膜

分为壁胸膜与脏胸膜两层，之间为潜在的胸膜腔隙结构。

## 三、肺与纵隔

左肺两叶右肺三叶，纵隔内有心脏大血管食管等重要结构。
"""
NOTE_B = """# 腹部

## 一、腹壁

腹壁由多层肌肉与筋膜构成的保护性结构层次。

## 二、腹腔

腹腔内有胃肠道与肝胆胰脾等消化器官结构分布。
"""
NOTE_C = """# 盆部

## 一、盆壁

盆壁由髋骨与盆壁肌肉筋膜共同构成的骨性保护结构。

## 二、盆腔

盆腔内含有泌尿生殖器官与直肠末端等重要结构分布。
"""
A_PATH, B_PATH, C_PATH = "胸.md", "腹.md", "盆.md"
(NOTES / A_PATH).write_text(NOTE_A, encoding="utf-8")
(NOTES / B_PATH).write_text(NOTE_B, encoding="utf-8")
(NOTES / C_PATH).write_text(NOTE_C, encoding="utf-8")


# ---- state helpers (same shape as gate_segment_level_test.py) -------------
def _entry(path):
    return backend._find_entry(backend._reading_read(), path)

def _segs(path):
    return sorted(_entry(path).get("segments") or [], key=lambda s: s["start_line"])

def _dealable_segs(path):
    return [s for s in _segs(path)
            if s.get("status") not in ("container", "background")]

def _set_seg(path, seg_id, **kw):
    data = backend._reading_read()
    entry = backend._find_entry(data, path)
    for s in entry.get("segments") or []:
        if str(s.get("seg_id")) == str(seg_id):
            s.update(kw)
    backend._reading_write(data)

async def _split_into_sections(c, path, whole_id, text):
    """Cut the whole-file segment into one todo segment per '## ' heading."""
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

def _deal_paths(r):
    return [ch["path"] for ch in r.json().get("chunks", [])]

def _deal_for(r, path):
    return [ch for ch in r.json().get("chunks", []) if ch["path"] == path]


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("== setup: add 3 files, split each into sections ==")
        for p, note in ((A_PATH, NOTE_A), (B_PATH, NOTE_B), (C_PATH, NOTE_C)):
            r = await c.post("/api/reading/list/add", json={"path": p})
            check(f"add {p} ok", r.json().get("ok") is True, r.text[:150])
            whole = _segs(p)[0]
            await _split_into_sections(c, p, whole["seg_id"], note)
        a_segs = _dealable_segs(A_PATH)
        check("A split into 3 dealable sections", len(a_segs) == 3, len(a_segs))
        check("B split into 2 dealable sections", len(_dealable_segs(B_PATH)) == 2,
              len(_dealable_segs(B_PATH)))

        print("== 1. N files → up to 2N segments, two per file, list order ==")
        r = await c.post("/api/reading/start?mode=daily")
        dealt = r.json().get("chunks", [])
        check("dealt exactly 6 (two per listed file)", len(dealt) == 6, len(dealt))
        check("order = 阅读清单 priority A,A,B,B,C,C",
              _deal_paths(r) == [A_PATH, A_PATH, B_PATH, B_PATH, C_PATH, C_PATH],
              _deal_paths(r))
        check("each file contributes exactly TWO segments",
              all(len(_deal_for(r, p)) == 2 for p in (A_PATH, B_PATH, C_PATH)),
              [(p, len(_deal_for(r, p))) for p in (A_PATH, B_PATH, C_PATH)])
        a_dealt = _deal_for(r, A_PATH)
        check("A slot 1 = its frontier (first dealable section)",
              a_dealt[0]["chunk_key"] == str(a_segs[0]["seg_id"]),
              (a_dealt[0]["chunk_key"], str(a_segs[0]["seg_id"])))
        check("A slot 2 = the NEXT section in line order",
              a_dealt[1]["chunk_key"] == str(a_segs[1]["seg_id"]),
              (a_dealt[1]["chunk_key"], str(a_segs[1]["seg_id"])))
        await c.post("/api/reading/finish")

        print("== 2. reorder → dealing follows the new priority ==")
        r = await c.post("/api/reading/list/reorder", json={"path": B_PATH, "top": True})
        check("reorder B to top ok", r.json()["order"][0] == B_PATH, r.json()["order"])
        r = await c.post("/api/reading/start?mode=daily")
        check("dealing order now B,B,A,A,C,C",
              _deal_paths(r) == [B_PATH, B_PATH, A_PATH, A_PATH, C_PATH, C_PATH],
              _deal_paths(r))
        await c.post("/api/reading/finish")
        # restore A,B,C
        await c.post("/api/reading/list/reorder",
                     json={"order": [A_PATH, B_PATH, C_PATH]})

        print("== 3. active segment occupies slot 1; frontier rides slot 2 ==")
        # A's 2nd section → active; slot 1 must hand back the ACTIVE s2 and
        # slot 2 the next dealable segment in line order (the frontier s1).
        _set_seg(A_PATH, a_segs[1]["seg_id"], status="active")
        r = await c.post("/api/reading/start?mode=daily")
        a_dealt = _deal_for(r, A_PATH)
        check("A contributes exactly two segments", len(a_dealt) == 2, len(a_dealt))
        check("A slot 1 = the ACTIVE s2",
              a_dealt and a_dealt[0]["chunk_key"] == str(a_segs[1]["seg_id"]),
              a_dealt and a_dealt[0]["chunk_key"])
        check("A slot 1 status = active",
              a_dealt and a_dealt[0]["status"] == "active",
              a_dealt and a_dealt[0]["status"])
        check("A slot 2 = frontier s1 (next dealable in line order)",
              len(a_dealt) > 1 and a_dealt[1]["chunk_key"] == str(a_segs[0]["seg_id"]),
              len(a_dealt) > 1 and a_dealt[1]["chunk_key"])
        await c.post("/api/reading/finish")

        print("== 4. multiple actives → BOTH occupy the two slots (line order) ==")
        _set_seg(A_PATH, a_segs[2]["seg_id"], status="active")  # s2 AND s3 active
        r = await c.post("/api/reading/start?mode=daily")
        a_dealt = _deal_for(r, A_PATH)
        check("still exactly two A segments", len(a_dealt) == 2, len(a_dealt))
        check("first active in line order (s2) takes slot 1",
              a_dealt and a_dealt[0]["chunk_key"] == str(a_segs[1]["seg_id"]),
              a_dealt and a_dealt[0]["chunk_key"])
        check("second active (s3) takes slot 2 — actives beat the todo s1",
              len(a_dealt) > 1 and a_dealt[1]["chunk_key"] == str(a_segs[2]["seg_id"]),
              len(a_dealt) > 1 and a_dealt[1]["chunk_key"])
        await c.post("/api/reading/finish")
        _set_seg(A_PATH, a_segs[2]["seg_id"], status="todo")   # reset s3
        _set_seg(A_PATH, a_segs[1]["seg_id"], status="todo")   # reset s2

        print("== 5. single-file list → its two slots, no padding (no floor) ==")
        await c.post("/api/reading/list/remove", json={"path": B_PATH})
        await c.post("/api/reading/list/remove", json={"path": C_PATH})
        r = await c.post("/api/reading/start?mode=daily")
        dealt = r.json().get("chunks", [])
        check("single file deals exactly 2 segments (of its 3 sections)",
              len(dealt) == 2, len(dealt))
        check("both from A, slots = s1 then s2 (line order)",
              [ch["path"] for ch in dealt] == [A_PATH, A_PATH]
              and [ch["chunk_key"] for ch in dealt]
              == [str(a_segs[0]["seg_id"]), str(a_segs[1]["seg_id"])],
              [(ch["path"], ch["chunk_key"]) for ch in dealt])
        await c.post("/api/reading/finish")
        # restore B, C
        await c.post("/api/reading/list/add", json={"path": B_PATH})
        await c.post("/api/reading/list/add", json={"path": C_PATH})

        print("== 6. done/skipped segments never dealt; all-done file drops out ==")
        for s in _dealable_segs(A_PATH):
            _set_seg(A_PATH, s["seg_id"], status="done")
        r = await c.post("/api/reading/start?mode=daily")
        check("A (all done) contributes nothing", len(_deal_for(r, A_PATH)) == 0,
              _deal_for(r, A_PATH))
        check("round = B + C only (2×2 = 4 segments)",
              len(r.json().get("chunks", [])) == 4, _deal_paths(r))
        check("B and C still dealt (two each)",
              _deal_paths(r) == [B_PATH, B_PATH, C_PATH, C_PATH],
              _deal_paths(r))
        await c.post("/api/reading/finish")

        print("== 7. no card-making quota on the wire (goal retired) ==")
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("start response has NO goal_reached", "goal_reached" not in d, list(d))
        check("start response has NO made_today/make_goal",
              "made_today" not in d and "make_goal" not in d, list(d))
        await c.post("/api/reading/finish")
        r = await c.get("/api/status")
        modes = r.json().get("study_modes", {}).get("daily", {})
        check("study_modes.daily has NO 'read' size", "read" not in modes, modes)
        check("study_modes.daily has NO 'make' size", "make" not in modes, modes)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
