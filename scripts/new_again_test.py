#!/usr/bin/env python3
"""Unit tests for the double-Again auto-return (user spec 2026-09-16).

Runs the real FastAPI app against an in-memory FAKE AnkiConnect (monkeypatched
`backend.anki`), so nothing touches the user's collection. Covers:
  1. NEW card + Again + Again  -> moved back to the preview pool, response
     carries returned_to_preview, streak cleared, round bookkeeping sane
  2. NEW card + Again + Good   -> streak cleared, card stays in the round
  3. REVIEW card + Again x2    -> never auto-returns (streak only opens on new)
  4. Undo after auto-return    -> card restored to the round AND moved back
     out of the pool (deck+suspend reversed), streak reset to limit-1
  5. Undo of a plain Again     -> streak decremented
  6. Kill switch               -> ANKI_NEW_AGAIN_RETURN off = no auto-return
  7. Manual to-preview clears the streak

Run: ~/anki-server/review-app/.venv/bin/python /home/shioriko/anki-review-app/scripts/new_again_test.py
"""
import asyncio
import importlib
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="new-again-test-")
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
        print(f"  FAIL {name} {detail}")


# ---- fake AnkiConnect ------------------------------------------------------
# Minimal in-memory collection: cards have type (0=new, 2=relearn), deckName,
# queue (-1 suspended), reps/lapses/due. Understands the subset of actions the
# backend actually calls on the answer/undo/to-preview paths.

class FakeAnki:
    def __init__(self):
        self.cards = {}
        self.notes = {}
        self.calls = []

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
        self.notes.setdefault(self.cards[cid]["note"], {"tags": tags or []})

    async def __call__(self, action, params=None, timeout=30):
        params = params or {}
        self.calls.append(action)
        cards = self.cards
        if action == "cardsInfo":
            return [dict(cards[c]) for c in params["cards"] if c in cards]
        if action == "notesInfo":
            out = []
            for n in params["notes"]:
                if n in self.notes:
                    out.append({"noteId": n, "tags": list(self.notes[n]["tags"]),
                                "fields": {}, "modelName": "问答题", "note": ""})
            return out
        if action == "answerCards":
            res = []
            for a in params["answers"]:
                c = cards.get(a["cardId"])
                if c is None:
                    res.append(False)
                    continue
                ease = a["ease"]
                if ease == 1:
                    c["lapses"] += 1
                    c["type"] = 2 if c["type"] == 0 else c["type"]
                    c["queue"] = 1
                    c["due"] = 1
                else:
                    c["type"] = 2
                    c["queue"] = 2
                    c["due"] = 100 + ease
                    c["interval"] = ease
                c["reps"] += 1
                res.append(True)
            return res
        if action == "forgetCards":
            for c in params["cards"]:
                card = cards.get(c)
                if card:
                    card["type"] = 0
                    card["queue"] = 0
                    card["due"] = 0
                    card["interval"] = 0
                    card["factor"] = 0
            return None
        if action == "setSpecificValueOfCard":
            card = cards.get(params["card"])
            if card is None:
                return [False]
            keymap = {"ivl": "interval", "factor": "factor", "due": "due",
                      "reps": "reps", "lapses": "lapses", "left": "left",
                      "type": "type", "queue": "queue"}
            for k, v in zip(params["keys"], params["newValues"]):
                if k in keymap:
                    card[keymap[k]] = v
            if card["queue"] == -1:
                pass
            return [True]
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
                if "tag:released-" in q and not any(
                        t.startswith("released-") for t in self.notes[c["note"]]["tags"]):
                    ok = False
                if "tag:released-" not in q and "released-" not in q:
                    pass
                if ok:
                    out.append(cid)
            return out
        if action in ("addTags",):
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
        if action == "deleteNotes":
            for n in params["notes"]:
                for cid in [k for k, c in cards.items() if c["note"] == n]:
                    del cards[cid]
            return None
        return None


def fresh(backend_mod):
    """Reset round/preview/streak state files between scenarios."""
    for f in ("round.json", "preview.json", "new_again.json"):
        p = Path(STATE) / f
        p.unlink(missing_ok=True)


async def scenario_double_again():
    print("== 1. NEW card, Again x2 -> auto-return to preview pool ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(1001, deck="2026", ctype=0)  # released new card
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        # hand-deal a round containing the card
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
            '"done_count":0,"pending":[1001],"new_count":1,"mode":"quick"}'
        )
        r1 = (await c.post("/api/answer?card_id=1001&ease=1")).json()
        check("first Again answered", r1.get("answered") is True, r1)
        check("first Again: no auto-return", r1.get("returned_to_preview") is None, r1)
        streak = backend._streak_read()
        check("streak=1 after first Again", streak.get("1001") == 1, streak)

        # Anki relearning: card comes back due later — re-deal it
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:30:00","total":1,'
            '"done_count":0,"pending":[1001],"new_count":0,"mode":"quick"}'
        )
        r2 = (await c.post("/api/answer?card_id=1001&ease=1")).json()
        check("second Again answered", r2.get("answered") is True, r2)
        rt = r2.get("returned_to_preview")
        check("second Again -> returned_to_preview", rt and rt.get("cardId") == 1001, r2)
        card = fake.cards[1001]
        check("card back in 预览池", card["deckName"] == "预览池", card["deckName"])
        check("card suspended", card["queue"] == -1, card["queue"])
        check("card reset to new (type=0)", card["type"] == 0, card["type"])
        check("reps zeroed", card["reps"] == 0, card["reps"])
        check("streak cleared", backend._streak_read().get("1001") is None,
              backend._streak_read())
        # undo slot marked auto_return
        import json as _json
        rd = _json.loads((Path(STATE) / "round.json").read_text())
        check("undo slot marked auto_return",
              (rd.get("last") or {}).get("auto_return") is True, rd.get("last"))


async def scenario_again_then_good():
    print("== 2. NEW card, Again + Good -> streak cleared, stays ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(2001, deck="2026", ctype=0)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
            '"done_count":0,"pending":[2001],"new_count":1,"mode":"quick"}'
        )
        await c.post("/api/answer?card_id=2001&ease=1")
        check("streak=1", backend._streak_read().get("2001") == 1)
        # re-deal, this time Good
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:30:00","total":1,'
            '"done_count":0,"pending":[2001],"new_count":0,"mode":"quick"}'
        )
        r = (await c.post("/api/answer?card_id=2001&ease=3")).json()
        check("Good answered", r.get("answered") is True, r)
        check("no auto-return on Good", r.get("returned_to_preview") is None, r)
        check("streak cleared by Good", backend._streak_read() == {}, backend._streak_read())
        check("card still in 2026", fake.cards[2001]["deckName"] == "2026",
              fake.cards[2001]["deckName"])


async def scenario_review_card():
    print("== 3. REVIEW card Again x2 -> never auto-returns ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(3001, deck="2026", ctype=2)  # mature review card
    fake.cards[3001]["queue"] = 2
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
            '"done_count":0,"pending":[3001],"new_count":0,"mode":"quick"}'
        )
        r1 = (await c.post("/api/answer?card_id=3001&ease=1")).json()
        check("lapse 1 no return", r1.get("returned_to_preview") is None, r1)
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:30:00","total":1,'
            '"done_count":0,"pending":[3001],"new_count":0,"mode":"quick"}'
        )
        r2 = (await c.post("/api/answer?card_id=3001&ease=1")).json()
        check("lapse 2 no return (review card)", r2.get("returned_to_preview") is None, r2)
        check("no streak opened for review card",
              backend._streak_read().get("3001") is None, backend._streak_read())
        check("review card stays put", fake.cards[3001]["deckName"] == "2026")


async def scenario_undo_auto_return():
    print("== 4. Undo after auto-return -> back in round + out of pool ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(4001, deck="2026", ctype=0)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":2,'
            '"done_count":0,"pending":[4001,4002],"new_count":1,"mode":"quick"}'
        )
        fake.add_card(4002, deck="2026", ctype=2)
        fake.cards[4002]["queue"] = 2
        await c.post("/api/answer?card_id=4001&ease=1")
        # re-deal just 4001; second Again triggers the return
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:30:00","total":1,'
            '"done_count":0,"pending":[4001],"new_count":0,"mode":"quick"}'
        )
        r = (await c.post("/api/answer?card_id=4001&ease=1")).json()
        check("auto-return happened", r.get("returned_to_preview") is not None, r)
        check("card in pool", fake.cards[4001]["deckName"] == "预览池")
        u = (await c.post("/api/undo")).json()
        check("undo restored", u.get("restored") is True, u)
        check("undo reports returned_from_preview",
              (u.get("returned_from_preview") or {}).get("cardId") == 4001, u)
        card = fake.cards[4001]
        check("card back in 2026", card["deckName"] == "2026", card["deckName"])
        check("card unsuspended", card["queue"] != -1, card["queue"])
        check("card back in round pending",
              4001 in __import__("json").loads((Path(STATE) / "round.json").read_text()).get("pending", []))
        check("streak restored to limit-1", backend._streak_read().get("4001") == 1,
              backend._streak_read())


async def scenario_undo_plain_again():
    print("== 5. Undo of a plain Again -> streak decremented ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(5001, deck="2026", ctype=0)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
            '"done_count":0,"pending":[5001],"new_count":1,"mode":"quick"}'
        )
        await c.post("/api/answer?card_id=5001&ease=1")
        check("streak=1", backend._streak_read().get("5001") == 1)
        u = (await c.post("/api/undo")).json()
        check("undo ok", u.get("restored") is True, u)
        check("streak gone after undo", backend._streak_read() == {}, backend._streak_read())


async def scenario_kill_switch():
    print("== 6. Kill switch ANKI_NEW_AGAIN_RETURN=0 -> disabled ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    old = backend.NEW_AGAIN_RETURN
    backend.NEW_AGAIN_RETURN = False
    fake.add_card(6001, deck="2026", ctype=0)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        for i in range(2):
            (Path(STATE) / "round.json").write_text(
                '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
                '"done_count":0,"pending":[6001],"new_count":1,"mode":"quick"}'
            )
            r = (await c.post("/api/answer?card_id=6001&ease=1")).json()
            check(f"again {i+1}: no auto-return (disabled)",
                  r.get("returned_to_preview") is None, r)
        check("card still in 2026", fake.cards[6001]["deckName"] == "2026")
        check("no streak tracked while disabled", backend._streak_read() == {})
    backend.NEW_AGAIN_RETURN = old


async def scenario_manual_to_preview_clears():
    print("== 7. Manual 移回预览池 clears the streak ==")
    fresh(backend)
    fake = FakeAnki()
    backend.anki = fake
    fake.add_card(7001, deck="2026", ctype=0)
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        (Path(STATE) / "round.json").write_text(
            '{"status":"active","created":"2026-09-16T10:00:00","total":1,'
            '"done_count":0,"pending":[7001],"new_count":1,"mode":"quick"}'
        )
        await c.post("/api/answer?card_id=7001&ease=1")
        check("streak=1", backend._streak_read().get("7001") == 1)
        fake.cards[7001]["queue"] = 2  # relearning, due again
        r = (await c.post("/api/card/to-preview?card_id=7001")).json()
        check("manual to-preview ok", r.get("moved") is True, r)
        check("streak cleared by manual move", backend._streak_read() == {},
              backend._streak_read())


async def main():
    await scenario_double_again()
    await scenario_again_then_good()
    await scenario_review_card()
    await scenario_undo_auto_return()
    await scenario_undo_plain_again()
    await scenario_kill_switch()
    await scenario_manual_to_preview_clears()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
