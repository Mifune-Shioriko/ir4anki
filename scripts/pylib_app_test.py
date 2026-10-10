"""Business flow against real pylib + isolated corpus/state, no fake plugin."""
import asyncio
from pylib_test_support import run_alive
import importlib
import sys
from pathlib import Path
import httpx
import pytest
from anki.collection import Collection
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))


def test_app_real_engine_flow(tmp_path,monkeypatch):
    path=tmp_path/'collection.anki2'
    col=Collection(str(path));col.close()
    corpus=tmp_path/'notes';corpus.mkdir();(corpus/'test.md').write_text('# Topic\n\nA substantial isolated reading passage to make cards from.\n')
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(path),
                'ANKI_STATE_DIR':str(tmp_path/'state'),'ANKI_NOTES_DIR':str(corpus),
                'ANKI_PREVIEW_MODE':'1','ANKI_READING_MODE':'1',
                'ANKI_ADD_MODEL':'Basic','ANKI_ADD_CLOZE_MODEL':'Cloze',
                'ANKI_BACKUP_DIR':str(tmp_path/'backups')}.items():monkeypatch.setenv(k,v)
    sys.modules.pop('app',None)
    app=importlib.import_module('app')
    app.fire_and_forget_sync=lambda:None
    async def run():
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://test') as client:
                status=(await client.get('/api/status')).json()
                assert status['anki_backend']=='pylib'
                info=(await client.get('/api/card/add/info')).json()
                assert info['fields']==['Front','Back']
                req={'fields':{'Front':'Integration question','Back':'Integration answer'},'tags':['integration']}
                result=await client.post('/api/card/add',json=req)
                assert result.status_code==200,result.text
                nid=result.json()['noteId']
                cid=(await app.anki('findCards',{'query':f'nid:{nid}'}))[0]
                card=(await app.anki('cardsInfo',{'cards':[cid]}))[0]
                assert card['queue']==-1
                assert (await client.post('/api/card/add',json=req)).status_code==409
                await app.anki('changeDeck',{'cards':[cid],'deck':'2026'})
                await app.anki('unsuspend',{'cards':[cid]})
                started=await client.post('/api/session/start')
                assert started.status_code==200,started.text
                answered=await client.post(f'/api/answer?card_id={cid}&ease=3')
                assert answered.status_code==200,answered.text
                assert answered.json()['answered']
                state=(await client.get('/api/session/state')).json()
                assert state['can_undo']
                restored=await client.post('/api/undo')
                assert restored.status_code==200,restored.text
                assert restored.json()['restored']
                assert (await app.anki('cardsInfo',{'cards':[cid]}))[0]['type']==0
                await client.post(f'/api/answer?card_id={cid}&ease=3')
                await app.anki('updateNoteTags',{'note':nid,'tags':['edited']})
                state=(await client.get('/api/session/state')).json()
                assert state['can_undo']
                assert (await client.post('/api/undo')).status_code==200
            # Cancelling a backup in flight must not make the periodic task
            # swallow cancellation and loop forever during lifespan shutdown.
            import threading
            started, release = threading.Event(), threading.Event()
            original = app._pylib._dispatch
            def delayed(action, params):
                if action == 'createBackup':
                    started.set()
                    assert release.wait(timeout=5)
                return original(action, params)
            app._pylib._dispatch = delayed
            stop = asyncio.Event()
            periodic = asyncio.create_task(app._periodic_backup(stop, interval=.001))
            while not started.is_set():await asyncio.sleep(.001)
            stop.set();periodic.cancel();release.set()
            await asyncio.wait_for(periodic,timeout=1)
            app._pylib._dispatch = original
        assert list((tmp_path/'backups').glob('*.colpkg'))
    try:run_alive(run())
    finally:sys.modules.pop('app',None)


def test_engine_http_security_and_restart_round(tmp_path,monkeypatch):
    path=tmp_path/'collection.anki2'
    from pylib_backend_test import seed
    cid=seed(path)
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(path),
                'ANKI_STATE_DIR':str(tmp_path/'state'),'ANKI_NOTES_DIR':str(tmp_path/'notes'),
                'ANKI_BACKUP_DIR':str(tmp_path/'backups'),'ANKI_EXPORT_DIR':str(tmp_path/'exports'),
                'ANKI_ENGINE_API_TOKEN':'temporary-test-token'}.items():monkeypatch.setenv(k,v)
    (tmp_path/'notes').mkdir()
    sys.modules.pop('app',None);app=importlib.import_module('app');app.fire_and_forget_sync=lambda:None
    async def run():
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://test') as c:
                assert (await c.get('/api/engine/stats')).status_code==403
                c.headers['Authorization']='Bearer temporary-test-token'
                assert (await c.get('/api/engine/stats')).json()['source']=='anki'
                export=(await c.post('/api/engine/export')).json()
                assert (await c.get(export['download_url'])).content
                backup=(await c.post('/api/engine/backup')).json()
                assert (await c.get(backup['download_url'])).content
                backup_file=tmp_path/'backups'/backup['handle']
                original_bytes=backup_file.read_bytes()
                original_response=app.FileResponse
                def overwrite_before_stream(path, **kwargs):
                    Path(path).write_bytes(b'replaced-after-provenance-check')
                    return original_response(path, **kwargs)
                app.FileResponse=overwrite_before_stream
                try:
                    assert (await c.get(backup['download_url'])).content == original_bytes
                finally:
                    app.FileResponse=original_response
                    backup_file.write_bytes(original_bytes)
                assert (await c.get('/api/engine/download/export/..colpkg')).status_code==404
                app._round_write({'status':'active','created':'2026-10-10T00:00:00','pending':[cid], 'done_count':0,'total':1})
                # Recovery backups may capture an undoable answer but must
                # never become a downloadable exported copy after it is undone.
                assert (await c.post('/api/answer', params={'card_id':cid,'ease':3})).status_code == 200
                pending_backup=(await c.post('/api/engine/backup')).json()
                assert (await c.post('/api/undo')).status_code == 200
                assert (await c.get(pending_backup['download_url'])).status_code == 409
                assert (await c.post(f'/api/answer?card_id={cid}&ease=3')).status_code==200
                assert (await c.post('/api/engine/sync',json={})).json()['deferred']
                # Simulate loss of the round write following a durable answer.
                app._round_write({'status':'active','created':'2026-10-10T00:00:00','pending':[cid], 'done_count':0,'total':1})
        sys.modules.pop('app',None)
        reopened=importlib.import_module('app');reopened.fire_and_forget_sync=lambda:None
        async with reopened.app.router.lifespan_context(reopened.app):
            assert await reopened._round_can_undo(reopened.current_round())
            assert reopened.current_round()['done_count']==1
            assert (await reopened._undo_impl())['restored']
    try:run_alive(run())
    finally:sys.modules.pop('app',None)


@pytest.mark.parametrize('crash_after_delete',[False,True])
def test_delete_pending_card_preserves_restart_and_prior_undo(tmp_path,monkeypatch,crash_after_delete):
    from pylib_backend_test import seed
    p=tmp_path/'collection.anki2';a=seed(p)
    col=Collection(str(p));note=col.new_note(col.models.by_name('Basic'))
    note['Front']='delete B';note['Back']='answer B';col.add_note(note,col.decks.id('Default'))
    b=list(col.find_cards(f'nid:{note.id}'))[0];col.close()
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(p),'ANKI_STATE_DIR':str(tmp_path/'state'),
                'ANKI_NOTES_DIR':str(tmp_path/'notes'),'ANKI_BACKUP_DIR':str(tmp_path/'backups')}.items():monkeypatch.setenv(k,v)
    (tmp_path/'notes').mkdir();sys.modules.pop('app',None)
    module=importlib.import_module('app');module.fire_and_forget_sync=lambda:None
    async def run():
        async with module.app.router.lifespan_context(module.app):
            module._round_write({'status':'active','created':__import__('datetime').datetime.now().isoformat(),'pending':[a,b],'done_count':0,'total':2})
            await module._answer_impl(a,3)
            if crash_after_delete:
                async def crash(*args):raise RuntimeError('injected deletion bookkeeping crash')
                module._round_remove_card=crash
                with pytest.raises(RuntimeError,match='bookkeeping crash'):
                    await module._delete_card_impl(b)
            else:
                assert (await module._delete_card_impl(b))['deleted']
        sys.modules.pop('app',None);reopened=importlib.import_module('app');reopened.fire_and_forget_sync=lambda:None
        async with reopened.app.router.lifespan_context(reopened.app):
            await reopened.restore_round_cards(reopened.current_round())
        sys.modules.pop('app',None);reopened=importlib.import_module('app');reopened.fire_and_forget_sync=lambda:None
        async with reopened.app.router.lifespan_context(reopened.app):
            assert await reopened._round_can_undo(reopened.current_round())
            assert (await reopened._undo_impl())['restored']
            assert reopened.current_round()['pending']==[a]
            assert (await reopened.anki('cardsInfo',{'cards':[b]}))==[{}]
    try:run_alive(run())
    finally:sys.modules.pop('app',None)


def test_undo_round_write_crash_recovery(tmp_path,monkeypatch):
    from pylib_backend_test import seed
    p=tmp_path/'collection.anki2';cid=seed(p)
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(p),'ANKI_STATE_DIR':str(tmp_path/'state'),
                'ANKI_NOTES_DIR':str(tmp_path/'notes'),'ANKI_BACKUP_DIR':str(tmp_path/'backups')}.items():monkeypatch.setenv(k,v)
    (tmp_path/'notes').mkdir();sys.modules.pop('app',None)
    app=importlib.import_module('app');app.fire_and_forget_sync=lambda:None
    async def run():
        async with app.app.router.lifespan_context(app.app):
            app._round_write({'status':'active','created':'2026-10-10T00:00:00','pending':[cid],'done_count':0,'total':1})
            await app._answer_impl(cid,3)
            write=app._round_write
            def fail(rd):raise OSError('simulated round write crash')
            app._round_write=fail
            import pytest
            with pytest.raises(OSError):await app._undo_impl()
            app._round_write=write
        sys.modules.pop('app',None);r=importlib.import_module('app');r.fire_and_forget_sync=lambda:None
        async with r.app.router.lifespan_context(r.app):
            assert r.current_round()['pending']==[cid]
            assert r.current_round()['done_count']==0
            assert not (await r.anki('engineStatus'))['sync_deferred']
    try:run_alive(run())
    finally:sys.modules.pop('app',None)


def test_finish_preserves_single_slot(tmp_path,monkeypatch):
    from pylib_backend_test import seed
    p=tmp_path/'collection.anki2';cid=seed(p)
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(p),'ANKI_STATE_DIR':str(tmp_path/'state'),
                'ANKI_NOTES_DIR':str(tmp_path/'notes'),'ANKI_BACKUP_DIR':str(tmp_path/'backups')}.items():monkeypatch.setenv(k,v)
    (tmp_path/'notes').mkdir();sys.modules.pop('app',None);a=importlib.import_module('app');a.fire_and_forget_sync=lambda:None
    async def run():
        async with a.app.router.lifespan_context(a.app):
            a._round_write({'status':'active','created':'2020-01-01T00:00:00','pending':[cid],'done_count':0,'total':1})
            # Use a current timestamp for first grade, then force old timestamp
            # after grade: persistent undo must defeat round expiration.
            from datetime import datetime
            rd=a._round_read();rd['created']=datetime.now().isoformat();a._round_write(rd)
            await a._answer_impl(cid,3)
            assert (await a.session_finish())['synced'] is False
            assert await a._round_can_undo(a.current_round())
            assert (await a._undo_impl())['restored']
    try:run_alive(run())
    finally:sys.modules.pop('app',None)


def test_new_round_preserves_undo_and_does_not_double_count(tmp_path,monkeypatch):
    from pylib_backend_test import seed
    p=tmp_path/'collection.anki2';cid=seed(p)
    for k,v in {'ANKI_BACKEND':'pylib','ANKI_COLLECTION_PATH':str(p),'ANKI_STATE_DIR':str(tmp_path/'state'),
                'ANKI_NOTES_DIR':str(tmp_path/'notes'),'ANKI_BACKUP_DIR':str(tmp_path/'backups')}.items():monkeypatch.setenv(k,v)
    (tmp_path/'notes').mkdir();sys.modules.pop('app',None);a=importlib.import_module('app');a.fire_and_forget_sync=lambda:None
    async def run():
        async with a.app.router.lifespan_context(a.app):
            from datetime import datetime
            for included in [False,True]:
                a._round_write({'status':'active','created':datetime.now().isoformat(),'pending':[cid],'done_count':0,'total':1})
                await a._answer_impl(cid,3)
                await a._write_new_round({'status':'active','created':datetime.now().isoformat(),'pending':[cid] if included else [],'done_count':0,'total':1 if included else 0})
                assert await a._round_can_undo(a.current_round())
                await a._undo_impl()
                assert a.current_round()['total']==1
                assert a.current_round()['done_count']==0
                assert a.current_round()['pending']==[cid]
    try:run_alive(run())
    finally:sys.modules.pop('app',None)
