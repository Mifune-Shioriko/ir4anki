"""Offline split protocol regression, runnable on HEAD without native fixtures.

All state/corpus/media live in pytest tmp_path; Anki is stubbed and no app
lifespan or sync service is started. Native/flow coverage remains in the
split guard and interleave recovery suites.
"""
import asyncio
from datetime import datetime
import importlib
import os
import sys
from pathlib import Path

import httpx
import pytest

@pytest.mark.parametrize('policy', [None, 'bookmark'])
@pytest.mark.parametrize('tail', ['Unread body remains for another round.', '', '# Heading', 'x'])
def test_bookmark_protocol(tmp_path, monkeypatch, policy, tail):
    for key in list(os.environ):
        if key.startswith('ANKI_'):
            monkeypatch.delenv(key)
    for key, value in {'ANKI_BACKEND':'connect', 'ANKI_READING_MODE':'1',
                       'ANKI_STATE_DIR':str(tmp_path/'state'),
                       'ANKI_NOTES_DIR':str(tmp_path/'corpus'),
                       'ANKI_MEDIA_DIR':str(tmp_path/'media')}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    sys.modules.pop('app', None)
    b = importlib.import_module('app')
    async def anki(*args, **kwargs):
        return []
    monkeypatch.setattr(b, 'anki', anki)
    monkeypatch.setattr(b, 'fire_and_forget_sync', lambda: None)
    notes = tmp_path/'corpus'; notes.mkdir(parents=True)
    text = 'Read prefix body.\nSelected first body.\nRead middle body.\nSelected second body.\n'+tail
    (notes/'test.md').write_text(text)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://test') as c:
            assert (await c.post('/api/reading/list/add', json={'path':'test.md'})).status_code == 200
            data=b._reading_read(); parent=data['list'][0]['segments'][0]
            data['round']={'status':'active','created':datetime.now().isoformat(),'pending':[{'path':'test.md','chunk_key':str(parent['seg_id'])}], 'done':0,'total':1}
            b._reading_write(data)
            request={'path':'test.md','seg_id':parent['seg_id'], 'selections':[{'start_line':2,'end_line':2},{'start_line':4,'end_line':4}],
                     'fingerprint':parent['fingerprint'],'line_start':parent['start_line']}
            def snapshot():
                return {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
            for rejected in ('extract','unknown'):
                before=snapshot()
                r=await c.post('/api/reading/split',json={**request,'gap_policy':rejected})
                assert r.status_code==422,r.text
                assert snapshot()==before
            before=snapshot()
            r=await c.post('/api/reading/split',json={**request,'fingerprint':'stale'})
            assert r.status_code==409,r.text
            if policy is not None: request['gap_policy']=policy
            r=await c.post('/api/reading/split',json=request)
            assert r.status_code==200,r.text
            result=r.json(); assert 'gap_policy' not in result
            children=result['children']
            assert [ch['status'] for ch in children[:4]]==['background','todo','background','todo']
            worthwhile=tail.startswith('Unread')
            assert children[-1]['status']==('todo' if worthwhile else 'background')
            assert bool(children[-1]['tail'])==worthwhile
            assert len(result['child_chunks'])==2
            after=b._reading_read(); pending=after['round']['pending']
            assert len(pending)==2
            assert str(children[-1]['seg_id']) not in [p['chunk_key'] for p in pending]
            assert after['list'][0]['segments'][0]['status']=='container'
            assert (notes/'test.md').read_text()==text
            if worthwhile:
                for ch in result['child_chunks']:
                    await c.post('/api/reading/act', params={'path':'test.md','chunk_key':ch['chunk_key'],'action':'complete'})
                await c.post('/api/reading/finish')
                r=await c.post('/api/reading/start')
                assert str(children[-1]['seg_id']) in [ch['chunk_key'] for ch in r.json()['chunks']]
    try: asyncio.run(run())
    finally: sys.modules.pop('app',None)
