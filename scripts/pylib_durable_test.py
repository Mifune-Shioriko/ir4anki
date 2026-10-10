import asyncio
from pylib_backend_test import seed, client_type

def test_restart_deferred_sync_full_restore_preserves_note(tmp_path):
    path=tmp_path/'collection.anki2';cid=seed(path)
    async def run():
        c=client_type()(path)
        async def rows(c):
            return await c._wait_owner(asyncio.get_running_loop().run_in_executor(c._executor,lambda:(c._open().db.all('select * from cards'),c._open().db.all('select * from revlog'))))
        before=await rows(c)
        await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
        token=await c.call('nativeUndoToken',{'card':cid})
        assert (await c.call('sync'))['deferred']
        nid=(await c.call('cardsInfo',{'cards':[cid]}))[0]['note']
        await c.call('updateNoteFields',{'note':{'id':nid,'fields':{'Back':'edited'}}})
        await c.close();c=client_type()(path)
        try:
            assert await c.call('nativeUndoToken',{'card':cid})==token
            assert (await c.call('sync'))['deferred']
            await c.call('nativeUndo',{'card':cid,'token':token})
            assert await rows(c)==before
            assert (await c.call('notesInfo',{'notes':[nid]}))[0]['fields']['Back']['value']=='edited'
        finally:await c.close()
    asyncio.run(run())

def test_prepared_without_engine_write_recovers(tmp_path):
    from answer_journal import AnswerJournal
    from anki.collection import Collection
    p=tmp_path/'collection.anki2';cid=seed(p)
    col=Collection(str(p));j=AnswerJournal(p.resolve(),col)
    j.save({'phase':'prepared','cid':cid,'token':'interrupted','before':j.snapshot(cid),'round':None})
    col.close()
    async def run():
        c=client_type()(p)
        try: assert not (await c.call('engineStatus'))['sync_deferred']
        finally:await c.close()
    asyncio.run(run())

def test_process_restart_restoration_and_foreign_guard(tmp_path):
    import subprocess,sys,os,json
    p=tmp_path/'collection.anki2';cid=seed(p)
    script='''import asyncio,json,sys
from anki_backend import PylibClient
async def run():
 c=PylibClient(sys.argv[1]);cid=int(sys.argv[2])
 try:
  if sys.argv[3]=='answer':
   await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
   assert (await c.call('sync'))['deferred']
  else:
   token=await c.call('nativeUndoToken',{'card':cid})
   assert token
   await c.call('nativeUndo',{'card':cid,'token':token})
 finally:await c.close()
asyncio.run(run())
'''
    from anki.collection import Collection
    col=Collection(str(p));before=col.db.all('select * from cards'),col.db.all('select * from revlog');col.close()
    env={'PATH':os.defpath,'PYTHONPATH':str(__import__('pathlib').Path(__file__).resolve().parents[1]/'backend')}
    for action in ['answer','undo']:
        subprocess.run([sys.executable,'-c',script,str(p),str(cid),action],env=env,check=True,timeout=15)
    col=Collection(str(p))
    try:assert (col.db.all('select * from cards'),col.db.all('select * from revlog'))==before
    finally:col.close()

def test_scheduling_config_mismatch_rejects_undo(tmp_path):
    import pytest
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        try:
            await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
            token=await c.call('nativeUndoToken',{'card':cid})
            await c._wait_owner(asyncio.get_running_loop().run_in_executor(c._executor,lambda:c._col.set_config('fsrs',False)))
            with pytest.raises(Exception,match='undo'):
                await c.call('nativeUndo',{'card':cid,'token':token})
            assert (await c.call('sync'))['deferred']
        finally:await c.close()
    asyncio.run(run())

def test_prepared_after_engine_write_blocks_export_and_sync(tmp_path):
    import json,pytest
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
        await c.close()
        journal=p.with_name(p.name+'.ir4anki-answer.json')
        data=json.loads(journal.read_text());data['phase']='prepared';data.pop('after');journal.write_text(json.dumps(data))
        c=client_type()(p)
        try:
            status=await c.call('engineStatus')
            assert status['sync_deferred'] and not status['undo_available']
            assert await c.call('nativeUndoToken',{'card':cid}) is None
            with pytest.raises(Exception,match='journal'):
                await c.call('sync',{'commit_undo':True})
            with pytest.raises(Exception,match='export'):
                await c.call('exportCollection',{'path':str(tmp_path/'unsafe.colpkg')})
        finally:await c.close()
    asyncio.run(run())


def test_replaced_collection_rejects_foreign_journal(tmp_path):
    import shutil,pytest
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]});await c.close()
        copy=tmp_path/'replacement.anki2';shutil.copyfile(p,copy);copy.replace(p)
        c=client_type()(p)
        try:
            with pytest.raises(Exception,match='binding mismatch'):
                await c.call('engineStatus')
        finally:await c.close()
    asyncio.run(run())

def test_failed_prior_sync_does_not_block_local_grades(tmp_path):
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p)
        try:
            await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
            c._sync_endpoint='http://127.0.0.1:1/'
            from anki_backend import BackendError
            attempts=[]
            def fail():attempts.append(True);raise BackendError('injected sync failure')
            c._sync=fail
            for _ in range(2):
                assert await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]}) == [True]
            assert len(attempts)==2
            assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['reps']==3
            assert (await c.call('engineStatus'))['last_sync_error'] == 'injected sync failure'
            assert await c.call('nativeUndoToken',{'card':cid})
        finally:await c.close()
    asyncio.run(run())

def test_delete_answered_note_ends_undo_without_blocking_next_grade(tmp_path):
    from anki.collection import Collection
    p=tmp_path/'collection.anki2';a=seed(p)
    col=Collection(str(p));note=col.new_note(col.models.by_name('Basic'));note['Front']='next';note['Back']='b'
    col.add_note(note,col.decks.id('Default'));b=list(col.find_cards(f'nid:{note.id}'))[0];col.close()
    async def run():
        c=client_type()(p)
        try:
            await c.call('answerCards',{'answers':[{'cardId':a,'ease':3}]})
            nid=(await c.call('cardsInfo',{'cards':[a]}))[0]['note']
            await c.call('deleteNotes',{'notes':[nid]})
            assert not (await c.call('engineStatus'))['sync_deferred']
            assert await c.call('answerCards',{'answers':[{'cardId':b,'ease':3}]})==[True]
        finally:await c.close()
    asyncio.run(run())


def test_restart_undo_restores_leech_delta_preserving_user_edits(tmp_path):
    from anki.collection import Collection
    p=tmp_path/'collection.anki2';cid=seed(p)
    col=Collection(str(p));card=col.get_card(cid)
    card.type=2;card.queue=2;card.ivl=5;card.due=col.sched.today;card.lapses=7;card.reps=10
    col.update_card(card);nid=card.nid;col.close()
    async def run():
        c=client_type()(p)
        await c.call('answerCards',{'answers':[{'cardId':cid,'ease':1}]})
        assert 'leech' in (await c.call('notesInfo',{'notes':[nid]}))[0]['tags']
        token=await c.call('nativeUndoToken',{'card':cid})
        await c.call('updateNoteFields',{'note':{'id':nid,'fields':{'Back':'user edit'}}})
        await c.call('updateNoteTags',{'note':nid,'tags':['leech','user-tag']})
        await c.close();c=client_type()(p)
        try:
            await c.call('nativeUndo',{'card':cid,'token':token})
            note=(await c.call('notesInfo',{'notes':[nid]}))[0]
            assert note['tags']==['user-tag']
            assert note['fields']['Back']['value']=='user edit'
            assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['lapses']==7
        finally:await c.close()
    asyncio.run(run())


def test_skipped_backup_preserves_existing_provenance(tmp_path):
    import json
    p=tmp_path/'collection.anki2';cid=seed(p)
    async def run():
        c=client_type()(p);folder=tmp_path/'backups'
        try:
            await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
            await c.call('createBackup',{'folder':str(folder)})
            marker=next(folder.glob('*.ir4anki-provenance.json'))
            before=marker.read_bytes();assert json.loads(before)['download_safe'] is False
            token=await c.call('nativeUndoToken',{'card':cid})
            await c.call('nativeUndo',{'card':cid,'token':token})
            def skip():c._col.create_backup=lambda **kwargs:False
            await c._wait_owner(asyncio.get_running_loop().run_in_executor(c._executor,skip))
            assert await c.call('createBackup',{'folder':str(folder)}) is False
            assert marker.read_bytes()==before
        finally:await c.close()
    asyncio.run(run())


def test_crash_boundaries_preserve_atomic_answer_journal(tmp_path):
    import subprocess, sys, os
    from pathlib import Path
    script='''import asyncio,sys,os
from anki_backend import PylibClient
async def run():
 c=PylibClient(sys.argv[1]);await c.call('engineStatus')
 if sys.argv[3]=='before_commit':
  original=c._journal.save
  def crash(d):
   original(d)
   if d and d['phase']=='committed':os._exit(24)
  c._journal.save=crash
 else:
  original=c._col.db.transact
  def crash(op):original(op);os._exit(25)
  c._col.db.transact=crash
 await c.call('answerCards',{'answers':[{'cardId':int(sys.argv[2]),'ease':3}]})
asyncio.run(run())
'''
    env={'PATH':os.defpath,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'backend')}
    for mode,code in [('before_commit',24),('after_commit',25)]:
        p=tmp_path/(mode+'.anki2');cid=seed(p)
        result=subprocess.run([sys.executable,'-c',script,str(p),str(cid),mode],env=env,timeout=15)
        assert result.returncode==code
        async def check():
            c=client_type()(p)
            try:
                status=await c.call('engineStatus')
                info=(await c.call('cardsInfo',{'cards':[cid]}))[0]
                if mode=='before_commit':
                    assert info['reps']==0 and status['journal_phase'] is None
                else:
                    assert info['reps']==1 and status['undo_available']
                    token=await c.call('nativeUndoToken',{'card':cid})
                    assert await c.call('nativeUndo',{'card':cid,'token':token})==[True]
                    assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['reps']==0
            finally:await c.close()
        asyncio.run(check())


def test_crash_before_outer_commit_rolls_back_answer(tmp_path):
    import subprocess,sys,os
    from pathlib import Path
    p=tmp_path/'collection.anki2';cid=seed(p)
    script='''import asyncio,sys,os
from anki_backend import PylibClient
async def run():
 c=PylibClient(sys.argv[1]);await c.call('engineStatus')
 original=c._journal.save
 def crash(d):
  if d and d['phase']=='committed':os._exit(23)
  original(d)
 c._journal.save=crash
 await c.call('answerCards',{'answers':[{'cardId':int(sys.argv[2]),'ease':3}]})
asyncio.run(run())
'''
    env={'PATH':os.defpath,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'backend')}
    result=subprocess.run([sys.executable,'-c',script,str(p),str(cid)],env=env,timeout=15)
    assert result.returncode==23
    async def run():
        c=client_type()(p)
        try:
            status=await c.call('engineStatus')
            assert status['journal_phase'] is None
            assert not status['sync_deferred'] and not status['undo_available']
            assert (await c.call('cardsInfo',{'cards':[cid]}))[0]['reps']==0
            assert await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]}) == [True]
        finally:await c.close()
    asyncio.run(run())
