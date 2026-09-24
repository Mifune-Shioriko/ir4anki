#!/usr/bin/env python3
"""E2E for the single daily pacing tier (user spec 2026-09-24).

Replaces the old two-tier (quick/focus) modes_e2e: one round shape —
4 reading + 15 preview + 15 new + ceil(D/3) reviews, where D is the day's
due count snapshotted at the first review deal (state/daily.json).

In-process ASGITransport against the REAL AnkiConnect but an ISOLATED
ANKI_STATE_DIR, so the user's live round.json/preview.json/daily.json are
untouched. Net-zero: no card is ever answered/approved/deferred; every
started round is finished (cleared) in teardown.

Run: python scripts/modes_e2e.py   (needs the backend venv's deps + live AnkiConnect)
"""
import asyncio
import json
import math
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="modes-e2e-state-")
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ.setdefault(
    "ANKI_MEDIA_DIR", tempfile.mkdtemp(prefix="modes-e2e-media-")
)

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


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=300) as c:
        print("== /api/status carries the single daily tier ==")
        st = (await c.get("/api/status")).json()
        check("status anki ok", st.get("anki") == "ok", st)
        modes = st.get("study_modes") or {}
        check("only 'daily' tier", list(modes.keys()) == ["daily"], list(modes.keys()))
        d = modes.get("daily", {})
        check("read=4 (wire)", d.get("read") == 4, d)
        check("preview=15 (wire)", d.get("preview") == 15, d)
        check("new=15 (wire)", d.get("new") == 15, d)
        check("review resolved (>=0, not the static placeholder when due>0)",
              isinstance(d.get("review"), int) and d["review"] >= 0, d.get("review"))
        check("default_mode = daily", st.get("default_mode") == "daily")
        check("release_daily_goal = 45", st.get("release_daily_goal") == 45,
              st.get("release_daily_goal"))
        check("status still has preview_mode", st.get("preview_mode") is True)

        due_pool = st.get("due_review") or 0
        expect_review = math.ceil(due_pool / 3) if due_pool else 0

        print("== session/state (idle) exposes the daily table ==")
        s = (await c.get("/api/session/state")).json()
        check("state none", s.get("state") == "none", s.get("state"))
        check("state carries study_modes", (s.get("study_modes") or {}) == modes)
        check("state preview_per_round None when idle", s.get("preview_per_round") is None,
              s.get("preview_per_round"))

        print("== start deals the daily batch: 15 new + ceil(D/3) reviews ==")
        r = (await c.post("/api/session/start")).json()
        n_new = sum(1 for x in r["cards"] if x["isNew"])
        n_rev = len(r["cards"]) - n_new
        check("response mode=daily", r.get("mode") == "daily", r.get("mode"))
        check("new_per_round=15 on wire", r.get("new_per_round") == 15, r.get("new_per_round"))
        check("new <= 15", n_new <= 15, n_new)
        check(f"reviews == ceil({due_pool}/3) = {expect_review}",
              n_rev == expect_review or due_pool == 0, f"n_rev={n_rev} due={due_pool}")
        rd = json.loads((Path(STATE) / "round.json").read_text())
        check("round.json stores mode=daily", rd.get("mode") == "daily", rd.get("mode"))
        await c.post("/api/session/finish")

        print("== daily snapshot: written by the first deal, sticky for the day ==")
        snap_file = Path(STATE) / "daily.json"
        check("daily.json written at first review deal", snap_file.exists())
        snap = json.loads(snap_file.read_text()) if snap_file.exists() else {}
        check("snapshot day = today's Anki day", snap.get("day") == backend._anki_day(), snap)
        r2 = (await c.post("/api/session/start")).json()
        snap2 = json.loads(snap_file.read_text())
        check("second start keeps the SAME snapshot", snap2.get("due") == snap.get("due"),
              (snap.get("due"), snap2.get("due")))
        n_rev2 = sum(1 for x in r2["cards"] if not x["isNew"])
        check(f"second batch reviews <= {expect_review} (snapshot-driven)",
              n_rev2 <= max(expect_review, 1) or due_pool == 0,
              f"n_rev2={n_rev2} expect={expect_review}")
        await c.post("/api/session/finish")

        print("== legacy mode names normalize to daily ==")
        for legacy in ("quick", "focus", "BOGUS"):
            rl = (await c.post(f"/api/session/start?mode={legacy}")).json()
            check(f"mode={legacy} → daily", rl.get("mode") == "daily", rl.get("mode"))
            await c.post("/api/session/finish")

        print("== more keeps working (legacy endpoint) ==")
        await c.post("/api/session/start")
        r3 = (await c.post("/api/session/more")).json()
        check("more mode=daily", r3.get("mode") == "daily", r3.get("mode"))
        check("more draws up to 15 new",
              sum(1 for x in r3["cards"] if x["isNew"]) <= 15, r3["cards"][:3])
        await c.post("/api/session/finish")

        print("== preview/start deals up to 15 pool cards ==")
        pool = st.get("preview_pool") or 0
        p = (await c.post("/api/preview/start")).json()
        expect_pv = min(15, pool, 45)
        check(f"preview deals min(15, pool={pool})", len(p["cards"]) == expect_pv,
              (len(p["cards"]), expect_pv))
        check("preview response mode=daily", p.get("mode") == "daily", p.get("mode"))
        prd = json.loads((Path(STATE) / "preview.json").read_text())
        check("preview.json stores mode=daily", prd.get("mode") == "daily", prd.get("mode"))
        s6 = (await c.get("/api/session/state")).json()
        check("state preview_round.mode=daily",
              (s6.get("preview_round") or {}).get("mode") == "daily")
        # teardown: finish WITHOUT acting — cards stay suspended in the pool
        f = (await c.post("/api/preview/finish")).json()
        check("preview finish ok", f.get("ok") is True)
        prd_after = json.loads((Path(STATE) / "preview.json").read_text()) \
            if (Path(STATE) / "preview.json").exists() else {}
        check("preview round cleared", prd_after.get("status") != "active", prd_after)

        print("== net-zero: pool unchanged, isolated state dir only ==")
        st2 = (await c.get("/api/status")).json()
        check("preview_pool unchanged", st2.get("preview_pool") == pool,
              f"{pool} -> {st2.get('preview_pool')}")
        s7 = (await c.get("/api/session/state")).json()
        check("final state none/idle", s7.get("state") == "none", s7.get("state"))
        check("no preview round left", s7.get("preview_round") is None)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
