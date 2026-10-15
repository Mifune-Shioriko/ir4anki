"""Use answer's wire directly across reading boundaries; no refresh to advance."""
import os,sys,importlib,asyncio
from pathlib import Path
from native_fixture import configure,NativeData,run_closed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def test_answer_wire_cross_phase_final_undo_and_missing_card(tmp_path,monkeypatch):
    for k,v in {'ANKI_READING_MODE':'1','ANKI_PREVIEW_MODE':'0','ANKI_STATE_DIR':str(tmp_path/'state'),'ANKI_NOTES_DIR':str(tmp_path/'corpus')}.items():monkeypatch.setenv(k,v)
    configure();sys.modules.pop('app',None);b=importlib.import_module('app')
    async def run():
        native=NativeData(b)
        b._pylib._sync_endpoint='http://127.0.0.1:1/';b._pylib._sync_hkey='fixture-only'
        def forbidden():raise AssertionError('implicit sync')
        b._pylib._sync=forbidden
        for i in range(4):native.add_card(900+i,deck='2026',note=9000+i)
        for i in range(2):
            name=f'{i}.md';(Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Topic\n\nA local paragraph.\n')
            await b.reading_list_add(b.ReadingPathBody(path=name))
        d=await b.flow_start();assert d['flow']['reading_slots']==2
        async def advance_reading(d):
            ch=d['reading_round']['chunks'][0];await b.reading_act(ch['path'],ch['chunk_key'],'next')
            return await b.flow_state()
        d=await advance_reading(d);assert d['flow']['phase']=='review'
        original=b.anki;renders=[]
        async def observed(action,params=None,**kw):
            if action=='cardsInfo':renders.append(list(params['cards']))
            return await original(action,params,**kw)
        b.anki=observed
        d=(await b.answer(900,3))['state'];assert d['cards'][0]['cardId']==901
        assert renders==[[901]];renders.clear()
        d=(await b.answer(901,3))['state']
        assert d['flow']['phase']=='reading' and d['flow']['card_handled']==2
        assert d['reading_round']['chunks'] and d['can_undo'] and renders==[]
        await b.undo();d=await b.flow_state();assert d['flow']['phase']=='review'
        assert d['cards'][0]['cardId']==901
        d=(await b.answer(901,3))['state'];assert d['flow']['phase']=='reading'
        d=await advance_reading(d);assert d['flow']['phase']=='review'
        # A removed future card is reconciled without rendering/grading it.
        native.native(lambda c:c.remove_notes([9003]))
        renders.clear();d=(await b.answer(902,3))['state']
        assert renders==[] and d['flow']['phase']=='complete'
        assert d['flow']['card_handled']==4 and d['flow']['stats']['reviewed']==3
        assert d['can_undo'] and not (await b.answer(902,3))['answered']
        await b._pylib._wait_owner(asyncio.get_running_loop().run_in_executor(b._pylib._executor,b._pylib._close))
        assert (await b.flow_state())['can_undo']
        await b.undo();d=await b.flow_state()
        assert d['flow']['phase']=='review' and d['cards'][0]['cardId']==902
        assert native.native(lambda c:c.db.scalar('select count(*) from revlog'))==2
    asyncio.run(run_closed(b,run()))

def test_final_answer_does_not_render_hidden_preview_pool(tmp_path,monkeypatch):
    for k,v in {'ANKI_READING_MODE':'0','ANKI_PREVIEW_MODE':'1','ANKI_STATE_DIR':str(tmp_path/'state'),'ANKI_NOTES_DIR':str(tmp_path/'corpus')}.items():monkeypatch.setenv(k,v)
    configure();sys.modules.pop('app',None);b=importlib.import_module('app')
    async def run():
        import time
        native=NativeData(b);native.add_card(700,deck='2026',note=7000)
        for i in range(4):native.add_pool_card(int(time.time()*1000)+i)
        await b.flow_start()
        renders=[];original=b._pylib._card_info
        def observed(cid):renders.append(cid);return original(cid)
        b._pylib._card_info=observed
        d=(await b.answer(700,3))['state']
        assert d['flow']['phase']=='complete' and d['preview_pool']==4
        assert renders==[], f'final answer rendered invisible pool cards: {renders}'
    asyncio.run(run_closed(b,run()))


import pytest

@pytest.mark.parametrize('drift', ['missing-file', 'orphan-segment', 'unlisted-file'])
def test_answer_reconciles_last_reading_slot_before_render(tmp_path, monkeypatch, drift):
    """Native answer wire alone must advance past a vanished second slot."""
    monkeypatch.setenv('ANKI_READING_MODE', '1')
    monkeypatch.setenv('ANKI_PREVIEW_MODE', '0')
    configure(); sys.modules.pop('app', None); b = importlib.import_module('app')
    async def run():
        native = NativeData(b)
        b._pylib._sync_endpoint = 'http://127.0.0.1:1/'
        b._pylib._sync_hkey = 'fixture-only'
        def forbidden(): raise AssertionError('implicit network sync')
        b._pylib._sync = forbidden
        for i in range(4): native.add_card(800+i, deck='2026', note=8000+i)
        for name in ('first.md', 'last.md'):
            (Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Topic\n\nOriginal paragraph.\n')
            await b.reading_list_add(b.ReadingPathBody(path=name))
        d = await b.flow_start()
        assert d['flow']['reading_slots'] == 2 and d['flow']['blocks'] == [2, 2]
        ch = d['reading_round']['chunks'][0]
        await b.reading_act(ch['path'], ch['chunk_key'], 'next')
        d = await b.flow_state(); assert d['flow']['phase'] == 'review'
        last = d['reading_round']['chunks'][0]
        d = (await b.answer(800, 3))['state']
        path = Path(os.environ['ANKI_NOTES_DIR'])/last['path']
        if drift == 'missing-file': path.unlink()
        elif drift == 'orphan-segment': path.write_text('# Replacement\n\nCompletely unrelated content.\n')
        else:
            data = b._reading_read()
            data['list'] = [e for e in data['list'] if e['path'] != last['path']]
            b._reading_write(data)
        original = b.anki; renders = []
        async def observed(action, params=None, **kw):
            if action == 'cardsInfo': renders.append(list(params['cards']))
            return await original(action, params, **kw)
        b.anki = observed
        census_calls = []; viewed = []; file_view = b._file_view
        def observed_file_view(entry, *args, **kwargs):
            viewed.append(entry['path'])
            return file_view(entry, *args, **kwargs)
        b._file_view = observed_file_view
        async def no_census():
            census_calls.append(True)
            raise AssertionError('answer performed full reading census')
        extras = b._reading_extras; b._reading_extras = no_census
        d = (await b.answer(801, 3))['state']
        b._reading_extras = extras
        b._file_view = file_view
        assert census_calls == []
        assert viewed == ([] if drift == 'unlisted-file' else [last['path']])
        assert d['flow']['phase'] == 'review', d
        assert d['cards'][0]['cardId'] == 802 and renders == [[802]]
        assert 'reading_round' not in d and d['can_undo']
        rd = b._reading_read()['round']
        assert rd['status'] == 'complete' and rd['pending'] == [] and rd['done'] == 2
        assert d['flow']['reading_consumed'] == 2
        assert d['flow']['stats']['readingNext'] == 1
        assert d['flow']['stats']['readingSkipped'] == 0  # drift is handled, not a user skip
        for _ in range(2):
            refreshed = await b.flow_state()
            assert refreshed['flow'] == d['flow']
            assert b._reading_read()['round'] == rd
        await b.undo(); d = await b.flow_state()
        assert d['flow']['phase'] == 'review' and d['cards'][0]['cardId'] == 801
        assert b._reading_read()['round'] == rd
        d = (await b.answer(801, 3))['state']
        assert d['flow']['phase'] == 'review' and d['cards'][0]['cardId'] == 802
        d = (await b.answer(802, 3))['state']
        d = (await b.answer(803, 3))['state']
        assert d['flow']['phase'] == 'complete' and d['can_undo']
        await b._pylib.close()
        from anki_backend import PylibClient
        b._pylib = PylibClient(os.environ['ANKI_COLLECTION_PATH'])
        d = await b.flow_state(); assert d['can_undo']
        await b.undo(); d = await b.flow_state()
        assert d['flow']['phase'] == 'review' and d['cards'][0]['cardId'] == 803
        assert b._reading_read()['round'] == rd
        assert native.native(lambda c: c.db.scalar('select count(*) from revlog')) == 3
    asyncio.run(run_closed(b, run()))
