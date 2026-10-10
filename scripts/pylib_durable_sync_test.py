"""Actual independent clients + disposable server; never uses existing accounts."""
import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from anki.collection import Collection
from pylib_backend_test import client_type
from pylib_test_support import run_alive

@contextmanager
def server(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    endpoint=f'http://127.0.0.1:{port}/'
    # Do not inherit any sync-account settings from the calling environment.
    env={'PATH':os.defpath,'LANG':'C.UTF-8'}
    env.update(SYNC_HOST='127.0.0.1',SYNC_PORT=str(port),SYNC_BASE=str(tmp_path/'server'),
               SYNC_USER1='journal-test:temporary-password',RUST_LOG='error')
    with (tmp_path/'server.log').open('w') as log:
        proc=subprocess.Popen([sys.executable,'-m','anki.syncserver'],env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(endpoint,timeout=.1)
                except urllib.error.HTTPError:break
                except OSError:time.sleep(.05)
            else:raise AssertionError('temporary syncserver failed to start')
            yield endpoint
        finally:proc.terminate();proc.wait(timeout=10)


def test_pending_answer_never_reaches_independent_client_and_commit_flushes(tmp_path):
    with server(tmp_path) as endpoint:
        p=tmp_path/'one.anki2';second_path=tmp_path/'two.anki2'
        c=Collection(str(p));c.set_config('fsrs',True)
        ids=[]
        for front in ['first','second']:
            n=c.new_note(c.models.by_name('Basic'));n['Front']=front;n['Back']='answer'
            c.add_note(n,1);ids.append(c.find_cards(f'nid:{n.id}')[0])
        auth=c.sync_login('journal-test','temporary-password',endpoint)
        c.sync_collection(auth,sync_media=False)
        # Explicit initialization only; adapter never chooses full sync.
        c.full_upload_or_download(auth=auth,server_usn=None,upload=True);c.close()
        two=Collection(str(second_path));auth2=two.sync_login('journal-test','temporary-password',endpoint)
        two.sync_collection(auth2,sync_media=False)
        two.full_upload_or_download(auth=auth2,server_usn=None,upload=False)
        baseline=two.db.all('select * from cards'),two.db.all('select * from revlog')
        kwargs=dict(sync_endpoint=endpoint,sync_username='journal-test',sync_password='temporary-password')
        async def run():
            one=client_type()(p,**kwargs)
            try:
                await one.call('answerCards',{'answers':[{'cardId':ids[0],'ease':3}]})
                token=await one.call('nativeUndoToken',{'card':ids[0]})
                for _ in range(3):assert (await one.call('sync'))['deferred']
                two.sync_collection(auth2,sync_media=False)
                assert (two.db.all('select * from cards'),two.db.all('select * from revlog'))==baseline
                await one.close();one=client_type()(p,**kwargs)
                assert await one.call('nativeUndoToken',{'card':ids[0]})==token
                await one.call('nativeUndo',{'card':ids[0],'token':token})
                await one.call('sync')
                two.sync_collection(auth2,sync_media=False)
                assert two.db.scalar('select count(*) from revlog')==0
                assert two.get_card(ids[0]).reps==0
                await one.call('answerCards',{'answers':[{'cardId':ids[0],'ease':3}]})
                # The next grade must export prior answer BEFORE grading new card.
                await one.call('answerCards',{'answers':[{'cardId':ids[1],'ease':3}]})
                two.sync_collection(auth2,sync_media=False)
                assert two.get_card(ids[0]).reps==1
                assert two.get_card(ids[1]).reps==0
                assert two.db.scalar('select count(*) from revlog')==1
                await one.call('sync',{'commit_undo':True})
                two.sync_collection(auth2,sync_media=False)
                assert two.get_card(ids[1]).reps==1
                assert two.db.scalar('select count(*) from revlog')==2
                assert await one.call('nativeUndoToken',{'card':ids[1]}) is None
            finally:await one.close()
        try:run_alive(run())
        finally:two.close()
