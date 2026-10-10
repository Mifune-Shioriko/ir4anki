"""Missing-note tolerance: corrupt only disposable native fixtures, never repair data."""
import asyncio
import importlib
import os
import sys
from pathlib import Path

import httpx
import pytest
from anki.collection import Collection
from anki.cards import Card
from anki.errors import NotFoundError
from native_fixture import configure, NativeData, run_closed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def test_orphan_card_batch_and_flow(tmp_path, monkeypatch):
    env = dict(os.environ)
    env.update(ANKI_READING_MODE='1', ANKI_PREVIEW_MODE='0')
    configure(env)
    for key, value in env.items():
        if key.startswith('ANKI_'):
            monkeypatch.setenv(key, value)
    sys.modules.pop('app', None)
    app = importlib.import_module('app')
    app.fire_and_forget_sync = lambda: None

    async def run():
        seed = NativeData(app)
        for cid, nid, ctype in [(101, 1001, 0), (102, 1002, 0), (103, 1003, 2), (104, 1004, 2)]:
            seed.add_card(cid, note=nid, deck='2026', ctype=ctype)
        # Intentionally bypass native deletion ONLY in this disposable fixture:
        # retain both new and due cards while removing their notes.
        seed.native(lambda col: col.db.execute('delete from notes where id in (1001,1003)'))
        with pytest.raises(NotFoundError, match='No such note'):
            seed.native(lambda col: col.get_card(101).note())
        def snapshot(col):
            return (col.db.all('select * from cards order by id'),
                    col.db.all('select * from notes order by id'), col.db.all('select * from revlog'))
        before = seed.native(snapshot)
        infos = await app.anki('cardsInfo', {'cards': [101, 102, 103, 104]})
        assert infos[0] == {} and infos[2] == {}
        assert [infos[i]['cardId'] for i in (1, 3)] == [102, 104]
        assert (await app.anki('notesInfo', {'notes': [1001, 1002]}))[0] == {}
        assert [c['cardId'] for c in await app.fetch_cards([101, 104, 103, 102])] == [104, 102]
        batch, _, _ = await app.build_batch(100, True)
        assert {c['cardId'] for c in batch} == {102, 104}
        assert all('Q' in c['question'] and 'A' in c['answer'] for c in batch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url='http://fixture') as client:
            response = await client.post('/api/flow/start')
            assert response.status_code == 200, response.text
            wire = response.json()
            assert {c['cardId'] for c in wire['cards']} == {102, 104}
            assert wire['flow']['card_count'] == 2
            state = await client.get('/api/flow/state')
            assert state.status_code == 200, state.text
            assert {c['cardId'] for c in state.json()['cards']} == {102, 104}
        assert seed.native(snapshot) == before, 'tolerating orphans must not repair/delete collection data'
        # Unrelated integrity errors must propagate rather than become empty rows.
        def broken_note(self, reload=False):
            raise RuntimeError('unrelated corruption')
        monkeypatch.setattr(Card, 'note', broken_note)
        with pytest.raises(RuntimeError, match='unrelated corruption'):
            await app.anki('cardsInfo', {'cards': [102]})
    try:
        asyncio.run(run_closed(app, run()))
    finally:
        sys.modules.pop('app', None)
