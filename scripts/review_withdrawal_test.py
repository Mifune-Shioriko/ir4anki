"""Withdrawal regression: pure policy and native ASGI, exclusively scratch fixtures."""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
import pytest
from native_fixture import configure, NativeData, run_closed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from flow import create, reconcile


def test_incident_policy():
    readings=[{'path':'fixture.md','chunk_key':str(i)} for i in range(13)]
    f=create(readings,list(range(164)),'r','c')
    reconcile(f,readings[2:],list(range(45,164)),answered_count=0)
    assert (f['phase'],f['block_target'],f['card_answered'],f['effective_card_count'])==('review',18,0,119)
    # Genuine answers (including Again) earn credit even though not yet graduated.
    reconcile(f,readings[2:],list(range(63,164)),answered_count=18)
    assert (f['phase'],f['effective_card_count'])==('reading',119)
    # Later withdrawal shrinks denominator; no answer is manufactured.
    reconcile(f,readings[2:],list(range(73,164)),answered_count=18)
    assert (f['card_answered'],f['effective_card_count'],f['block_target'])==(18,109,16)
    # Queue restoration and undo increase remaining work and reopen debt.
    reconcile(f,readings[2:],list(range(45,164)),answered_count=0)
    assert (f['phase'],f['effective_card_count'])==('review',119)
    reconcile(f,readings[2:],[999]+list(range(63,164)),answered_count=18)
    assert f['phase']=='review' # prior round debt even with enough answers
    reconcile(f,[],[],answered_count=119)
    assert (f['phase'],f['effective_card_count'],f['card_answered'])==('complete',119,119)


def load_backend():
    assert os.environ.get('NATIVE_FIXTURE_ROOT'), 'explicit scratch fixture root required'
    configure()
    sys.modules.pop('app',None)
    b=importlib.import_module('app')
    b.fire_and_forget_sync=lambda:None
    return b


async def incident(b):
    """Seed persisted incident shape before reopening; no historical production data."""
    seed=NativeData(b)
    # Keep historical synthetic note IDs from triggering unrelated auto-release.
    b._auto_release_write(b.RELEASE_DAILY_GOAL)
    for i in range(164):
        seed.add_card(10000+i,deck='2026' if i>=45 else '预览池',note=100000+i,ctype=2 if i>=45 else 0,suspended=i<45)
    for i in range(13):
        name=f'read-{i}.md'
        (b.NOTES_DIR/name).write_text('# Passage\n\nFixture reading body.\n')
        await b.reading_list_add(b.ReadingPathBody(path=name))
    await b._reading_start_impl()
    data=b._reading_read(); rd=data['round']; originals=rd['pending'].copy()
    assert len(originals)==13
    rd['pending']=originals[2:];rd['done']=2;rd['stats']={'done':1,'next':1,'skipped':0};b._reading_write(data)
    card_round={'id':'fixture-cards','status':'active','created':b.datetime.now().isoformat(),
                'pending':list(range(10000,10164)),'total':164,'done_count':0,'answered_count':0,'new_answered':0,'new_count':45,'mode':'daily'}
    b._round_write(card_round)
    f=b._flow_create(originals,card_round['pending'],rd['id'],card_round['id'])
    f.update(phase='reading',card_handled=45,reading_consumed=2,block_target=25)
    b._flow_write(f)
    return seed


@pytest.mark.parametrize('current_only',[False,True])
def test_incident_native_api(monkeypatch,current_only):
    monkeypatch.setenv('ANKI_READING_MODE','1');monkeypatch.setenv('ANKI_PREVIEW_MODE','1');monkeypatch.setenv('ANKI_REVIEW_DECK','2026')
    b=load_backend()
    async def run():
        import httpx
        seed=await incident(b)
        # Restore persisted fixture with a new native owner, as after restart.
        await b._pylib.close()
        from anki_backend import PylibClient
        b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
            async def state():
                if current_only:return await b._flow_state_impl(current_only=True)
                r=await c.get('/api/flow/state'); assert r.status_code==200,r.text; return r.json()
            d=await state()
            assert (d['flow']['phase'],d['flow']['block_target'])==('review',18)
            assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(0,119)
            assert d['cards'] and d['cards'][0]['cardId']==10045
            assert seed.native(lambda col:col.db.scalar('select count(*) from revlog'))==0
            # Refresh in review must still present the same real native card.
            assert (await state())['cards'][0]['cardId']==10045
            # A persisted queue restoration grows the eligible denominator again.
            # This is fixture recovery, not an automatic production redeal.
            saved=b._round_read()
            seed.native(lambda col:col.sched.suspend_cards([10046]))
            d=await state();assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(0,118)
            seed.native(lambda col:col.sched.unsuspend_cards([10046]))
            b._round_write(saved)
            d=await state();assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(0,119)
            # Again counts as one answer in this round, independent of learning due.
            answer=await c.post('/api/answer',params={'card_id':10045,'ease':1});assert answer.status_code==200,answer.text
            d=answer.json()['state'];assert d['flow']['card_answered']==1 and d['flow']['effective_card_count']==119
            assert 10045 not in b._round_read()['pending']
            assert seed.cards[10045]['type']!=0
            # Withdrawal after an answer: one suspended + one moved out of scope.
            seed.native(lambda col:col.sched.suspend_cards([10046]))
            seed.native(lambda col:col.set_deck([10047],col.decks.id('Other')))
            d=await state();assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(1,117)
            # A final-round/native journal restart must preserve answer vs withdrawal.
            await b._pylib.close();b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
            async with b.app.router.lifespan_context(b.app):
                d=await state();assert d['flow']['card_answered']==1
            # External scheduling/deck mutations are not safe native undo:
            # preserve the existing fail-closed guard, never repair learning rows.
            b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
            before=b._round_read()
            undone=await c.post('/api/undo');assert undone.status_code==409,undone.text
            assert b._round_read()==before
            assert (await state())['flow']['card_answered']==1
            print('API incident proof: REVIEW, owed 18, progress 0/119, native card 10045, revlog initially zero')
    try:asyncio.run(run_closed(b,run()))
    finally:sys.modules.pop('app',None)


def test_legacy_counter_never_uses_withdrawals():
    # Missing historical answer counters cannot establish that handled == recalled.
    from flow import answer_count
    assert answer_count({'done_count':45,'pending':[]})==0
    assert answer_count({'done_count':45,'answered_count':2})==2


def test_prior_round_and_final_undo(monkeypatch):
    monkeypatch.setenv('ANKI_READING_MODE','0');monkeypatch.setenv('ANKI_PREVIEW_MODE','0')
    b=load_backend()
    async def run():
        seed=NativeData(b);seed.add_card(201,deck='2026',note=2001)
        d=await b.flow_start();assert d['flow']['phase']=='review'
        d=(await b.answer(201,1))['state']
        assert (d['flow']['phase'],d['flow']['card_answered'],d['flow']['effective_card_count'])==('complete',1,1)
        seed.add_card(202,deck='2026',note=2002)
        d=await b.flow_start()
        assert b._round_read()['last']['from_prior_round']
        assert d['flow']['card_answered']==0
        await b.undo();d=await b.flow_state()
        assert d['flow']['phase']=='review' and d['flow']['card_answered']==0
        assert d['flow']['effective_card_count']==len(b._round_read()['pending'])==2
        for cid in b._round_read()['pending'].copy():
            assert (await b.answer(cid,3))['answered']
        d=await b.flow_state()
        assert (d['flow']['phase'],d['flow']['card_answered'],d['flow']['effective_card_count'])==('complete',2,2)
        await b._pylib.close()
        from anki_backend import PylibClient
        b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
        async with b.app.router.lifespan_context(b.app):
            await b.undo();d=await b.flow_state()
            assert (d['flow']['phase'],d['flow']['card_answered'],d['flow']['effective_card_count'])==('review',1,2)
            assert d['cards']
    try:asyncio.run(run_closed(b,run()))
    finally:sys.modules.pop('app',None)


def test_ambiguous_legacy_round_recovery(monkeypatch):
    monkeypatch.setenv('ANKI_READING_MODE','0');monkeypatch.setenv('ANKI_PREVIEW_MODE','0')
    b=load_backend()
    async def run():
        seed=NativeData(b)
        for cid in (301,302,303):seed.add_card(cid,deck='2026',note=3000+cid)
        b._round_write({'status':'active','created':'2000-01-01T00:00:00',
                        'total':48,'done_count':45,'pending':[301,302,303],'new_count':3})
        d=await b.flow_state()
        assert d['flow']['legacy'] and d['cards'][0]['cardId']==301
        assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(0,3)
        d=(await b.answer(301,1))['state']
        assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(1,3)
        await b._pylib.close()
        from anki_backend import PylibClient
        b._pylib=PylibClient(os.environ['ANKI_COLLECTION_PATH'])
        async with b.app.router.lifespan_context(b.app):
            await b.undo();d=await b.flow_state()
            assert b._round_read()['done_count']==45
            assert (d['flow']['card_answered'],d['flow']['effective_card_count'])==(0,3)
    try:asyncio.run(run_closed(b,run()))
    finally:sys.modules.pop('app',None)
