"""Native manual policy, no remote calls, authoritative single-render answer."""
import asyncio, sys, time, json
from pathlib import Path
from native_fixture import configure, NativeData, run_closed
configure()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import app as b
async def run():
    seed=NativeData(b)
    for i in range(12): seed.add_card(700+i,deck='2026',note=7000+i)
    owner=b._pylib
    owner._sync_endpoint='http://127.0.0.1:1/'
    owner._sync_username='fixture';owner._sync_password='fixture'
    attempts=[]
    def forbidden():
        attempts.append('sync');raise AssertionError('ordinary local work attempted remote sync')
    owner._sync=forbidden
    assert b.SYNC_POLICY == 'manual'
    b.fire_and_forget_sync()
    await asyncio.sleep(.02)
    assert not b._sync_pending and b._sync_timer is None
    assert await b.do_sync() is False
    created = await b.add_card(b.NoteAdd(kind='qa', fields={'正面':'Offline authored','背面':'Local answer'}, tags=[]))
    await b.delete_card(created['cardIds'][0])
    note = await b.get_note(700)
    await b.update_note(b.NoteUpdate(card_id=700, fields=note['fields'], tags=note['tags']))
    assert not (await b.start_session())["synced"]
    assert not (await b.session_finish())["synced"]
    await b.flow_start()
    original=b.anki;calls=[]
    async def observed(action,params=None,**kw):
        if action=='cardsInfo': calls.append(len(params['cards']))
        return await original(action,params,**kw)
    b.anki=observed
    times=[]
    for cid in range(700,712):
        calls.clear();start=time.perf_counter();r=await b.answer(cid,3);times.append(time.perf_counter()-start)
        assert r['answered'] and 'state' in r, r
        assert calls == ([1] if cid<711 else []), calls
        assert r['state']['flow']['card_handled']==cid-699
        assert r['state']['can_undo']
        assert not (await b.answer(cid,3))['answered'], 'response-loss retry double grades'
    assert not attempts, attempts
    await owner._wait_owner(asyncio.get_running_loop().run_in_executor(owner._executor, owner._close))
    assert (await b.flow_state())['can_undo'], 'restart lost undo'
    await b.undo()
    assert (await b.flow_state())['cards'][0]['cardId']==711
    assert seed.native(lambda c:c.db.scalar('select count(*) from revlog'))==11
    print(json.dumps({'native_answer_seconds':times,'render_batches':'[1] next card / [] final','network_attempts':attempts}))
if __name__=='__main__':asyncio.run(run_closed(b,run()))
