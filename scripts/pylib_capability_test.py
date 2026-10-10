import asyncio
from pylib_backend_test import seed, client_type

def test_native_capabilities(tmp_path):
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        try:
            info=(await c.call('cardsInfo',{'cards':[cid]}))[0]
            assert len(info['next_intervals'])==4
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
