#!/usr/bin/env python3
"""Unit check: sync throttle (L1) + global serialization (L2).

Imports app with a tiny SYNC_MIN_INTERVAL and counts do_sync executions.
No Anki needed — anki() is monkeypatched.

Run: backend/.venv/bin/python scripts/sync_throttle_test.py
"""
import asyncio
import os
import sys
import tempfile

os.environ["ANKI_STATE_DIR"] = tempfile.mkdtemp(prefix="ir4anki-throttle-")
os.environ["ANKI_SYNC_MIN_INTERVAL"] = "0.5"
os.environ["ANKICONNECT_URL"] = "http://127.0.0.1:18765"  # dead

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import app  # noqa: E402

ORIG_ANKI = app.anki  # capture BEFORE monkeypatching (test 5 restores it)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


async def main():
    sync_calls = 0
    call_times = []

    async def fake_anki(action, params=None, timeout=30):
        nonlocal sync_calls
        if action == "sync":
            sync_calls += 1
            call_times.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.05)  # simulate sync work
        return []

    app.anki = fake_anki

    # --- 1. burst of 5 fires -> exactly 1 immediate sync (first fire is
    #        never throttled: _sync_last_finish starts at 0 => wait < 0).
    #        Fires 2-5 arrive while pending and are absorbed.
    for _ in range(5):
        app.fire_and_forget_sync()
    await asyncio.sleep(0.2)
    check("burst coalesced to 1 sync", sync_calls == 1, f"got {sync_calls}")

    # --- 2. fire right after a sync completed -> inside cooldown -> tail
    #        armed, fires only when the window expires.
    app.fire_and_forget_sync()
    await asyncio.sleep(0.2)
    check("tail not early", sync_calls == 1, f"got {sync_calls}")
    await asyncio.sleep(0.5)
    check("tail fired after interval", sync_calls == 2, f"got {sync_calls}")
    gap = call_times[1] - call_times[0]
    check("tail respects min interval", gap >= 0.45, f"gap={gap:.2f}s")

    # --- 3. fire DURING pending is absorbed (coalescer unchanged): arms
    #        at most one tail, duplicates never stack.
    app.fire_and_forget_sync()
    app.fire_and_forget_sync()
    n_before = sync_calls
    await asyncio.sleep(0.8)
    check("duplicate fires => single tail", sync_calls == n_before + 1,
          f"got {sync_calls - n_before} extra")

    # --- 4. forced do_sync absorbs an armed tail (collection just synced).
    await app.do_sync()        # resets the cooldown window deterministically
    app.fire_and_forget_sync()  # inside cooldown -> arms tail
    await asyncio.sleep(0.05)
    check("tail armed, not fired yet", sync_calls == 4, f"got {sync_calls}")
    forced = await app.do_sync()  # forced, direct — never throttled
    check("forced do_sync returns True", forced is True)
    n_before = sync_calls
    await asyncio.sleep(0.7)
    check("forced sync absorbed tail", sync_calls == n_before,
          f"got {sync_calls - n_before} extra")

    # --- 5. L2: concurrent anki() calls serialize (enter/exit never interleave).
    import httpx

    order = []

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"result": None, "error": None}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            order.append(("enter", json["action"]))
            await asyncio.sleep(0.05)
            order.append(("exit", json["action"]))
            return FakeResp()

    app.anki = ORIG_ANKI  # restore the REAL anki()
    httpx.AsyncClient = FakeClient
    await asyncio.gather(
        app.anki("actionA"), app.anki("actionB"), app.anki("actionC")
    )
    interleaved = any(
        order[i][0] == "enter" and order[i + 1][0] == "enter"
        for i in range(len(order) - 1)
    )
    check("concurrent anki() serialized", not interleaved, str(order))


asyncio.run(main())
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
