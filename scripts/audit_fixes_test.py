"""Audit regressions against actual ASGI app and disposable official Anki."""
import asyncio, importlib, os, sys, io, json
from pathlib import Path
import pytest, httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from native_fixture import configure, cleanup
from pylib_test_support import run_alive

@pytest.fixture
def backend(monkeypatch):
    for key in list(os.environ):
        if key.startswith('ANKI_') or key == 'ANKICONNECT_URL': monkeypatch.delenv(key)
    assert os.environ.get('NATIVE_FIXTURE_ROOT'), 'use scratch test runner'
    configure()
    monkeypatch.setenv('ANKI_READING_MODE','1')
    monkeypatch.setenv('ANKI_PREVIEW_MODE','1')
    sys.modules.pop('app',None)
    b=importlib.import_module('app')
    b.fire_and_forget_sync=lambda:None
    yield b
    run_alive(b._pylib.close())
    sys.modules.pop('app',None)
    cleanup()

def client(b,host='localhost'):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://'+host)

def test_access_control(backend,monkeypatch):
    b=backend
    async def run():
        async with client(b) as c:
            for headers in [{'Origin':'https://evil.example'},{'Origin':'null'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.example'},{'Host':'localhost.evil.example'}]:
                r=await c.get('/api/files/list',headers=headers)
                assert r.status_code==403,(headers,r.status_code)
            assert (await c.get('/api/files/list')).status_code==200
            assert (await c.get('/api/files/list',headers={'Origin':'http://localhost','Sec-Fetch-Site':'same-origin'})).status_code==200
            r=await c.options('/api/files/upload',headers={'Origin':'https://evil.example','Access-Control-Request-Method':'POST'})
            assert r.status_code==403 and 'access-control-allow-origin' not in r.headers
            assert (await c.get('/api/engine/stats')).status_code==403
            assert (await c.post('/api/local/sync',json={'commit_undo':True},headers={'Origin':'http://localhost'})).status_code==403
            monkeypatch.setenv('ANKI_TRUSTED_HOSTS','phone.example,100.100.1.2')
            assert (await c.get('/api/files/list',headers={'Host':'phone.example','Origin':'http://phone.example'})).status_code==200
            assert (await c.get('/api/files/list',headers={'Host':'100.100.1.2'})).status_code==200
            assert (await c.post('/api/local/sync',json={'commit_undo':True},headers={'Host':'phone.example','Origin':'http://phone.example'})).status_code==403
    run_alive(run())

def seed_reading(b, name='note.md', text='# Topic\n'+('reading passage content ' * 30)):
    (b.NOTES_DIR/name).write_text(text)
    run_alive(b.reading_list_add(b.ReadingPathBody(path=name)))
    return b._reading_read()['list'][0]['segments'][0]

@pytest.mark.parametrize("query_kind",["reading_state","status"])
def test_provenance_concurrency(backend,monkeypatch,caplog,query_kind):
    b=backend; seg=seed_reading(b)
    async def run():
        entered,release,created=asyncio.Event(),asyncio.Event(),asyncio.Event()
        original=b._reading_gates; dispatch=b.anki
        async def gate(data):
            entered.set();await release.wait();return await original(data)
        async def anki(action,params=None,**kw):
            result=await dispatch(action,params,**kw)
            if action=='suspend':created.set()
            return result
        monkeypatch.setattr(b,'_reading_gates',gate);monkeypatch.setattr(b,'anki',anki)
        query=asyncio.create_task(getattr(b,query_kind)());await entered.wait()
        add=asyncio.create_task(b.add_card(b.NoteAdd(fields={'正面':'concurrent','背面':'answer'},reading_source={'path':'note.md','chunk_key':str(seg['seg_id'])})))
        await asyncio.wait_for(created.wait(),5)
        await asyncio.sleep(.03);release.set()
        await asyncio.wait_for(query,5);result=await asyncio.wait_for(add,5)
        saved=b._reading_read()['list'][0]['segments'][0]
        assert saved['cards_created']==[result['noteId']]
        assert saved['status']=='active'
        monkeypatch.setattr(b,'_reading_record_card',lambda *a: (_ for _ in ()).throw(RuntimeError('bookkeeping fault')))
        await b.add_card(b.NoteAdd(fields={'正面':'fail soft','背面':'answer'},reading_source={'path':'note.md','chunk_key':str(seg['seg_id'])}))
        assert 'bookkeeping fault' in caplog.text
    run_alive(run())

def test_edit_conflict(backend):
    b=backend;seg=seed_reading(b);old=(b.NOTES_DIR/'note.md').read_text()
    import hashlib
    payload=dict(path='note.md',seg_id=seg['seg_id'],new_text=old.replace('Topic','Other'),expected_revision=hashlib.sha256(old.encode()).hexdigest(),expected_fingerprint=seg['fingerprint'])
    async def run():
        async with client(b) as c:
            assert (await c.post('/api/reading/edit',json=payload)).status_code==200
            saved=(b.NOTES_DIR/'note.md').read_bytes();state=b._reading_read()
            stale=dict(payload,new_text=old.replace('Topic','Stale'))
            r=await c.post('/api/reading/edit',json=stale)
            assert r.status_code==409,r.text
            assert (b.NOTES_DIR/'note.md').read_bytes()==saved and b._reading_read()==state
            fresh=b._reading_read()['list'][0]['segments'][0]
            payload.update(expected_revision=hashlib.sha256(saved).hexdigest(),expected_fingerprint=fresh['fingerprint'],new_text=old+'\nnew line')
            (b.NOTES_DIR/'note.md').write_text(old+' external')
            before=b._reading_read()
            assert (await c.post('/api/reading/edit',json=payload)).status_code==409
            assert b._reading_read()==before
            missing={k:v for k,v in payload.items() if not k.startswith('expected_')}
            assert (await c.post('/api/reading/edit',json=missing)).status_code==422
    run_alive(run())

async def upload(c,items,policy='error'):
    return await c.post('/api/files/upload',files=[('files',(p,data,'application/octet-stream')) for p,data in items],data={'rel_paths':[p for p,_ in items],'on_conflict':policy})

def test_upload_identity(backend):
    b=backend
    (b.NOTES_DIR/'x.png').write_bytes(b'existing')
    async def run():
        async with client(b) as c:
            r=await upload(c,[('a/x.png',b'A'),('b/x.png',b'B'),('c/x.png',b'C'),('a/n.md',b'![a](x.png) ![b](../b/x.png) <img src="../c/x.png"> ![external](https://other/x.png) ![other](../outside/x.png)'),('b/n.md',b'![b](x.png) <a href="../a/x.png">')])
            assert r.status_code==200,r.text
            saved={row['original']:row['path'] for row in r.json()['saved']}
            a=(b.NOTES_DIR/saved['a/n.md']).read_text();bb=(b.NOTES_DIR/saved['b/n.md']).read_text()
            assert Path(saved['a/x.png']).name in a and Path(saved['b/x.png']).name in a and Path(saved['c/x.png']).name in a
            assert Path(saved['b/x.png']).name in bb and Path(saved['a/x.png']).name in bb
            assert 'https://other/x.png' in a and '../outside/x.png' in a
            assert (b.NOTES_DIR/saved['a/x.png']).read_bytes()==b'A'
            assert (b.NOTES_DIR/'x.png').read_bytes()==b'existing'
    run_alive(run())

def test_upload_preflight(backend):
    b=backend;(b.NOTES_DIR/'old.md').write_bytes(b'original')
    async def run():
        async with client(b) as c:
            for items,policy in [([('good.md',b'good'),('bad.md',b'\xff')],'error'),([('old.md',b'replacement'),('bad.md',b'\xff')],'overwrite')]:
                r=await upload(c,items,policy)
                assert r.status_code==400,r.text
                assert not (b.NOTES_DIR/'good.md').exists()
                assert (b.NOTES_DIR/'old.md').read_bytes()==b'original'
            r=await upload(c,[('dup.md',b'one'),('dup.md',b'two')],'overwrite')
            assert r.status_code==409 and not (b.NOTES_DIR/'dup.md').exists()
            r=await upload(c,[('old.md',b'one'),('old (1).md',b'two')],'rename')
            assert r.status_code==200,r.text
            paths=[x['path'] for x in r.json()['saved']]
            assert len(set(paths))==2
            assert sorted((b.NOTES_DIR/x).read_bytes() for x in paths)==[b'one',b'two']
            assert (await upload(c,[('good.md',b'retry')])).status_code==200
    run_alive(run())

def test_upload_io_failure(backend,monkeypatch):
    b=backend;original=Path.replace
    def fail(p,target):
        if Path(target).name=='late.md':raise OSError('injected replace failure')
        return original(p,target)
    monkeypatch.setattr(Path,'replace',fail)
    async def run():
        async with client(b) as c:
            r=await upload(c,[('first.md',b'one'),('late.md',b'two')])
            assert r.status_code==503,r.text
            detail=r.json()['detail']
            assert detail['partial'] is True and detail['saved'][0]['path']=='first.md' and detail['failed']=='late.md'
            assert (b.NOTES_DIR/'first.md').read_bytes()==b'one' and not (b.NOTES_DIR/'late.md').exists()
            assert not list(b.NOTES_DIR.glob('.upload-*'))
    run_alive(run())

def test_upload_budget(backend,monkeypatch):
    b=backend;monkeypatch.setattr(b,'UPLOAD_MAX_BYTES',8);monkeypatch.setattr(b,'UPLOAD_BATCH_MAX_BYTES',12);monkeypatch.setattr(b,'UPLOAD_MAX_FILES',2)
    async def run():
        async with client(b) as c:
            for items in [[('large.md',b'123456789')],[('a.md',b'12345678'),('b.md',b'12345')],[('a.md',b'a'),('b.md',b'b'),('c.md',b'c')]]:
                r=await upload(c,items)
                assert r.status_code==413,r.text
                assert not list(b.NOTES_DIR.rglob('*'))
            assert (await upload(c,[('a.md',b'12345678'),('b.md',b'1234')])).status_code==200
        class Bounded:
            calls=[]
            async def read(self,size=-1):
                self.calls.append(size)
                assert 0 < size <= 65536
                return b'x' * min(size,9)
        f=Bounded()
        with pytest.raises(b.HTTPException) as error:await b._read_upload_bounded(f,8,'bounded')
        assert error.value.status_code==413
    run_alive(run())

def test_incremental_reading(backend,monkeypatch):
    b=backend;seed_reading(b)
    original=b._reading_conn;counts=[]
    class Connection:
        def __init__(self,c):self.c=c;self.before=c.total_changes
        def __getattr__(self,n):return getattr(self.c,n)
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*args):return self.c.__exit__(*args)
        def close(self):counts.append(self.c.total_changes-self.before);self.c.close()
    monkeypatch.setattr(b,'_reading_conn',lambda:Connection(original()))
    data=b._reading_read();counts.clear();b._reading_write(data)
    assert counts==[0],counts
    data['list'][0]['segments'][0]['status']='active';counts.clear();b._reading_write(data)
    assert counts==[1],counts
    data['list'][0]['segments'][0]['cards_created']=[42];b._reading_write(data)
    assert b._reading_read()['list'][0]['segments'][0]['cards_created']==[42]
    entry=data['list'].pop();data['archive']['note.md']=entry;b._reading_write(data)
    assert b._reading_read()['archive']['note.md']['segments'][0]['cards_created']==[42]
    data['archive'].clear();b._reading_write(data)
    assert b._reading_read()['archive']=={}
    c=original();assert c.execute('SELECT count(*) FROM rcards').fetchone()[0]==0;c.close()

def test_visible_rendering(backend,monkeypatch):
    b=backend;seg=seed_reading(b)
    from native_fixture import NativeData
    data=NativeData(b)
    for i in range(12):data.add_card(700000+i,deck='2026',note=800000+i)
    calls=[];original=b._pylib._card_info
    def render(cid):calls.append(cid);return original(cid)
    monkeypatch.setattr(b._pylib,'_card_info',render)
    async def run():
        async with client(b) as c:
            r=await c.post('/api/flow/start');assert r.status_code==200,r.text
            assert r.json()['flow']['phase']=='reading'
            assert calls==[],calls
            assert len(b._round_read()['pending'])==12
            calls.clear();st=(await c.get('/api/flow/state')).json();assert calls==[]
            r=await c.post('/api/reading/act',params={'path':'note.md','chunk_key':str(seg['seg_id']),'action':'complete'})
            assert r.status_code==200,r.text
            calls.clear();st=(await c.get('/api/flow/state')).json()
            assert st['flow']['phase']=='review' and len(st['cards'])==1
            assert len(calls)==1,calls
            pending=list(b._round_read()['pending'])
            # Restore via persisted coordinator, withdraw first card through real engine.
            await b.anki('suspend',{'cards':[pending[0]]})
            calls.clear();st=(await c.get('/api/flow/state')).json()
            assert len(calls)==1 and st['cards'][0]['cardId']==pending[1]
            seen=[]
            while st.get('cards'):
                cid=st['cards'][0]['cardId'];seen.append(cid);calls.clear()
                r=await c.post('/api/answer',params={'card_id':cid,'ease':3});assert r.status_code==200,r.text
                st=r.json()['state'];assert len(calls)<=1,calls
            assert seen==pending[1:]
            r=await c.post('/api/undo');assert r.status_code==200,r.text
            st=(await c.get('/api/flow/state')).json();assert st['cards'][0]['cardId']==seen[-1]
    run_alive(run())

@pytest.mark.parametrize('reviews',[10,20000])
def test_journal_single_guard(backend,monkeypatch,reviews):
    b=backend
    from native_fixture import NativeData
    from anki_backend import PylibClient
    import time
    data=NativeData(b);data.add_card(710000,deck='2026',note=810000)
    def history(col):
        col.db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<?) INSERT INTO revlog SELECT x,710000,-1,3,1,1,2500,100,1 FROM n',reviews)
        for i in range(300):col.set_config('audit_synthetic_'+str(i),i)
    data.native(history)
    async def run():
        await b.anki('answerCards',{'answers':[{'cardId':710000,'ease':3}]})
        token=await b.anki('nativeUndoToken',{'card':710000})
        path=b._pylib.path
        await b._pylib.close();b._pylib=PylibClient(path)
        await b.anki('engineStatus')
        journal=b._pylib._journal;original=journal.snapshot;calls=[]
        def snapshot(cid):calls.append(cid);return original(cid)
        monkeypatch.setattr(journal,'snapshot',snapshot)
        start=time.perf_counter();await b.anki('nativeUndo',{'card':710000,'token':token});elapsed=time.perf_counter()-start
        print(json.dumps({'synthetic_revlog':reviews,'config_rows_added':300,'undo_seconds':elapsed,'full_snapshots':len(calls)}))
        assert len(calls)==1,calls
        assert data.native(lambda col:col.db.scalar('select count(*) from revlog where cid=710000'))==reviews
    run_alive(run())

@pytest.mark.parametrize('mutation',["update cards set ivl=ivl+1 where id=720000", "update revlog set ease=1 where cid=720000", "update decks set mtime_secs=mtime_secs+1", "update deck_config set mtime_secs=mtime_secs+1", "update config set val='false' where key='fsrs'"])
def test_journal_external_guard(backend,mutation):
    b=backend
    from native_fixture import NativeData
    from anki_backend import PylibClient
    data=NativeData(b);data.add_card(720000,deck='2026',note=820000)
    async def run():
        await b.anki('answerCards',{'answers':[{'cardId':720000,'ease':3}]})
        token=await b.anki('nativeUndoToken',{'card':720000})
        path=b._pylib.path;await b._pylib.close();b._pylib=PylibClient(path)
        await b.anki('engineStatus');data.native(lambda col:col.db.execute(mutation))
        before=data.native(lambda col:col.db.all('select * from cards'))
        with pytest.raises(Exception,match='guard'):await b.anki('nativeUndo',{'card':720000,'token':token})
        assert data.native(lambda col:col.db.all('select * from cards'))==before
    run_alive(run())

def test_relocation_durable_order(backend,monkeypatch):
    b=backend;seed_reading(b)
    b._flow_write(dict(status='active',policy='even-floor-v1',slots=[[['note.md','1']]],card_ids=[]))
    events=[];fsync=os.fsync
    def sync(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode) and os.fstat(fd).st_ino==b.STATE_DIR.stat().st_ino:events.append('state_directory')
        return fsync(fd)
    monkeypatch.setattr(os,'fsync',sync)
    original=b._reading_conn
    def connection():
        c=original();c.set_trace_callback(lambda sql:events.append('FULL') if sql=='PRAGMA synchronous=FULL' else events.append('COMMIT') if sql=='COMMIT' else None);return c
    monkeypatch.setattr(b,'_reading_conn',connection)
    def fault(phase):
        events.append(phase)
        if phase=='flow_before':
            assert events.index('FULL') < events.index('COMMIT') < events.index('db')
            assert 'state_directory' in events[events.index('db')+1:],events
    relocator=b._files_relocator();relocator.fault=fault
    result=relocator.move('note.md','moved.md',False)
    journal=json.loads((relocator.directory/(result['operationId']+'.json')).read_text())
    assert journal['phase']=='complete' and not list((relocator.directory/'pending').iterdir())
    assert b._reading_read()['list'][0]['path']=='moved.md'
    assert ['moved.md','1'] in b._flow_read()['slots'][0]
    assert events.index('flow') > events.index('flow_before')
    print('durability order',events)

def test_upload_rewrite_budget(backend,monkeypatch):
    b=backend;monkeypatch.setattr(b,'UPLOAD_MAX_BYTES',40);monkeypatch.setattr(b,'UPLOAD_BATCH_MAX_BYTES',80)
    (b.NOTES_DIR/'x.png').write_bytes(b'existing')
    before={str(p):p.read_bytes() for p in b.NOTES_DIR.rglob('*') if p.is_file()}
    async def run():
        async with client(b) as c:
            folder='very-long-directory-name-for-prefix'
            r=await upload(c,[(folder+'/x.png',b'img'),(folder+'/n.md',b'![](x.png) ![](x.png)')])
            assert r.status_code==413,r.text
            assert {str(p):p.read_bytes() for p in b.NOTES_DIR.rglob('*') if p.is_file()}==before
    run_alive(run())

@pytest.mark.parametrize('name,value',[('ANKI_UPLOAD_MAX_BYTES','0'),('ANKI_UPLOAD_BATCH_MAX_BYTES','-1'),('ANKI_UPLOAD_MAX_FILES','wrong')])
def test_upload_config_validation(backend,name,value,monkeypatch):
    monkeypatch.setenv(name,value)
    with pytest.raises(RuntimeError,match=name):backend._positive_upload_limit(name,1)

def test_security_ports_media_write(backend,monkeypatch):
    b=backend;(b.MEDIA_DIR/'guard.png').parent.mkdir(parents=True,exist_ok=True);(b.MEDIA_DIR/'guard.png').write_bytes(b'fixture')
    (b.NOTES_DIR/'guard.md').write_bytes(b'keep')
    async def run():
        async with client(b,'localhost:80') as c:
            # Browser Origin omits the default HTTP port.
            assert (await c.get('/api/files/list',headers={'Origin':'http://localhost'})).status_code==200
            assert (await c.get('/api/files/list',headers={'Origin':'http://localhost:81'})).status_code==403
            assert (await c.get('/media/guard.png',headers={'Origin':'https://evil.example'})).status_code==403
            assert (await c.get('/media/guard.png')).content==b'fixture'
            r=await c.post('/api/files/delete',json={'path':'guard.md','confirm':'guard.md'},headers={'Origin':'https://evil.example'})
            assert r.status_code==403 and (b.NOTES_DIR/'guard.md').read_bytes()==b'keep'
            r=await c.options('/api/files/upload',headers={'Origin':'http://localhost','Access-Control-Request-Method':'POST'})
            assert r.status_code==200 and 'access-control-allow-origin' not in r.headers
        monkeypatch.delenv('ANKI_TRUSTED_HOSTS')
        async with client(b,'test') as c:assert (await c.get('/api/files/list')).status_code==403
        async with client(b,'127.0.0.2') as c:assert (await c.get('/api/files/list')).status_code==200
    run_alive(run())

def test_upload_markdown_forms(backend):
    b=backend;(b.NOTES_DIR/'x(1).png').write_bytes(b'old')
    source='![inline](x(1).png)\n![reference][pic]\n[pic]: x(1).png "title"\n[ordinary]: x(1).png\n`![code](x(1).png)`\n```md\n![fence](x(1).png)\n```\n<img src="x(1).png">'
    async def run():
        async with client(b) as c:
            r=await upload(c,[('a/x(1).png',b'new'),('a/n.md',source.encode())]);assert r.status_code==200,r.text
            text=(b.NOTES_DIR/'a/n.md').read_text()
            assert '![inline](a-x%281%29.png)' in text
            assert '[pic]: a-x%281%29.png "title"' in text
            assert '[ordinary]: x(1).png' in text
            assert '`![code](x(1).png)`' in text and '![fence](x(1).png)' in text
            assert '<img src="a-x%281%29.png">' in text
    run_alive(run())

def test_upload_nested_destinations(backend):
    b=backend;(b.NOTES_DIR/'x((1)).png').write_bytes(b'old')
    async def run():
        async with client(b) as c:
            text='![`alt`](x((1)).png) ![escaped](x\\(\\(1\\)\\).png)\n[pic]: x((1)).png `unrelated code`\n![image][pic]'
            r=await upload(c,[('a/x((1)).png',b'new'),('a/n.md',text.encode())]);assert r.status_code==200,r.text
            result=(b.NOTES_DIR/'a/n.md').read_text()
            assert '![`alt`](a-x%28%281%29%29.png)' in result
            assert '![escaped](a-x%28%281%29%29.png)' in result
            assert '`unrelated code`' in result
    run_alive(run())

def test_upload_allocated_filename_preflight(backend):
    b=backend;name='x'*130+'.png';folder='f'*130
    (b.NOTES_DIR/name).write_bytes(b'old image');(b.NOTES_DIR/'old.md').write_bytes(b'old note')
    async def run():
        async with client(b) as c:
            r=await upload(c,[('old.md',b'replacement'),(folder+'/'+name,b'new image')],'overwrite')
            assert r.status_code==400,r.text
            assert (b.NOTES_DIR/'old.md').read_bytes()==b'old note'
            assert not (b.NOTES_DIR/folder).exists()
    run_alive(run())

def test_upload_valid_long_filename(backend):
    b=backend;name='x'*251+'.md'
    async def run():
        async with client(b) as c:
            r=await upload(c,[(name,b'valid')]);assert r.status_code==200,r.text
            assert (b.NOTES_DIR/name).read_bytes()==b'valid'
    run_alive(run())

def rendered_image_sources(sources):
    import subprocess
    result=subprocess.run(['node','--loader','./tests/loader.mjs','tests/image-urls.test.mjs','--render'],cwd=Path(__file__).resolve().parents[1]/'frontend',input=json.dumps(sources),text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)

def test_uploaded_images_rendered_http(backend):
    from urllib.parse import quote
    b=backend
    names=['x (1).png','中文 图.png','percent%.png','delimiters?#.png','ordinary.png']
    sources=[];items=[]
    for i,name in enumerate(names):
        (b.NOTES_DIR/name).write_bytes(b'old')
        items.append(('a/'+name,('image-'+str(i)).encode()))
        sources.append('![image]('+quote(name,safe='')+'?size=2&v=%25#part)')
    items.append(('a/n.md','\n'.join(sources).encode()))
    async def run():
        async with client(b) as c:
            r=await upload(c,items);assert r.status_code==200,r.text
            text=(b.NOTES_DIR/'a/n.md').read_text()
            urls=rendered_image_sources([text])[0]
            assert len(urls)==len(names),urls
            for i,url in enumerate(urls):
                response=await c.get(url)
                assert response.status_code==200,(url,response.text)
                assert response.content==('image-'+str(i)).encode()
                assert url.endswith('?size=2&v=%25#part'),url
    run_alive(run())

@pytest.mark.parametrize('code',[
    '`` before ``` ![](x.png) after ``',
    '`` before ``` and ` ![](x.png) after ``',
    '`` first line\nmiddle ``` ![](x.png) <img src="x.png">\nlast line ``',
    '    ![](x.png)\n    <img src="x.png">',
    '\t![](x.png)\n\t<img src="x.png">',
    '```md\n![](x.png)\n```not-a-closing-fence\n<img src="x.png">\n````',
    '~~~md\n![](x.png) <img src="x.png">\n~~~~',
])
def test_upload_code_regions_literal(backend,code):
    b=backend;(b.NOTES_DIR/'x.png').write_bytes(b'old')
    source='# Source\n\n'+code+'\n\n![real](x.png)\n'
    async def run():
        async with client(b) as c:
            r=await upload(c,[('a/x.png',b'new'),('a/n.md',source.encode())]);assert r.status_code==200,r.text
            result=(b.NOTES_DIR/'a/n.md').read_bytes()
            assert code.encode() in result,result.decode()
            assert result==('# Source\n\n'+code+'\n\n![real](a-x.png)\n').encode()
            assert (await c.get('/api/reading/media/a-x.png')).content==b'new'
    run_alive(run())

def test_upload_indentation_does_not_hide_paragraph_image(backend):
    b=backend;(b.NOTES_DIR/'x.png').write_bytes(b'old')
    source='Paragraph continuation\n    ![real](x.png)\n'
    async def run():
        async with client(b) as c:
            r=await upload(c,[('a/x.png',b'new'),('a/n.md',source.encode())]);assert r.status_code==200,r.text
            assert (b.NOTES_DIR/'a/n.md').read_text()=='Paragraph continuation\n    ![real](a-x.png)\n'
    run_alive(run())

@pytest.mark.parametrize('code', [
    '>     ![](x.png)',
    '> >     ![](x.png)\n> >     <img src="x.png">',
    '> ```md\n> ![](x.png)\n> <img src="x.png">\n> ```',
    '- item\n\n      ![](x.png)\n      <img src="x.png">',
    '> - item\n>\n>       ![](x.png)',
])
def test_upload_container_code_regions_literal(backend, code):
    b = backend
    (b.NOTES_DIR / 'x.png').write_bytes(b'old')
    real = '> ![quoted](x.png)\n\n> > ![nested](x.png)\n\n- ![listed](x.png)\n\n![after](x.png)\n'
    source = code + '\n\n' + real
    async def run():
        async with client(b) as c:
            response = await upload(c, [('a/x.png', b'new'), ('a/n.md', source.encode())])
            assert response.status_code == 200, response.text
            result = (b.NOTES_DIR / 'a/n.md').read_bytes()
            assert result == (code + '\n\n' + real.replace('(x.png)', '(a-x.png)')).encode(), result.decode()
            image = await c.get('/api/reading/media/a-x.png')
            assert image.status_code == 200 and image.content == b'new'
    run_alive(run())
