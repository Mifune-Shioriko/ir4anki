#!/usr/bin/env python3
"""Real engine sync deferral; explicitly unconfigured sync must fail closed.

Dispatch observation records timing only; every action reaches PylibClient.
Loopback transfer coverage remains in pylib_sync_test.py (requires sockets).
"""
import asyncio
import os
import sys
from pathlib import Path
from native_fixture import configure, run_closed
configure()
os.environ['ANKI_SYNC_MIN_INTERVAL'] = '0.15'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import app as backend
PASS = FAIL = 0

def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print('  ok ', name)
    else:
        FAIL += 1
        print('  FAIL', name)

async def main():
    observed=[]
    dispatch = backend._pylib._dispatch
    def record(action, params):
        if action == 'sync': observed.append(__import__('time').monotonic())
        return dispatch(action, params)
    backend._pylib._dispatch = record
    try:
        check('real unconfigured sync fails closed', await backend.do_sync() is False)
        baseline=len(observed)
        backend.fire_and_forget_sync()
        backend.fire_and_forget_sync()
        check('native sync tail armed', backend._sync_pending and backend._sync_timer is not None)
        await asyncio.sleep(.04)
        check('native sync deferred during cooldown', len(observed) == baseline)
        await asyncio.sleep(.20)
        check('one deferred native sync attempt', len(observed) == baseline+1)
        check('failed native sync clears pending', not backend._sync_pending)
        check('deferred sync respects interval', observed[-1]-observed[-2] >= .14)
        check('native collection usable after sync failure', await backend.anki('findCards', {'query':''}) == [])
        check('native owner remains open', backend._pylib._col is not None)
    finally:
        backend._pylib._dispatch = dispatch
    print(f'\n{PASS} passed, {FAIL} failed')
    return 1 if FAIL else 0

if __name__ == '__main__':
    sys.exit(asyncio.run(run_closed(backend, main())))
