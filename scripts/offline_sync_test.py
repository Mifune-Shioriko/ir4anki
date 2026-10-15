"""Protected manual sync, failure barrier, offline continuation; native collection."""
import asyncio
from pylib_backend_test import seed, client_type
from pylib_test_support import run_alive

def test_explicit_failed_sync_never_revives_exported_undo(tmp_path):
    path=tmp_path/'collection.anki2';cid=seed(path)
    async def run():
        c=client_type()(path,sync_endpoint='http://127.0.0.1:1/',sync_hkey='fixture-only')
        try:
            await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
            assert (await c.call('sync'))['deferred']
            # Trap before native Rust can open a socket; only explicit sync reaches here.
            col=c._col;attempts=[]
            def fail(*a,**k):
                attempts.append('collection');raise RuntimeError('network trapped')
            col.sync_collection=fail
            try: await c.call('sync',{'commit_undo':True})
            except Exception as e: assert 'sync failed' in str(e)
            else: assert False
            assert attempts==['collection']
            s=await c.call('engineStatus')
            assert s['journal_phase']=='exporting' and not s['undo_available']
            assert s['sync_stage']=='failed' and s['last_sync_success'] is None
            assert s['local_changes_pending']
            await c.close();c=client_type()(path,sync_endpoint='http://127.0.0.1:1/',sync_hkey='fixture-only')
            s=await c.call('engineStatus')
            assert s['last_sync_error'] and not s['undo_available']
            def forbidden(*a,**k): raise AssertionError('ordinary grade attempted sync')
            c._sync=forbidden
            await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
            assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['reps']==2
            token=await c.call('nativeUndoToken',{'card':cid})
            await c.call('nativeUndo',{'card':cid,'token':token})
            assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['reps']==1
        finally:await c.close()
    run_alive(run())


def test_media_failure_never_reports_full_success(tmp_path):
    from anki.sync_pb2 import SyncCollectionResponse
    p=tmp_path/'collection.anki2';seed(p)
    async def run():
        c=client_type()(p,sync_endpoint='http://127.0.0.1:1/',sync_hkey='fixture-only')
        try:
            await c.call('engineStatus')
            c._col.sync_collection=lambda *a,**k:SyncCollectionResponse(required=SyncCollectionResponse.NORMAL_SYNC)
            def fail(*a,**k):raise RuntimeError('media network failure injected')
            c._col.sync_media=fail
            try:await c.call('sync')
            except Exception as e:assert 'sync failed' in str(e)
            else:assert False
            s=await c.call('engineStatus')
            assert s['last_collection_sync'] and not s['last_sync_success']
            assert s['sync_stage']=='failed' and c._sync_meta['failed_stage']=='media'
        finally:await c.close()
    run_alive(run())


def test_corrupt_optional_sync_status_does_not_block_offline_collection(tmp_path):
    p=tmp_path/'collection.anki2';cid=seed(p)
    from pathlib import Path
    Path(str(p)+'.ir4anki-sync.json').write_text('{broken')
    async def run():
        c=client_type()(p)
        try:
            assert await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]}) == [True]
            assert (await c.call('engineStatus'))['undo_available']
        finally:await c.close()
    run_alive(run())
