#!/usr/bin/env python3
"""Unit tests for the next-day AUTO-RELEASE (user spec 2026-09-27).

Replaces release_budget_test.py (preview-deal budget) and new_again_test.py
(double-Again auto-return) — both features were deleted: the manual preview
stage is retired, and auto_release_pool() now moves pool cards whose NOTE
was created on an EARLIER Anki day into RELEASE_DECK unsuspended, capped at
RELEASE_DAILY_GOAL per Anki day (ledger state/auto_release.json).

Runs the real backend module against an in-memory FAKE AnkiConnect
(monkeypatched `backend.anki`) — nothing touches the user's collection.

Covers:
  1. pool card made YESTERDAY → released (deck 2026, unsuspended)
  2. pool card made TODAY → stays suspended in the pool
  3. legacy released-YYYYMMDD stamped card (old manual-approve flow,
     suspended in 2026) → drained by the legacy sweep
  4. daily cap: more aged cards than budget → oldest first, exactly
     `budget` released; ledger persists; a second run releases nothing
  5. ledger resets on a new Anki day
  6. _pending_release_today counts ONLY today's pool cards
  7. cap disabled (goal<=0) → everything aged releases, no ledger written
  8. fail-soft: dead AnkiConnect → returns 0, no exception

Run: backend/.venv/bin/python scripts/auto_release_test.py
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="auto-release-test-")
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_RELEASE_DAILY_GOAL"] = "3"   # small cap so the budget path is exercised

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

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


def nid_for(days_ago: float = 0.0, hour=None) -> int:
    """A realistic note id (Anki = creation ms epoch).

    hour: force a specific local hour of that day — used to test the 4 AM
    rollover (a card made at 02:00 'today' belongs to YESTERDAY's Anki day).
    """
    dt = datetime.now() - timedelta(days=days_ago)
    if hour is not None:
        dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


class FakeAnki:
    def __init__(self):
        self.cards = {}
        self.notes = {}

    def add_card(self, cid, *, deck, note, ctype=0, suspended=False, tags=None):
        self.cards[cid] = {
            "cardId": cid,
            "type": ctype,
            "queue": -1 if suspended else (0 if ctype == 0 else 2),
            "deckName": deck,
            "due": 0 if ctype == 0 else 100,
            "interval": 0, "factor": 0, "reps": 0, "lapses": 0, "left": 0,
            "question": f"<p>Q{cid}</p>", "answer": f"<p>A{cid}</p>",
            "css": "", "modelName": "问答题", "note": note,
            "isNew": ctype == 0,
        }
        self.notes.setdefault(note, {"tags": list(tags or [])})

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
        params = params or {}
        if action == "findCards":
            return [cid for cid in self.cards if self._match(cid, params["query"])]
        if action == "cardsInfo":
            return [dict(self.cards[c]) for c in params["cards"] if c in self.cards]
        if action == "notesInfo":
            return [{"noteId": n, "tags": list(self.notes[n]["tags"]),
                     "fields": {}, "modelName": "问答题"}
                    for n in params["notes"] if n in self.notes]
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
        if action in ("addTags", "removeTags"):
            for n in params["notes"]:
                tags = self.notes.setdefault(n, {"tags": []})["tags"]
                for t in (params.get("tags") or "").split():
                    if action == "addTags":
                        if t not in tags:
                            tags.append(t)
                    elif t in tags:
                        tags.remove(t)
            return None
        if action == "sync":
            return None
        return None


def reset():
    """Fresh fake + clean ledger between scenarios."""
    for f in ("round.json", "auto_release.json"):
        (Path(STATE) / f).unlink(missing_ok=True)
    fake = FakeAnki()
    backend.anki = fake
    return fake


async def scenario_timestamp_gate():
    print("== 1-2. timestamp gate: yesterday's card releases, today's stays ==")
    fake = reset()
    y_cid, y_nid = 7001, nid_for(days_ago=1, hour=15)
    t_cid, t_nid = 7002, nid_for(days_ago=0, hour=None)
    # guard: the 'today' note must really be today's Anki day
    assert backend._note_created_anki_day(t_nid) == backend._anki_day()
    assert backend._note_created_anki_day(y_nid) < backend._anki_day()
    fake.add_card(y_cid, deck="预览池", note=y_nid, suspended=True)
    fake.add_card(t_cid, deck="预览池", note=t_nid, suspended=True)

    n = await backend.auto_release_pool()
    check("released exactly 1", n == 1, n)
    check("yesterday's card now in 2026", fake.cards[y_cid]["deckName"] == "2026")
    check("yesterday's card unsuspended", fake.cards[y_cid]["queue"] == 0)
    check("today's card still in pool", fake.cards[t_cid]["deckName"] == "预览池")
    check("today's card still suspended", fake.cards[t_cid]["queue"] == -1)

    # 4 AM rollover: a card made at 02:00 local today belongs to YESTERDAY's
    # Anki day → releases on the next run (its first grading still gets an
    # overnight gap relative to the Anki scheduler's day).
    n2_cid, n2_nid = 7003, nid_for(days_ago=0, hour=2)
    if backend._note_created_anki_day(n2_nid) < backend._anki_day():
        fake.add_card(n2_cid, deck="预览池", note=n2_nid, suspended=True)
        n2 = await backend.auto_release_pool()
        check("pre-4AM card counts as yesterday → released", n2 == 1 and
              fake.cards[n2_cid]["deckName"] == "2026", n2)
    else:
        print("  skip (current time is after 4 AM + note lands on today's Anki day)")

    pend = await backend._pending_release_today()
    check("_pending_release_today counts only today's pool card", pend == 1, pend)


async def scenario_legacy_sweep():
    print("== 3. legacy released-* stamped cards (old manual flow) drain ==")
    fake = reset()
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    cid, nid = 7101, nid_for(days_ago=5)
    # old flow: approved → moved to 2026 but left SUSPENDED with a stamp
    fake.add_card(cid, deck="2026", note=nid, suspended=True,
                  tags=[f"released-{yday}", "previewed"])
    n = await backend.auto_release_pool()
    check("legacy stamped card released", n == 1, n)
    check("legacy card unsuspended in 2026",
          fake.cards[cid]["deckName"] == "2026" and fake.cards[cid]["queue"] == 0)
    # a stamp dated TODAY must NOT release (that was the old next-day rule)
    # NOTE: distinct note id (days_ago=6 + fixed hour) — same-ms note ids
    # would share one tag list and poison each other (fake models notes,
    # and real Anki does too: two cards of one note share its tags)
    cid2, nid2 = 7102, nid_for(days_ago=6, hour=12)
    assert nid2 != nid
    today_stamp = backend._anki_day()
    fake.add_card(cid2, deck="2026", note=nid2, suspended=True,
                  tags=[f"released-{today_stamp}"])
    n2 = await backend.auto_release_pool()
    check("today-stamped legacy card waits", n2 == 0 and fake.cards[cid2]["queue"] == -1, n2)


async def scenario_daily_cap():
    print("== 4. daily cap (RELEASE_DAILY_GOAL=3): oldest first, ledger ==")
    fake = reset()
    cids = []
    # 5 aged pool cards, ages 5..1 days (oldest = smallest nid)
    for i, days in enumerate([1, 2, 3, 4, 5]):
        cid = 7200 + i
        cids.append((cid, days))
        fake.add_card(cid, deck="预览池", note=nid_for(days_ago=days, hour=12),
                      suspended=True)
    n = await backend.auto_release_pool()
    check("released exactly cap=3", n == 3, n)
    released = [cid for cid, _ in cids if fake.cards[cid]["deckName"] == "2026"]
    held = [cid for cid, _ in cids if fake.cards[cid]["deckName"] == "预览池"]
    # oldest three (days=5,4,3) released
    oldest = sorted(cids, key=lambda t: -t[1])[:3]
    check("oldest-first: 5d/4d/3d released",
          set(released) == {c for c, _ in oldest}, (released, oldest))
    check("youngest two held for tomorrow", len(held) == 2, held)
    led = json.loads((Path(STATE) / "auto_release.json").read_text())
    check("ledger: day + released=3",
          led.get("day") == backend._anki_day() and led.get("released") == 3, led)
    check("_release_budget_left() == 0", backend._release_budget_left() == 0)

    n2 = await backend.auto_release_pool()
    check("second run same day releases 0 (cap spent)", n2 == 0, n2)

    # ledger reset: rewrite the day to yesterday → the cap comes back
    (Path(STATE) / "auto_release.json").write_text(json.dumps(
        {"day": "20000101", "released": 99}))
    check("stale-day ledger ignored", backend._released_today_count() == 0)
    n3 = await backend.auto_release_pool()
    check("new day: remaining 2 release", n3 == 2, n3)
    led3 = json.loads((Path(STATE) / "auto_release.json").read_text())
    check("ledger rewritten for the new day",
          led3.get("day") == backend._anki_day() and led3.get("released") == 2, led3)


async def scenario_cap_disabled():
    print("== 7. cap disabled (goal<=0): everything aged releases ==")
    fake = reset()
    backend.RELEASE_DAILY_GOAL = 0
    try:
        for i, days in enumerate([1, 2, 3, 4, 5]):
            fake.add_card(7300 + i, deck="预览池",
                          note=nid_for(days_ago=days, hour=12), suspended=True)
        n = await backend.auto_release_pool()
        check("all 5 released, uncapped", n == 5, n)
        check("budget None when disabled", backend._release_budget_left() is None)
        check("no ledger written when disabled",
              not (Path(STATE) / "auto_release.json").exists())
    finally:
        backend.RELEASE_DAILY_GOAL = 3


async def scenario_fail_soft():
    print("== 8. fail-soft: dead AnkiConnect → 0, no raise ==")
    reset()

    async def dead(action, params=None, timeout=30):
        raise ConnectionError("anki down")

    backend.anki = dead
    n = await backend.auto_release_pool()
    check("returns 0 on backend failure", n == 0, n)
    pend = None
    try:
        pend = await backend._pending_release_today()
    except Exception as e:  # _pending_release_today is allowed to raise (callers guard)
        pend = f"raised {type(e).__name__}"
    print(f"  note: _pending_release_today on dead backend → {pend!r} (callers try/except)")


async def scenario_wire():
    print("== wire: /api/status + /api/session/state carry the pool fields ==")
    import httpx
    fake = reset()
    fake.add_card(7401, deck="预览池", note=nid_for(days_ago=0, hour=None),
                  suspended=True)
    fake.add_card(7402, deck="预览池", note=nid_for(days_ago=2, hour=12),
                  suspended=True)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
        st = (await c.get("/api/status")).json()
        check("status.release_daily_goal = 3", st.get("release_daily_goal") == 3, st.get("release_daily_goal"))
        check("status.release_budget_left int", isinstance(st.get("release_budget_left"), int), st)
        s = (await c.get("/api/session/state")).json()
        # session/state runs auto_release → the aged card left the pool
        check("state.preview_pool = 1 (aged card auto-released)",
              s.get("preview_pool") == 1, s.get("preview_pool"))
        check("state.pending_release = 1 (today's card)",
              s.get("pending_release") == 1, s.get("pending_release"))
        check("state has NO preview_round field", "preview_round" not in s)
        modes = (s.get("study_modes") or {}).get("daily") or {}
        check("wire study_modes: no 'preview' key", "preview" not in modes, modes)
        check("wire study_modes: read/new present", modes.get("read") == 4 and modes.get("new") == 15, modes)
        # retired endpoints are really gone: no POST route matches; the SPA
        # catch-all is GET-only, so FastAPI answers 405 (or 404) — either
        # way NOT a JSON preview payload
        r = await c.post("/api/preview/start")
        check("/api/preview/start gone (404/405)", r.status_code in (404, 405), r.status_code)
        r2 = await c.post("/api/card/to-preview?card_id=7401")
        check("/api/card/to-preview gone (404/405)", r2.status_code in (404, 405), r2.status_code)


async def main():
    await scenario_timestamp_gate()
    await scenario_legacy_sweep()
    await scenario_daily_cap()
    await scenario_cap_disabled()
    await scenario_fail_soft()
    await scenario_wire()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
