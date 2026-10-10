"""Isolated native coordinator recovery, split/removal, stale clients and undo."""
import importlib,sys,asyncio,os
from pathlib import Path
import httpx
from native_fixture import configure,NativeData,run_closed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def test_split_removal_interruption(tmp_path,monkeypatch):
    for k,v in {'ANKI_STATE_DIR':str(tmp_path/'state'),'ANKI_NOTES_DIR':str(tmp_path/'corpus'),
                'ANKI_READING_MODE':'1','ANKI_PREVIEW_MODE':'1'}.items():monkeypatch.setenv(k,v)
    configure();sys.modules.pop('app',None);b=importlib.import_module('app')
    b.fire_and_forget_sync=lambda:None
    async def run():
        seed=NativeData(b)
        for i in range(4):seed.add_card(100+i,deck='2026',note=1000+i)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
            async def post(path,**kwargs):
                r=await c.post(path,**kwargs);assert r.status_code==200,r.text;return r.json()
            async def state():return (await c.get('/api/flow/state')).json()
            for name in ('a.md','b.md'):
                (Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Topic\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n')
                await post('/api/reading/list/add',json={'path':name})
            # Crash after both deals but before coordinator materialization.
            b._flow_write({'status':'initializing','mode':b.DEFAULT_MODE,'legacy':False})
            await b._reading_start_impl();await b._start_session_impl(False)
            original_r=b._reading_read()['round']['pending'].copy()
            original_c=b._round_read()['pending'].copy()
            d=await state();assert b._round_read()['pending']==original_c
            assert b._reading_read()['round']['pending']==original_r
            f=d['flow'];assert f['reading_slots']==2 and f['card_count']==4
            await post('/api/files/rename',json={'path':'a.md','new_name':'renamed.md'})
            d=await state();assert d['flow']['reading_consumed']==0 and d['flow']['phase']=='reading'
            ch=d['reading_round']['chunks'][0]
            # A stale device cannot grade behind a reading slot.
            assert not (await post('/api/answer',params={'card_id':100,'ease':3}))['answered']
            state_root=Path(os.environ['ANKI_STATE_DIR'])
            for policy in ('extract','unknown'):
                before={str(p):p.read_bytes() for p in state_root.rglob('*') if p.is_file()}
                rejected=await c.post('/api/reading/split',json={'path':ch['path'],'seg_id':ch['seg_id'],
                    'selections':[{'start_line':3,'end_line':3}],'gap_policy':policy})
                assert rejected.status_code==422,rejected.text
                assert before=={str(p):p.read_bytes() for p in state_root.rglob('*') if p.is_file()}
            split=await post('/api/reading/split',json={'path':ch['path'],'seg_id':ch['seg_id'],
                'selections':[{'start_line':3,'end_line':3},{'start_line':5,'end_line':5}],
                'fingerprint':ch['fingerprint'],'line_start':ch['line_start']})
            assert len(split['child_chunks'])==2,split
            tail=next(ch for ch in split['children'] if ch['tail'])
            assert tail['status']=='todo'
            assert str(tail['seg_id']) not in [ch['chunk_key'] for ch in split['child_chunks']]
            assert str(tail['seg_id']) not in [ch['chunk_key'] for ch in b._reading_read()['round']['pending']]
            d=await state();assert d['flow']['id']==f['id'] and d['flow']['phase']=='reading'
            child=d['reading_round']['chunks'][0]
            await post('/api/reading/act',params={'path':child['path'],'chunk_key':child['chunk_key'],'action':'complete'})
            d=await state();assert d['flow']['phase']=='reading' and d['flow']['reading_consumed']==0
            # Removal consumes that original slot once, keeping the next file alive.
            await post('/api/reading/list/remove',json={'path':'renamed.md'})
            d=await state();assert d['flow']['phase']=='review' and d['flow']['reading_consumed']==1
            # Stale reading action during review cannot consume the next slot.
            ch=d['reading_round']['chunks'][0]
            assert not (await post('/api/reading/act',params={'path':ch['path'],'chunk_key':ch['chunk_key'],'action':'skip'}))['ok']
            for _ in range(2):
                d=await state();await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3})
            d=await state();assert d['flow']['phase']=='reading'
            # Older than 24h must keep BOTH coordinated queues.
            rd=b._round_read();rd['created']='2000-01-01T00:00:00'
            before=b._round_read();await b.anki('engineRoundTransition',{'before':before,'after':rd});b._round_write(rd)
            data=b._reading_read();data['round']['created']='2000-01-01T00:00:00';b._reading_write(data)
            assert (await state())['flow']['phase']=='reading'
            # Reopen the official adapter and recover native round journal.
            await b._pylib.close()
            from anki_backend import PylibClient
            b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
            async with b.app.router.lifespan_context(b.app):
                await post('/api/undo');d=await state()
                assert d['flow']['phase']=='review' and d['flow']['stats']['readingDone']==1
                assert d['flow']['stats']['newReviewed']==1
                await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3})
                assert (await state())['flow']['phase']=='reading'
                await post('/api/flow/end-reading');d=await state();assert d['flow']['phase']=='review'
                for _ in range(2):
                    d=await state();await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3})
                d=await state();assert d['flow']['phase']=='complete'
                assert d['flow']['stats']=={'readingDone':1,'readingSkipped':1,'readingNext':0,'reviewed':4,'newReviewed':4}
                await post('/api/undo')
                resumed=await post('/api/flow/start') # another device has a stale completed screen
                assert resumed['flow']['id']==d['flow']['id'] and resumed['flow']['phase']=='review'
                await post('/api/flow/exit');assert (await state())['flow'] is None
    try:asyncio.run(run_closed(b,run()))
    finally:sys.modules.pop('app',None)


def test_card_removal_counts_and_legacy_resume(tmp_path,monkeypatch):
    monkeypatch.setenv('ANKI_READING_MODE','1');monkeypatch.setenv('ANKI_PREVIEW_MODE','1')
    configure();sys.modules.pop('app',None);b=importlib.import_module('app');b.fire_and_forget_sync=lambda:None
    async def run():
        seed=NativeData(b)
        for i in range(5):seed.add_card(200+i,deck='2026',note=2000+i,ctype=2 if i<3 else 0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
            async def post(path,**kwargs):
                r=await c.post(path,**kwargs);assert r.status_code==200,r.text;return r.json()
            async def state():return (await c.get('/api/flow/state')).json()
            for name in ('first.md','second.md'):
                (Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Passage\n\nReading body.\n')
                await post('/api/reading/list/add',json={'path':name})
            data=b._reading_read();source=data['list'][0]['segments'][0]
            b._reading_record_card({'path':'first.md','chunk_key':str(source['seg_id'])},2004)
            d=await post('/api/flow/start');assert d['flow']['blocks']==[2,3]
            before=d['flow'].copy()
            trace=await c.get('/api/reading/source',params={'note_id':2004})
            assert trace.status_code==200 and trace.json()['path']=='first.md'
            assert (await state())['flow']==before # trace lookup is not a slot
            await post('/api/card/delete',params={'card_id':200})
            # Foreign deletion is reconciled by actual native cardsInfo/due data.
            seed.native(lambda col:col.remove_notes([2001]))
            d=await state();assert d['flow']['stats']['reviewed']==0 and d['flow']['card_handled']==2
            ch=d['reading_round']['chunks'][0]
            await post('/api/reading/act',params={'path':ch['path'],'chunk_key':ch['chunk_key'],'action':'skip'})
            d=await state();assert d['flow']['phase']=='reading' # first block's cards disappeared
            await post('/api/flow/end-reading');d=await state();assert d['flow']['phase']=='review'
            while d['flow']['phase']=='review':
                await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3});d=await state()
            assert d['flow']['stats']['reviewed']==3 and d['flow']['stats']['newReviewed']==2
            # A fresh start with no remaining cards gets fresh zero stats.
            d=await post('/api/flow/start');assert d['flow']['stats']['reviewed']==0
            # Interrupted explicit exit resumes cleanup without touching scheduling.
            b._flow_write({'status':'exiting'});assert (await state())['flow'] is None
            # Genuine legacy partial card-only round: preserve pending order/stats;
            # adopt reading once, never redo a legacy answer.
            seed.clear()
            for i in range(3):seed.add_card(300+i,deck='2026',note=3000+i)
            b._round_write({'status':'active','created':'2000-01-01T00:00:00',
                            'pending':[300,301,302],'total':5,'done_count':2,'new_count':3})
            d=await state();assert d['flow']['legacy'] and d['flow']['stats']['reviewed']==2
            await post('/api/flow/end-reading');d=await state()
            await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3})
            d=await state();assert d['flow']['stats']['reviewed']==3
            await post('/api/undo');d=await state();assert d['flow']['stats']['reviewed']==2
            assert b._round_read()['pending']==[300,301,302]
            await post('/api/flow/exit')
            # Completed legacy tombstones are a fresh start, not a partial adoption.
            seed.add_card(450,deck='2026',note=4500)
            b._round_write({'status':'complete','created':'2000-01-01T00:00:00',
                            'pending':[],'total':2,'done_count':2})
            d=await post('/api/flow/start')
            assert 450 in b._round_read()['pending'] and d['flow']['stats']['reviewed']==0
            await post('/api/flow/exit')
            # An interrupted fresh start must deal after the intent, but not
            # repeat a committed deal even when its ready bit was not written.
            d=await post('/api/flow/start');await post('/api/flow/end-reading')
            while d['flow']['phase']!='complete':
                d=await state()
                if d['flow']['phase']=='review':await post('/api/answer',params={'card_id':d['cards'][0]['cardId'],'ease':3})
                else:break
            old=b._round_read();prior=b._reading_read().get('round')
            seed.add_card(400,deck='2026',note=4000)
            b._flow_write({'status':'initializing','mode':b.DEFAULT_MODE,'fresh':True,
                          'previous_cards':old,'previous_reading':prior})
            d=await state();assert 400 in b._round_read()['pending']
            ids=b._round_read()['pending'].copy();new=b._round_read()
            b._flow_write({'status':'initializing','mode':b.DEFAULT_MODE,'fresh':True,
                          'previous_cards':old,'previous_reading':prior,'reading_ready':True})
            await state();assert b._round_read()==new and b._round_read()['pending']==ids
            before=b._round_read().copy();b.FLOW_FILE.write_text('{broken')
            assert (await c.post('/api/flow/start')).status_code==409
            assert b._round_read()==before
    try:asyncio.run(run_closed(b,run()))
    finally:sys.modules.pop('app',None)
