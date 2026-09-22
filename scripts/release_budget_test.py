#!/usr/bin/env python3
"""Unit tests for the daily release-budget cap on preview dealing (user spec 2026-09-16).

Rule: 剩余额度 = ANKI_RELEASE_DAILY_GOAL - 今日已放行 (pending_release).
Once the remaining budget drops below the biggest preview round size, EVERY
mode (quick AND focus) deals exactly the remainder; at 0 no round is dealt
(goal_reached). Deferred cards (明天再看) do NOT consume budget; undoing an
approval gives it back.

Runs the real FastAPI app against an in-memory FAKE AnkiConnect (same pattern
as new_again_test.py) — nothing touches the live collection.

Run: python scripts/release_budget_test.py   (needs the backend venv's deps + live AnkiConnect)
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="release-budget-test-")
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_PREVIEW_MODE"] = "1"

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
        print(f"  FAIL {name} {detail!r}")


def anki_day() -> str:
    return (datetime.now() - timedelta(hours=4)).strftime("%Y%m%d")


CAL_DAY = datetime.now().strftime("%Y%m%d")
STAMP = "released-" + anki_day()
DEFER = "deferred-" + CAL_DAY


# ---- fake AnkiConnect ------------------------------------------------------
# Extends the new_again_test fake with EXACT tag matching (the budget paths
# query tag:released-YYYYMMDD / tag:deferred-YYYYMMDD specifically).

class FakeAnki:
    def __init__(self):
        self.cards = {}
        self.notes = {}

    def add_card(self, cid, *, deck, ctype=0, suspended=False, note=None, tags=None):
        self.cards[cid] = {
            "cardId": cid,
            "type": ctype,
            "queue": -1 if suspended else (0 if ctype == 0 else 2),
            "deckName": deck,
            "due": 0 if ctype == 0 else 100,
            "interval": 0,
            "factor": 0,
            "reps": 0,
            "lapses": 0,
            "left": 3131,
            "question": f"<p>Q{cid}</p>",
            "answer": f"<p>A{cid}</p>",
            "css": "",
            "modelName": "问答题",
            "note": note or cid * 10,
        }
        self.notes.setdefault(self.cards[cid]["note"], {"tags": list(tags or [])})

    def _tags(self, cid):
        return self.notes.get(self.cards[cid]["note"], {}).get("tags", [])

    async def __call__(self, action, params=None, timeout=30):
        params = params or {}
        cards = self.cards
        if action == "cardsInfo":
            return [dict(cards[c]) for c in params.get("cards", []) if c in cards]
        if action == "notesInfo":
            return [
                {"noteId": n, "tags": list(self.notes[n]["tags"]),
                 "fields": {}, "modelName": "问答题", "note": ""}
                for n in params.get("notes", []) if n in self.notes
            ]
        if action == "changeDeck":
            for c in params["cards"]:
                if c in cards:
                    cards[c]["deckName"] = params["deck"]
            return None
        if action == "suspend":
            for c in params["cards"]:
                if c in cards:
                    cards[c]["queue"] = -1
            return None
        if action == "unsuspend":
            for c in params["cards"]:
                if c in cards:
                    cards[c]["queue"] = 0 if cards[c]["type"] == 0 else 2
            return None
        if action == "findCards":
            q = params["query"]
            out = []
            for cid, c in cards.items():
                ok = True
                if 'deck:"预览池"' in q and c["deckName"] != "预览池":
                    ok = False
                if 'deck:"2026"' in q and c["deckName"] != "2026":
                    ok = False
                if "is:new" in q and c["type"] != 0:
                    ok = False
                if "is:suspended" in q and c["queue"] != -1:
                    ok = False
                if "-is:suspended" in q and c["queue"] == -1:
                    ok = False
                if "is:due" in q and "-is:new" in q:
                    if not (c["type"] != 0 and c["queue"] == 2 and c["due"] <= 100):
                        ok = False
                # exact tag match: tag:NAME token in the query
                for tok in q.split():
                    if tok.startswith("tag:"):
                        want = tok[4:]
                        if want.endswith("*"):
                            if not any(t.startswith(want[:-1]) for t in self._tags(cid)):
                                ok = False
                        elif want not in self._tags(cid):
                            ok = False
                if ok:
                    out.append(cid)
            return out
        if action == "addTags":
            for n in params["notes"]:
                tags = self.notes.setdefault(n, {"tags": []})["tags"]
                for t in params["tags"].split():
                    if t not in tags:
                        tags.append(t)
            return None
        if action == "removeTags":
            for n in params["notes"]:
                tags = self.notes.setdefault(n, {"tags": []})["tags"]
                for t in params["tags"].split():
                    if t in tags:
                        tags.remove(t)
            return None
        if action == "sync":
            return None
        return None


def fresh():
    for f in ("round.json", "preview.json", "new_again.json"):
        (Path(STATE) / f).unlink(missing_ok=True)


def setup(goal, released_today, pool_size=20):
    """Fake collection: `released_today` approved-today cards in 2026
    (suspended + today's stamp) and `pool_size` pool cards."""
    fake = FakeAnki()
    backend.anki = fake
    backend.RELEASE_DAILY_GOAL = goal
    for i in range(released_today):
        fake.add_card(900_000 + i, deck="2026", ctype=0, suspended=True,
                      tags=[STAMP, "previewed"])
    for i in range(pool_size):
        fake.add_card(100_000 + i, deck="预览池", ctype=0, suspended=True)
    return fake


def client():
    transport = httpx.ASGITransport(app=backend.app)
    return httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30)


async def scenario_cap_applies_to_both_modes():
    print("== 1. budget 6 (< max size 10): BOTH quick and focus deal 6 ==")
    fresh()
    setup(goal=40, released_today=34)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("focus deals 6 (not 10)", len(r["cards"]) == 6, len(r["cards"]))
        check("preview_per_round=6", r.get("preview_per_round") == 6, r.get("preview_per_round"))
        prd = json.loads((Path(STATE) / "preview.json").read_text())
        check("round total=6", prd.get("total") == 6, prd.get("total"))
        capped = r.get("study_modes") or {}
        check("wire table capped: focus preview=6",
              (capped.get("focus") or {}).get("preview") == 6, capped)
        check("wire table capped: quick preview=6",
              (capped.get("quick") or {}).get("preview") == 6, capped)
        await c.post("/api/preview/finish")

        r = (await c.post("/api/preview/start?mode=quick")).json()
        check("quick also deals 6 (not 5)", len(r["cards"]) == 6, len(r["cards"]))
        await c.post("/api/preview/finish")


async def scenario_no_cap_while_budget_large():
    print("== 2. budget 32 (>= 10): normal sizes 5/10 ==")
    fresh()
    setup(goal=40, released_today=8)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("focus deals 10", len(r["cards"]) == 10, len(r["cards"]))
        await c.post("/api/preview/finish")
        r = (await c.post("/api/preview/start?mode=quick")).json()
        check("quick deals 5", len(r["cards"]) == 5, len(r["cards"]))
        s = (await c.get("/api/session/state")).json()
        check("state release_budget_left=32", s.get("release_budget_left") == 32,
              s.get("release_budget_left"))
        check("state study_modes raw", (s.get("study_modes") or {}).get("focus", {}).get("preview") == 10,
              s.get("study_modes"))
        await c.post("/api/preview/finish")


async def scenario_exhausted():
    print("== 3. budget 0: goal_reached, no deal ==")
    fresh()
    setup(goal=40, released_today=40)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("no cards dealt", r["cards"] == [], r["cards"])
        check("goal_reached flag", r.get("goal_reached") is True, r)
        check("no active round written", not (Path(STATE) / "preview.json").exists()
              or json.loads((Path(STATE) / "preview.json").read_text()).get("status") != "active")
        s = (await c.get("/api/session/state")).json()
        check("state release_budget_left=0", s.get("release_budget_left") == 0,
              s.get("release_budget_left"))
        check("state capped table preview=0",
              (s.get("study_modes") or {}).get("quick", {}).get("preview") == 0,
              s.get("study_modes"))


async def scenario_over_goal_clamps_to_zero():
    print("== 4. released > goal (43 > 40): budget clamps to 0 ==")
    fresh()
    setup(goal=40, released_today=43)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=quick")).json()
        check("goal_reached when over", r.get("goal_reached") is True, r)


async def scenario_defer_does_not_consume():
    print("== 5. defer does NOT consume budget; approve does ==")
    fresh()
    fake = setup(goal=40, released_today=34)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        ids = [x["cardId"] for x in r["cards"]]
        # defer 2, approve 1 (user's example shape)
        await c.post(f"/api/preview/act?card_id={ids[0]}&action=defer")
        await c.post(f"/api/preview/act?card_id={ids[1]}&action=defer")
        s = (await c.get("/api/session/state")).json()
        check("budget still 6 after 2 defers", s.get("release_budget_left") == 6,
              s.get("release_budget_left"))
        await c.post(f"/api/preview/act?card_id={ids[2]}&action=approve")
        s = (await c.get("/api/session/state")).json()
        check("budget 5 after 1 approve", s.get("release_budget_left") == 5,
              s.get("release_budget_left"))
        check("deferred card tagged", DEFER in fake._tags(ids[0]), fake._tags(ids[0]))
        check("approved card stamped", STAMP in fake._tags(ids[2]), fake._tags(ids[2]))
        # finish the round, start the next: must deal exactly 5
        await c.post("/api/preview/finish")
        r2 = (await c.post("/api/preview/start?mode=focus")).json()
        check("next round deals 5 (40-35)", len(r2["cards"]) == 5, len(r2["cards"]))
        await c.post("/api/preview/finish")


async def scenario_undo_gives_budget_back():
    print("== 6. undo of an approval restores budget ==")
    fresh()
    setup(goal=40, released_today=39)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("deals 1 (budget 1)", len(r["cards"]) == 1, len(r["cards"]))
        cid = r["cards"][0]["cardId"]
        a = (await c.post(f"/api/preview/act?card_id={cid}&action=approve")).json()
        check("round complete", a.get("round_complete") is True, a)
        check("act reports budget 0", a.get("release_budget_left") == 0,
              a.get("release_budget_left"))
        check("act capped table preview=0",
              (a.get("study_modes") or {}).get("focus", {}).get("preview") == 0,
              a.get("study_modes"))
        u = (await c.post("/api/preview/undo")).json()
        check("undo ok", u.get("restored") is True, u)
        s = (await c.get("/api/session/state")).json()
        check("budget back to 1 after undo", s.get("release_budget_left") == 1,
              s.get("release_budget_left"))
        await c.post("/api/preview/finish")


async def scenario_goal_disabled():
    print("== 7. goal <= 0 disables the cap entirely ==")
    fresh()
    setup(goal=0, released_today=999)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("focus deals full 10 despite 999 released", len(r["cards"]) == 10,
              len(r["cards"]))
        s = (await c.get("/api/session/state")).json()
        check("budget None (disabled)", s.get("release_budget_left") is None,
              s.get("release_budget_left"))
        await c.post("/api/preview/finish")


async def scenario_active_round_per_round_is_total():
    print("== 8. active round: preview_per_round reports the dealt total ==")
    fresh()
    setup(goal=40, released_today=37)
    async with client() as c:
        r = (await c.post("/api/preview/start?mode=focus")).json()
        check("deals 3", len(r["cards"]) == 3, len(r["cards"]))
        s = (await c.get("/api/session/state")).json()
        check("state preview_per_round=3", s.get("preview_per_round") == 3,
              s.get("preview_per_round"))
        await c.post("/api/preview/finish")


async def main():
    await scenario_cap_applies_to_both_modes()
    await scenario_no_cap_while_budget_large()
    await scenario_exhausted()
    await scenario_over_goal_clamps_to_zero()
    await scenario_defer_does_not_consume()
    await scenario_undo_gives_budget_back()
    await scenario_goal_disabled()
    await scenario_active_round_per_round_is_total()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
