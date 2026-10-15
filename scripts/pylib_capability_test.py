import asyncio
from pylib_backend_test import seed, client_type

def test_native_capabilities(tmp_path):
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        try:
            info=(await c.call('cardsInfo',{'cards':[cid]}))[0]
            assert info['next_intervals'] is None # presentation must not calculate unused scheduler hints
            assert set(info['memory_state'])=={'stability','difficulty','retention'}
            stats=await c.call('engineStats')
            assert stats['source']=='anki' and stats['html']
            assert stats['graphs']['today']['answer_count'] == 0
            assert stats['graphs']['card_counts']['including_inactive']
            out=tmp_path/'export.colpkg'
            await c.call('exportCollection',{'path':str(out)})
            assert out.stat().st_size>0
        finally: await c.close()
    asyncio.run(run())


def test_presentation_does_not_request_hidden_scheduler_statistics(tmp_path):
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        try:
            await c.call('engineStatus')
            def fail(*args,**kwargs):raise AssertionError('presentation calculated hidden scheduling statistics')
            c._col._backend.get_scheduling_states=fail
            c._col._backend.card_stats=fail
            c._col.sched.describe_next_states=fail
            info=(await c.call('cardsInfo',{'cards':[cid]}))[0]
            assert info['question'] and info['answer'] and info['next_intervals'] is None
        finally:await c.close()
    asyncio.run(run())
