"""Real native engine, ASGI transport, disposable corpus/state/collection."""
import asyncio, os, sys
from pathlib import Path
from native_fixture import configure, NativeData, run_closed
os.environ.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='1')
configure()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import app as b
import httpx
b.fire_and_forget_sync=lambda:None
async def run():
    seed=NativeData(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
        async def req(path,**kw):
            r=await c.post(path,**kw);assert r.status_code==200,r.text;return r.json()
        async def state():
            r=await c.get('/api/flow/state');assert r.status_code==200,r.text;return r.json()
        for s,n in [(3,8),(7,1),(0,3),(3,0),(0,0),(1,1)]:
            await req('/api/flow/exit')
            seed.clear()
            data=b._reading_read();data['list']=[];data['round']=None;b._reading_write(data)
            for i in range(s):
                name=f'{s}-{n}-{i}.md'
                (Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Passage\n\nReading text with sufficient content.\n')
                await req('/api/reading/list/add',json={'path':name})
            for i in range(n):seed.add_card(10000+i,deck='2026',note=100000+i,ctype=0)
            d=await req('/api/flow/start')
            assert d['flow']['reading_slots']==s and d['flow']['card_count']==n,d
            if d['flow']['phase']!='complete':
                assert (await req('/api/flow/start'))['flow']['id']==d['flow']['id']
            seq=[]
            while d['flow']['phase']!='complete':
                phase=d['flow']['phase']
                assert (await state())['flow']['phase']==phase # refresh either phase
                if phase=='reading':
                    seq.append('R');ch=d['reading_round']['chunks'][0]
                    await req('/api/reading/act',params=dict(path=ch['path'],chunk_key=ch['chunk_key'],action='next'))
                else:
                    seq.append('C');card=d['cards'][0]
                    await req('/api/answer',params=dict(card_id=card['cardId'],ease=3))
                d=await state()
                if seq==['R','C','C'] and s==3:
                    assert d['flow']['phase']=='reading'
                    # Native journal fallback must survive note edits, creation and sync attempt.
                    last=b.current_round()['last']['cardId']
                    note=(await b.anki('cardsInfo',{'cards':[last]}))[0]['note']
                    await b.anki('updateNoteTags',{'note':note,'tags':['edited']})
                    await b.anki('updateNoteFields',{'note':{'id':note,'fields':{'背面':'Edited after answering'}}})
                    created=await req('/api/card/add',json={'fields':{'正面':'New today','背面':'Answer'},'tags':[]})
                    assert not await b.do_sync()
                    assert (await b.anki('engineStatus'))['sync_deferred']
                    await b._pylib.close()
                    from anki_backend import PylibClient
                    b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
                    recovery=await b.anki('engineRoundRecovery')
                    assert recovery['after']==b._round_read()
                    
                    await req('/api/undo')
                    edited=(await b.anki('notesInfo',{'notes':[note]}))[0]
                    assert 'edited' in edited['tags'] and edited['fields']['背面']['value']=='Edited after answering'
                    made=(await b.anki('findCards',{'query':f"nid:{created['noteId']}"}))[0]
                    assert (await b.anki('cardsInfo',{'cards':[made]}))[0]['queue']==-1
                    u=await state();assert u['flow']['phase']=='review' and u['flow']['stats']['newReviewed']==1,u
                    await req('/api/answer',params=dict(card_id=last,ease=3))
                    d=await state();assert d['flow']['phase']=='reading'
            expected=[]
            if s:
                for i in range(1,s+1):expected+=['R']+['C']*((i*n)//s-((i-1)*n)//s)
            else:expected=['C']*n
            assert seq==expected,(seq,expected)
            assert d['flow']['stats']['reviewed']==n and d['flow']['stats']['newReviewed']==n,d
            assert (await state())['flow']['stats']==d['flow']['stats']
        # Legacy reading-only round: adoption deals cards once; preserves reading queue.
        await req('/api/flow/exit');seed.clear()
        await req('/api/reading/start')
        old=b._reading_read()['round']['pending'].copy()
        legacy=await state();assert legacy['flow']['legacy']
        assert b._reading_read()['round']['pending']==old
        await req('/api/flow/end-reading');assert (await state())['flow']['phase']=='complete'
    print('native integration: ratios, refresh, idempotence, journal undo, summary, legacy passed')
asyncio.run(run_closed(b,run()))
