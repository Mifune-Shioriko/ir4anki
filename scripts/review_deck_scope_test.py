"""Real Anki ids in disposable collections; never use live configuration."""
import asyncio
import importlib
import os
import sys
from pathlib import Path
import pytest
from native_fixture import configure, NativeData, run_closed
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

@pytest.mark.parametrize('scope', ['2026', '', 'Study "quoted" \\ path', 'Study_* literal'])
def test_native_scope(scope, monkeypatch):
    for key in list(os.environ):
        if key.startswith('ANKI_') or key == 'ANKICONNECT_URL':
            monkeypatch.delenv(key)
    monkeypatch.setenv('ANKI_REVIEW_DECK', scope)
    monkeypatch.setenv('ANKI_PREVIEW_MODE', '1')
    monkeypatch.setenv('ANKI_READING_MODE', '1')
    configure()
    sys.modules.pop('app', None)
    b = importlib.import_module('app')
    b.fire_and_forget_sync = lambda: None
    async def run():
        seed = NativeData(b)
        target = scope or '2026'
        for cid, deck, typ, suspended in [
            (100, target, 2, False), (101, target+'::child', 2, False),
            (102, 'Exam bank', 2, False), (103, target, 0, False),
            (104, target+'::child', 0, False), (105, 'Exam bank', 0, False),
            (106, target, 0, True)]:
            seed.add_card(cid, deck=deck, note=1000+cid, ctype=typ, suspended=suspended)
        expected = {100,101,103,104} if scope else {100,101,102,103,104,105}
        cards, due, new = await b.build_batch(100, allow_new=True, new_count=0)
        assert {c['cardId'] for c in cards} == expected
        assert (due,new) == ((2,2) if scope else (3,3))
        status = await b.status()
        assert (status['due_review'], status['new_total']) == (due, new)
        # An old all-deck snapshot must not control the scoped daily plan.
        b._daily_write({'day':b._anki_day(), 'due':99})
        assert await b._daily_review_size() == (2 if scope else 99)
        rd = {'status':'active','created':b.datetime.now().isoformat(),
              'pending':[102,100,105,103], 'total':4, 'done_count':0, 'new_count':2}
        b._round_write(rd)
        b._flow_write(b._flow_create([],rd['pending'],None,None,True))
        state = await b._flow_state_impl()
        assert {c['cardId'] for c in state['cards']} <= expected
        assert b._round_read()['pending'] == ([100,103] if scope else [102,100,105,103])
        if scope:
            assert not (await b._answer_impl(102,3))['answered']
            assert seed.cards[102]['type'] == 2
        if scope:
            # Native undo from an earlier all-deck round remains usable,
            # but must not return the foreign card to the recall UI.
            b._flow_write(None)
            b.REVIEW_DECK = ''
            b._round_write({'status':'active','created':b.datetime.now().isoformat(),
                            'pending':[105], 'total':1, 'done_count':0})
            assert (await b._answer_impl(105,3))['answered']
            b.REVIEW_DECK = scope
            result = await b._undo_impl()
            assert result['restored'] and result['card'] is None
            assert 105 not in b._round_read()['pending']
            assert seed.cards[105]['type'] == 0
        # Blank restores the complete pool without changing any deck/card.
        b.REVIEW_DECK = ''
        cards, _, _ = await b.build_batch(100, allow_new=True, new_count=0)
        assert {c['cardId'] for c in cards} == {100,101,102,103,104,105}
    try:
        asyncio.run(run_closed(b,run()))
    finally:
        sys.modules.pop('app',None)
