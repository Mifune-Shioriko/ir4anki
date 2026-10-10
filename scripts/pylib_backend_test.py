"""Real-engine adapter contract. All collections are temporary, never live."""
import asyncio
from pylib_test_support import run_alive
import sys
from pathlib import Path
import pytest
from anki.collection import Collection
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

def seed(path):
    col = Collection(str(path))
    col.set_config('fsrs', True)
    note = col.new_note(col.models.by_name('Basic'))
    note['Front'], note['Back'] = 'Adapter question', 'Adapter answer'
    col.add_note(note, col.decks.id('Default'))
    cid = list(col.find_cards(f'nid:{note.id}'))[0]
    col.close()
    return cid

def client_type():
    try:
        from anki_backend import PylibClient
    except ImportError:
        pytest.fail('PylibClient is not implemented yet')
    return PylibClient

def test_reads_real_engine_and_preserves_wire(tmp_path):
    path = tmp_path/'collection.anki2'
    cid = seed(path)
    async def run():
        client = client_type()(path)
        try:
            assert await client.call('findCards', {'query':'is:new'}) == [cid]
            infos = await client.call('cardsInfo', {'cards':[cid,1]})
            assert infos[0]['cardId'] == cid
            assert 'Adapter question' in infos[0]['question']
            assert infos[0]['fields']['Front']['value'] == 'Adapter question'
            assert infos[1] == {}
            notes = await client.call('notesInfo', {'notes':[infos[0]['note'],1]})
            assert notes[0]['cards'] == [cid]
            assert notes[1] == {}
            assert await client.call('modelFieldNames', {'modelName':'Basic'}) == ['Front','Back']
        finally:
            await client.close()
    run_alive(run())


def test_native_answer_undo_restores_fsrs_and_revlog(tmp_path):
    import sqlite3
    path = tmp_path/'collection.anki2'
    cid = seed(path)
    async def run():
        client = client_type()(path)
        async def snapshot():
            def read():
                col = client._open()
                return col.db.all('select * from cards'), col.db.all('select * from revlog')
            return await asyncio.get_running_loop().run_in_executor(client._executor, read)
        try:
            before = await snapshot()
            assert await client.call('answerCards', {'answers':[{'cardId':cid,'ease':3}]}) == [True]
            assert await snapshot() != before
            memory = await asyncio.get_running_loop().run_in_executor(
                client._executor, lambda: client._col.get_card(cid).memory_state)
            assert memory is not None, 'real FSRS memory state must be populated'
            token = await client.call('nativeUndoToken', {'card':cid})
            assert token is not None
            assert await client.call('nativeUndo', {'card':cid,'token':token}) == [True]
            assert await snapshot() == before
            assert await client.call('answerCards', {'answers':[{'cardId':1,'ease':3}]}) == [False]
            await client.call('answerCards', {'answers':[{'cardId':cid,'ease':3}]})
            token = await client.call('nativeUndoToken', {'card':cid})
            await client.call('suspend', {'cards':[cid]})
            with pytest.raises(Exception, match='undo'):
                await client.call('nativeUndo', {'card':cid,'token':token})
            with pytest.raises(Exception, match='unsafe'):
                await client.call('setSpecificValueOfCard', {'card':cid,'keys':['ivl'],'newValues':[1]})
        finally:
            await client.close()
    run_alive(run())


def test_safety_missing_collection_owner_lock_and_backup(tmp_path):
    async def run():
        cls = client_type()
        missing = cls(tmp_path/'missing.anki2')
        with pytest.raises(Exception, match='existing'):
            await missing.call('findCards', {'query':''})
        assert not (tmp_path/'missing.anki2').exists()
        await missing.close()
        path = tmp_path/'collection.anki2'
        seed(path)
        first, second = cls(path), cls(path)
        try:
            await first.call('findCards', {'query':''})
            with pytest.raises(Exception, match='owner'):
                await second.call('findCards', {'query':''})
            assert await first.call('createBackup',{'folder':str(tmp_path/'backups')})
            assert list((tmp_path/'backups').glob('*.colpkg'))
            with pytest.raises(Exception, match='sync'):
                await first.call('sync')
        finally:
            await first.close()
            await second.close()
        reopened = cls(path)
        try:
            assert await reopened.call('findCards',{'query':''})
        finally:
            await reopened.close()
    run_alive(run())


def test_cancellation_cannot_leave_committed_write_without_acknowledgment(tmp_path):
    import threading
    path = tmp_path/'collection.anki2'
    cid = seed(path)
    async def run():
        client = client_type()(path)
        original = client._dispatch
        started, release = threading.Event(), threading.Event()
        def delayed(action, params):
            if action == 'answerCards':
                started.set()
                assert release.wait(timeout=5)
            return original(action, params)
        client._dispatch = delayed
        try:
            task = asyncio.create_task(client.call('answerCards', {'answers':[{'cardId':cid,'ease':3}]}))
            while not started.is_set():await asyncio.sleep(.001)
            task.cancel()
            await asyncio.sleep(.01)
            try:
                assert not task.done(), 'caller escaped while committed result/state update still pending'
            finally: release.set()
            assert await task == [True]
            assert (await client.call('cardsInfo', {'cards':[cid]}))[0]['reps'] == 1
        finally:
            release.set()
            await client.close()
    run_alive(run())


def test_cancelled_close_drains_queue_releases_owner_and_persists_answer(tmp_path):
    import threading
    path = tmp_path/'collection.anki2'
    cid = seed(path)
    async def run():
        cls = client_type()
        client = cls(path)
        original = client._dispatch
        started, release = threading.Event(), threading.Event()
        def delayed(action, params):
            started.set()
            assert release.wait(timeout=5)
            return original(action, params)
        client._dispatch = delayed
        writer = asyncio.create_task(client.call('answerCards', {'answers':[{'cardId':cid,'ease':3}]}))
        while not started.is_set():await asyncio.sleep(.001)
        closer = asyncio.create_task(client.close())
        while not client._closed:await asyncio.sleep(.001)
        closer.cancel()
        await asyncio.sleep(.01)
        try:
            assert not closer.done(), 'cancelled close must still wait for queued cleanup'
        finally:release.set()
        assert await writer == [True]
        await closer
        assert client._col is None and client._lock_file is None
        reopened = cls(path)
        try:
            assert (await reopened.call('cardsInfo', {'cards':[cid]}))[0]['reps'] == 1
            revlogs = await asyncio.get_running_loop().run_in_executor(
                reopened._executor,lambda:reopened._col.db.scalar('select count(*) from revlog where cid=?',cid))
            assert revlogs == 1
        finally:await reopened.close()
    run_alive(run())


def test_note_lifecycle_duplicates_cloze_and_media(tmp_path):
    import base64
    path = tmp_path/'collection.anki2'
    seed(path)
    async def run():
        client = client_type()(path)
        try:
            request = {'deckName':'Test deck','modelName':'Basic',
                       'fields':{'Front':'New unique','Back':'Back'},'tags':['alpha'],
                       'options':{'allowDuplicate':False}}
            ids = await client.call('addNotes', {'notes':[request]})
            nid = ids[0]
            assert nid
            assert await client.call('addNotes', {'notes':[request]}) == [None]
            cid = (await client.call('findCards', {'query':f'nid:{nid}'}))[0]
            await client.call('changeDeck', {'cards':[cid],'deck':'Pool'})
            await client.call('suspend', {'cards':[cid]})
            assert (await client.call('cardsInfo',{'cards':[cid]}))[0]['queue'] == -1
            await client.call('unsuspend', {'cards':[cid]})
            await client.call('updateNoteFields', {'note':{'id':nid,'fields':{'Back':'Changed'}}})
            await client.call('updateNoteTags', {'note':nid,'tags':['beta']})
            note = (await client.call('notesInfo',{'notes':[nid]}))[0]
            assert note['tags'] == ['beta']
            assert note['fields']['Back']['value'] == 'Changed'
            assert 'beta' in await client.call('getTags')
            raw = b'\x89PNG\r\n\x1a\n'
            name = await client.call('storeMediaFile',{'filename':'test.png','data':base64.b64encode(raw).decode()})
            assert (tmp_path/'collection.media'/name).read_bytes() == raw
            with pytest.raises(Exception):
                await client.call('storeMediaFile',{'filename':'../outside.png','data':'AA=='})
            cloze = {'deckName':'Test deck','modelName':'Cloze',
                     'fields':{'Text':'A {{c1::one}} and {{c2::two}}','Back Extra':''}}
            cloze_nid = (await client.call('addNotes',{'notes':[cloze]}))[0]
            assert len(await client.call('findCards',{'query':f'nid:{cloze_nid}'})) == 2
            await client.call('deleteNotes', {'notes':[nid,cloze_nid]})
            assert await client.call('findCards', {'query':f'nid:{nid}'}) == []
        finally:
            await client.close()
    run_alive(run())
