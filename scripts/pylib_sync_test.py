"""Pinned pylib sync adapter integration, disposable localhost server only."""
import asyncio
from pylib_test_support import run_alive
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import pytest
from anki.collection import Collection
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from anki_backend import PylibClient


def test_incremental_media_sync_and_full_sync_fail_closed(tmp_path):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    env={'PATH':os.defpath,'LANG':'C.UTF-8'}
    env.update(SYNC_HOST='127.0.0.1',SYNC_PORT=str(port),SYNC_BASE=str(tmp_path/'server'),
               SYNC_USER1='test:disposable-test-password',RUST_LOG='error')
    endpoint=f'http://127.0.0.1:{port}/'
    with (tmp_path/'sync.log').open('w') as log:
        proc=subprocess.Popen([sys.executable,'-m','anki.syncserver'],env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try: urllib.request.urlopen(endpoint,timeout=.1)
                except urllib.error.HTTPError: break
                except OSError: time.sleep(.05)
            else: pytest.fail('isolated sync server not ready')
            path=tmp_path/'collection.anki2'
            col=Collection(str(path));note=col.new_note(col.models.by_name('Basic'))
            note['Front']='sync question';note['Back']='original';col.add_note(note,col.decks.id('Default'));nid=note.id
            auth=col.sync_login('test','disposable-test-password',endpoint)
            col.sync_collection(auth,sync_media=False)
            col.full_upload_or_download(auth=auth,server_usn=None,upload=True);col.close()
            async def run():
                client=PylibClient(path,sync_endpoint=endpoint,sync_username='test',sync_password='disposable-test-password')
                try:
                    await client.call('updateNoteFields',{'note':{'id':nid,'fields':{'Back':'changed'}}})
                    await client.call('storeMediaFile',{'filename':'sync.txt','data':'bWVkaWE='})
                    assert await client.call('sync') is None
                finally: await client.close()
                empty=tmp_path/'empty.anki2';Collection(str(empty)).close()
                client=PylibClient(empty,sync_endpoint=endpoint,sync_username='test',sync_password='disposable-test-password')
                try:
                    with pytest.raises(Exception,match='full sync'):
                        await client.call('sync')
                finally: await client.close()
            run_alive(run())
            second=Collection(str(tmp_path/'second.anki2'))
            try:
                auth=second.sync_login('test','disposable-test-password',endpoint)
                second.sync_collection(auth,sync_media=False)
                second.full_upload_or_download(auth=auth,server_usn=None,upload=False)
                second.sync_media(auth)
                for _ in range(100):
                    if not second.media_sync_status().active:break
                    time.sleep(.05)
                assert second.get_note(nid)['Back']=='changed'
                assert (Path(second.media.dir())/'sync.txt').read_bytes()==b'media'
            finally:second.close()
        finally:
            proc.terminate();proc.wait(timeout=10)
