#!/usr/bin/env python3
"""Regression tests for the GOAL-BASED reading stage (user spec 2026-10-05).

The reading stage no longer stops after one fixed 4-segment batch: it keeps
dealing until MAKE_DAILY_GOAL cards were made TODAY (ANKI_DAILY_MAKE, default
= ANKI_DAILY_NEW). The checkpoint is a SEGMENT BOUNDARY (complete/skip/next)
— never mid-card — so card-making is never cut short:
  - act at a boundary with made >= goal → round parked (goal_reached=true,
    round_complete=true), pending segments keep their statuses (re-dealt
    next round);
  - reading/start with the goal already met → empty deal + goal_reached;
  - made < goal → normal advance (round stays open while pending);
  - counter unavailable (AnkiConnect dead / PREVIEW_MODE off) → fail-open:
    made_today=None, goal never fires, make_goal absent/None on the wire.

Same harness pattern as gate_segment_level_test.py: real FastAPI app against
a FAKE AnkiConnect and TEMPORARY corpus/state — prod untouched.

Run: backend/.venv/bin/python scripts/make_goal_test.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="goal-state-")
NOTES = Path(tempfile.mkdtemp(prefix="goal-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_READING_MODE"] = "1"
os.environ["ANKI_RELEASE_DAILY_GOAL"] = "45"
os.environ["ANKI_DAILY_MAKE"] = "3"   # small goal for the test
os.environ["ANKI_DAILY_READ"] = "4"

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


def nid_for(days_ago: int) -> int:
    """Realistic note id (Anki = creation ms epoch) at noon `days_ago`.
    Noon anchor: days_ago>=1 is ALWAYS an earlier Anki day regardless of
    when the suite runs (the modes_ui_test fixture bug taught this)."""
    dt = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


class FakeAnki:
    """Covers the pool-counter path: findCards(deck:预览池 is:new
    is:suspended) + cardsInfo(type/note). `dead=True` simulates AnkiConnect
    being unreachable (fail-open checks)."""

    def __init__(self):
        self.cards = {}
        self.notes = {}
        self.next_cid = 900001
        self.dead = False

    def add_pool_card(self, nid: int, *, deck="预览池", suspended=True, ctype=0):
        cid = self.next_cid
        self.next_cid += 1
        self.cards[cid] = {
            "cardId": cid, "type": ctype,
            "queue": -1 if suspended else (0 if ctype == 0 else 2),
            "deckName": deck, "due": 0, "interval": 0, "factor": 0,
            "reps": 0, "lapses": 0, "left": 0, "note": nid,
        }
        self.notes.setdefault(nid, {"tags": []})
        return cid

    def cards_of(self, nid: int) -> list:
        return [c["cardId"] for c in self.cards.values() if c["note"] == nid]

    def _match(self, cid, q):
        c = self.cards[cid]
        if 'deck:"预览池"' in q and c["deckName"] != "预览池":
            return False
        if 'deck:"2026"' in q and c["deckName"] != "2026":
            return False
        if "is:new" in q and c["type"] != 0:
            return False
        if "is:suspended" in q and c["queue"] != -1:
            return False
        if "-is:suspended" in q and c["queue"] == -1:
            return False
        if "tag:released-*" in q and not any(
                t.startswith("released-") for t in self.notes[c["note"]]["tags"]):
            return False
        return True

    async def __call__(self, action, params=None, timeout=30):
        if self.dead:
            raise ConnectionError("AnkiConnect unreachable (test)")
        params = params or {}
        if action == "findCards":
            return [cid for cid in self.cards if self._match(cid, params["query"])]
        if action == "cardsInfo":
            return [dict(self.cards[c]) for c in params["cards"] if c in self.cards]
        if action == "notesInfo":
            out = []
            for n in params["notes"]:
                if n in self.notes:
                    out.append({"noteId": n, "tags": list(self.notes[n]["tags"]),
                                "fields": {}, "modelName": "问答题",
                                "cards": self.cards_of(n)})
                else:
                    out.append({})
            return out
        if action == "changeDeck":
            for c in params["cards"]:
                if c in self.cards:
                    self.cards[c]["deckName"] = params["deck"]
            return None
        if action == "suspend":
            for c in params["cards"]:
                if c in self.cards:
                    self.cards[c]["queue"] = -1
            return None
        if action == "unsuspend":
            for c in params["cards"]:
                if c in self.cards:
                    self.cards[c]["queue"] = 0 if self.cards[c]["type"] == 0 else 2
            return None
        return None


fake = FakeAnki()
backend.anki = fake
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")  # keep the SPA mount away

NOTE = """# 胸部

## 一、胸壁

皮肤与浅筋膜内容，胸壁由肋骨与肋间肌构成支架结构。

## 二、胸膜

分为壁胸膜与脏胸膜两层，之间为潜在的胸膜腔隙结构。

## 三、肺与纵隔

左肺两叶右肺三叶，纵隔内有心脏大血管食管等重要结构。

## 四、膈肌

膈肌为穹窿状扁肌，分隔胸腔与腹腔，有三个重要裂孔。
"""

A_PATH = "胸.md"
(NOTES / A_PATH).write_text(NOTE, encoding="utf-8")


def _entry(path):
    data = backend._reading_read()
    return backend._find_entry(data, path)


def _segs(path):
    return sorted(_entry(path).get("segments") or [], key=lambda s: s["start_line"])


def _seg(path, seg_id):
    return next(s for s in _segs(path) if str(s.get("seg_id")) == str(seg_id))


def _round():
    return (backend._reading_read() or {}).get("round")


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


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("== setup: list + split into 4 sections ==")
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("add ok", r.json().get("ok") is True, r.text[:150])
        whole = _segs(A_PATH)[0]
        await _split_into_sections(c, A_PATH, whole["seg_id"], NOTE)
        dealable = [s for s in _segs(A_PATH)
                    if s.get("status") not in ("container", "background")]
        check("4 dealable sections", len(dealable) == 4,
              [(s["seg_id"], s["status"]) for s in _segs(A_PATH)])
        keys = [str(s["seg_id"]) for s in dealable]

        print("== 1. goal not met → normal deal + boundary advance ==")
        # 2 cards made today (< goal 3)
        fake.add_pool_card(nid_for(0))
        fake.add_pool_card(nid_for(0))
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("round dealt (4 chunks)", len(d.get("chunks", [])) == 4,
              len(d.get("chunks", [])))
        check("wire carries made_today=2 + make_goal=3",
              d.get("made_today") == 2 and d.get("make_goal") == 3,
              {k: d.get(k) for k in ("made_today", "make_goal")})
        check("no goal_reached on a normal deal", not d.get("goal_reached"))
        r = await c.post("/api/reading/act",
                         params={"path": A_PATH, "chunk_key": keys[0],
                                 "action": "complete"})
        d = r.json()
        check("boundary act: made 2 < goal 3 → round stays open",
              d.get("ok") is True and d.get("round_complete") is False
              and not d.get("goal_reached"), d)
        check("act echoes made_today=2", d.get("made_today") == 2, d)
        rnd = _round()
        check("3 chunks still pending", len(rnd.get("pending", [])) == 3, rnd)

        print("== 2. goal crossed at a SEGMENT BOUNDARY → round parked ==")
        fake.add_pool_card(nid_for(0))  # 3rd card today = goal met
        r = await c.post("/api/reading/act",
                         params={"path": A_PATH, "chunk_key": keys[1],
                                 "action": "complete"})
        d = r.json()
        check("goal_reached=true at the boundary", d.get("goal_reached") is True, d)
        check("round_complete=true → frontend chains to review",
              d.get("round_complete") is True, d)
        check("made_today=3 echoed", d.get("made_today") == 3, d)
        rnd = _round()
        check("round parked (status complete, pending empty)",
              rnd.get("status") == "complete" and rnd.get("pending") == [], rnd)
        st = {k: _seg(A_PATH, k).get("status") for k in keys}
        check("completed segments kept done",
              st[keys[0]] == "done" and st[keys[1]] == "done", st)
        check("parked segments UNTOUCHED (todo — re-dealt next round)",
              st[keys[2]] == "todo" and st[keys[3]] == "todo", st)

        print("== 3. start with the goal already met → empty + goal_reached ==")
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("empty deal", d.get("empty") is True and d.get("chunks") == [], d)
        check("goal_reached on the empty deal", d.get("goal_reached") is True, d)
        check("no round was created", _round() is None or
              _round().get("status") == "complete", _round())

        print("== 4. fail-open: AnkiConnect dead → goal never fires ==")
        fake.dead = True
        # release yesterday-style cards can't run either; auto_release fails
        # soft (returns 0). A fresh round must deal normally.
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("deal still works with AnkiConnect dead",
              len(d.get("chunks", [])) == 2, len(d.get("chunks", [])))
        check("made_today=None (counter unavailable)",
              d.get("made_today") is None, d)
        check("make_goal withheld on the wire", d.get("make_goal") is None, d)
        check("no goal_reached", not d.get("goal_reached"))
        r = await c.post("/api/reading/act",
                         params={"path": A_PATH, "chunk_key": keys[2],
                                 "action": "complete"})
        d = r.json()
        check("boundary act keeps the round open (fail-open)",
              d.get("ok") is True and not d.get("goal_reached")
              and d.get("round_complete") is False, d)
        fake.dead = False
        await c.post("/api/reading/finish")

        print("== 5. session/state carries the goal progress ==")
        r = await c.get("/api/session/state")
        st = r.json()
        check("made_today=3 in extras", st.get("made_today") == 3,
              st.get("made_today"))
        check("make_goal=3 in extras", st.get("make_goal") == 3,
              st.get("make_goal"))
        modes = (st.get("study_modes") or {}).get("daily") or {}
        check("study_modes.daily.make = 3", modes.get("make") == 3, modes)

        print("== 6. mark_active/promote/demote skip the counter ==")
        # mark_active must not touch made_today (cheap path, no AnkiConnect)
        fake.dead = True
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        k3 = keys[3]
        r = await c.post("/api/reading/act",
                         params={"path": A_PATH, "chunk_key": k3,
                                 "action": "mark_active"})
        d = r.json()
        check("mark_active ok while AnkiConnect is dead (no counter call)",
              d.get("ok") is True and d.get("made_today") is None, d)
        check("segment flipped to active", _seg(A_PATH, k3).get("status") == "active")
        fake.dead = False

        print("== 7. PREVIEW_MODE off → counter inactive ==")
        backend.PREVIEW_MODE = False
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=daily")
        d = r.json()
        check("deal works (goal tracking off)", d.get("empty") is not True, d)
        check("made_today=None", d.get("made_today") is None, d)
        check("make_goal=None", d.get("make_goal") is None, d)
        r = await c.get("/api/session/state")
        st = r.json()
        modes = (st.get("study_modes") or {}).get("daily") or {}
        check("wire study_modes.daily.make = 0 (inactive)", modes.get("make") == 0,
              modes)
        check("extras make_goal withheld", st.get("make_goal") is None,
              st.get("make_goal"))
        backend.PREVIEW_MODE = True
        await c.post("/api/reading/finish")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
