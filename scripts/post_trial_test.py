"""Native duplicate-render critical path probe; disposable fixture, deliberate latency."""
import asyncio, json, os, sys, time
from pathlib import Path
from native_fixture import configure, NativeData, run_closed
for key in list(os.environ):
    if key.startswith("ANKI_"): os.environ.pop(key)
configure()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import app as b
b.fire_and_forget_sync=lambda:None
async def run():
    seed=NativeData(b)
    for i in range(12): seed.add_card(700+i,deck='2026',note=7000+i)
    original=b.anki; calls=[]
    async def measured(action,params=None,**kw):
        if action=='cardsInfo':
            calls.append(list(params['cards']))
            await asyncio.sleep(.025*len(params['cards']))
        return await original(action,params,**kw)
    b.anki=measured
    rd=dict(status='active',pending=list(range(700,712)),done_count=0,total=12)
    start=time.perf_counter(); cards=await b.restore_round_cards(rd); elapsed=time.perf_counter()-start
    print(json.dumps(dict(probe='restore 12 cards',calls=calls,seconds=round(elapsed,4),deliberate_seconds_per_card=.025)),flush=True)
    assert len(cards)==12
    assert len(calls)==1, 'remaining cards rendered twice on critical path'
    assert all('fields' in card and 'tags' in card and 'ord' in card for card in cards), 'render metadata must avoid serial note request'
    b._round_write(None)
    await b.flow_start()
    calls.clear()
    start=time.perf_counter(); result=await b.answer(700,3); answered=time.perf_counter()-start
    assert result['answered']
    answer_calls=list(calls);calls.clear()
    start=time.perf_counter(); wire=await b.flow_state(); refreshed=time.perf_counter()-start
    print(json.dumps(dict(probe='answer then authoritative refresh',answer_seconds=round(answered,4),refresh_seconds=round(refreshed,4),answer_render_batches=answer_calls,refresh_render_batches=calls,deliberate_seconds_per_card=.025)),flush=True)
    assert [len(ids) for ids in answer_calls]==[12,1]
    assert [len(ids) for ids in calls]==[11]
    assert wire['flow']['card_handled']==1 and wire['flow']['card_count']==12
    assert wire['can_undo']
    await b.undo()
    wire=await b.flow_state()
    assert wire['flow']['card_handled']==0 and wire['flow']['phase']=='review'
    assert wire['cards'][0]['cardId']==700

if __name__=='__main__': asyncio.run(run_closed(b,run()))
