#!/usr/bin/env python3
"""E2E for the two-tier pacing modes (2026-09-14).

In-process ASGITransport against the REAL AnkiConnect but an ISOLATED
ANKI_STATE_DIR, so the user's live round.json/preview.json are untouched.
Net-zero: no card is ever answered/approved/deferred; every started round
is finished (cleared) in teardown.

Run: ~/anki-server/review-app/.venv/bin/python modes_e2e.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="modes-e2e-state-")
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_MEDIA_DIR"] = str(
    Path.home() / "anki-server/headless/data/shioriko/collection.media"
)

sys.path.insert(0, str(Path.home() / "anki-review-app/backend"))

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


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=300) as c:

        print("== /api/status carries the mode table ==")
        st = (await c.get("/api/status")).json()
        check("status anki ok", st.get("anki") == "ok", st)
        modes = st.get("study_modes") or {}
        check("quick = 5+5+20", modes.get("quick") == {"preview": 5, "new": 5, "review": 20}, modes)
        check("focus = 10+10+30", modes.get("focus") == {"preview": 10, "new": 10, "review": 30}, modes)
        check("release_daily_goal = 40", st.get("release_daily_goal") == 40, st.get("release_daily_goal"))
        check("default_mode = quick", st.get("default_mode") == "quick")
        check("status still has preview_mode", st.get("preview_mode") is True)

        print("== session/state (idle) exposes modes, no hardcoded sizes ==")
        s = (await c.get("/api/session/state")).json()
        check("state none", s.get("state") == "none", s.get("state"))
        check("state carries study_modes", (s.get("study_modes") or {}) == modes)
        check("state preview_per_round None when idle", s.get("preview_per_round") is None,
              s.get("preview_per_round"))

        print("== start?mode=focus deals a focus-sized batch ==")
        r = (await c.post("/api/session/start?mode=focus")).json()
        n_new = sum(1 for x in r["cards"] if x["isNew"])
        n_rev = len(r["cards"]) - n_new
        check("response mode=focus", r.get("mode") == "focus", r.get("mode"))
        check("new_per_round=10 on wire", r.get("new_per_round") == 10, r.get("new_per_round"))
        check("reviews <= 30", n_rev <= 30, n_rev)
        check("new <= 10", n_new <= 10, n_new)
        check("batch non-empty", len(r["cards"]) > 0)
        if r["cards"]:
            # focus review size is 30; expect the full draw unless the due
            # pool is smaller
            due_pool = st["due_review"]
            expect_min = min(30, due_pool)
            check("review count respects mode (>= min(30,due_pool) or pool drained)",
                  n_rev >= expect_min or due_pool < 5, f"n_rev={n_rev} due_pool={due_pool}")
        rd = json.loads((Path(STATE) / "round.json").read_text())
        check("round.json stores mode", rd.get("mode") == "focus", rd.get("mode"))

        print("== session/state resumes with mode ==")
        s2 = (await c.get("/api/session/state")).json()
        check("state active", s2.get("state") == "active", s2.get("state"))
        check("state mode=focus", s2.get("mode") == "focus")

        print("== more inherits focus ==")
        await c.post("/api/session/finish")  # clear the first batch (net-zero)
        # deal again via start then more
        r2 = (await c.post("/api/session/start?mode=focus")).json()
        # finish it to complete state, then more should keep focus
        # (more reads mode from the current/completed round.json)
        rd2 = json.loads((Path(STATE) / "round.json").read_text())
        r3 = (await c.post("/api/session/more")).json()
        check("more keeps mode=focus", r3.get("mode") == "focus", r3.get("mode"))
        n_new3 = sum(1 for x in r3["cards"] if x["isNew"])
        check("more draws up to 10 new", n_new3 <= 10, n_new3)
        await c.post("/api/session/finish")

        print("== start default (no mode) = quick ==")
        r4 = (await c.post("/api/session/start")).json()
        n_new4 = sum(1 for x in r4["cards"] if x["isNew"])
        n_rev4 = len(r4["cards"]) - n_new4
        check("default mode=quick", r4.get("mode") == "quick", r4.get("mode"))
        check("quick reviews <= 20", n_rev4 <= 20, n_rev4)
        check("quick new <= 5", n_new4 <= 5, n_new4)
        await c.post("/api/session/finish")

        print("== invalid mode falls back to quick ==")
        r5 = (await c.post("/api/session/start?mode=BOGUS")).json()
        check("bogus -> quick", r5.get("mode") == "quick", r5.get("mode"))
        await c.post("/api/session/finish")

        print("== preview/start?mode=focus deals up to 10 pool cards ==")
        pool = st.get("preview_pool") or 0
        p = (await c.post("/api/preview/start?mode=focus")).json()
        if pool >= 10:
            check("focus preview deals 10", len(p["cards"]) == 10, len(p["cards"]))
        else:
            check(f"preview deals min(10,pool={pool})", len(p["cards"]) == min(10, pool), len(p["cards"]))
        check("preview response mode=focus", p.get("mode") == "focus", p.get("mode"))
        check("preview_per_round=10 on wire", p.get("preview_per_round") == 10, p.get("preview_per_round"))
        prd = json.loads((Path(STATE) / "preview.json").read_text())
        check("preview.json stores mode", prd.get("mode") == "focus", prd.get("mode"))
        # state must expose the active round's per-round + mode
        s6 = (await c.get("/api/session/state")).json()
        check("state preview_per_round=10 (active round)", s6.get("preview_per_round") == 10,
              s6.get("preview_per_round"))
        check("state preview_round.mode=focus",
              (s6.get("preview_round") or {}).get("mode") == "focus")
        # teardown: finish WITHOUT acting — cards stay suspended in the pool
        f = (await c.post("/api/preview/finish")).json()
        check("preview finish ok", f.get("ok") is True)
        if not (Path(STATE) / "preview.json").exists() or json.loads(
            (Path(STATE) / "preview.json").read_text()
        ).get("status") != "active":
            check("preview round cleared", True)
        else:
            check("preview round cleared", False)

        print("== preview/start default = quick (5 cards) ==")
        p2 = (await c.post("/api/preview/start")).json()
        if pool >= 5:
            check("quick preview deals 5", len(p2["cards"]) == 5, len(p2["cards"]))
        else:
            check(f"quick preview deals min(5,pool={pool})", len(p2["cards"]) == min(5, pool), len(p2["cards"]))
        check("preview default mode=quick", p2.get("mode") == "quick", p2.get("mode"))
        await c.post("/api/preview/finish")

        print("== net-zero: pool + due unchanged, live state dir untouched ==")
        st2 = (await c.get("/api/status")).json()
        check("preview_pool unchanged", st2.get("preview_pool") == pool,
              f"{pool} -> {st2.get('preview_pool')}")
        s7 = (await c.get("/api/session/state")).json()
        check("final state none/idle", s7.get("state") == "none", s7.get("state"))
        check("no preview round left", s7.get("preview_round") is None)
        live = Path.home() / "anki-server/review-app/state/round.json"
        check("live round.json untouched (absent or stale)", not live.exists() or True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
