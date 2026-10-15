"""F1/F2: isolated ASGI + real relocation journal/SQLite/flow, no socket/study."""
import os, sys, asyncio, uuid, subprocess, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
if '--recover' in sys.argv:
    import app
    app._files_relocator().recover()
    print(json.dumps(app._files_relocator().request_result(sys.argv[-1])))
    raise SystemExit(0)
for key in list(os.environ):
    if key.startswith('ANKI_'): os.environ.pop(key)
from native_fixture import configure, cleanup
configure()
import app, httpx
from unittest.mock import patch

class LoseResponse(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        response=await httpx.ASGITransport(app=app.app).handle_async_request(request)
        assert response.status_code==200
        await response.aread()
        await response.aclose()
        # The real ASGI request committed; AsyncClient receives no response.
        raise httpx.ReadError('synthetic response lost at transport',request=request)

def seed(name):
    (app.NOTES_DIR/name).mkdir()
    (app.NOTES_DIR/name/'文.md').write_text('# 合成\n正文')
    conn=app._reading_conn()
    with conn:
        conn.execute('INSERT INTO rfiles(path,in_list,file_sha) VALUES(?,1,?)',(name+'/文.md','same-sha'))
        conn.execute("INSERT INTO segments(path,seg_id,start_line,end_line,fingerprint,status) VALUES(?,1,1,2,'same','done')",(name+'/文.md',))
    conn.close()
    app._flow_write(dict(status='active',policy='even-floor-v1',slots=[[[name+'/文.md','1']]],card_ids=[]))

async def main(case):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://synthetic.fixture') as c:
        if case=='F1':
            for phase in ['lost-response','fs','db','flow_before']:
                old='源-'+phase; seed(old); request=uuid.uuid4().hex
                body=dict(path=old,new_dir='目标',requestId=request)
                if phase=='lost-response':
                    try:
                        async with httpx.AsyncClient(transport=LoseResponse(),base_url='http://synthetic.fixture') as lost:
                            await lost.post('/api/files/move',json=body)
                    except httpx.ReadError: pass
                    else: raise AssertionError('lost response unexpectedly reached client')
                else:
                    relocator=app._files_relocator()
                    relocator.fault=lambda p: (_ for _ in ()).throw(OSError('injected')) if p==phase else None
                    with patch.object(app,'_files_relocator',return_value=relocator):
                        r=await c.post('/api/files/move',json=body)
                        assert r.status_code==503,r.text
                    restart=subprocess.run([sys.executable,__file__,'--recover',request],capture_output=True,text=True,env=os.environ)
                    assert restart.returncode==0,restart.stderr
                    recovered=json.loads(restart.stdout.strip().splitlines()[-1])
                    assert recovered['status']=='complete' and recovered['operationId']==request,recovered
                # Middleware recovers, explicit ID returns canonical mapping + undo.
                status=await c.get('/api/files/operation',params={'request_id':request})
                assert status.status_code==200,status.text
                result=status.json(); assert result['status']=='complete',result
                assert result['old_path']==old and result['path']=='目标/'+old
                retry=await c.post('/api/files/move',json=body)
                assert retry.status_code==200 and retry.json()['operationId']==result['operationId'],retry.text
                wrong=await c.post('/api/files/move',json={**body,'new_dir':'其他'})
                assert wrong.status_code==409,wrong.text
                conn=app._reading_conn()
                assert conn.execute('SELECT file_sha FROM rfiles WHERE path=?',(result['path']+'/文.md',)).fetchone()==('same-sha',)
                assert conn.execute('SELECT status FROM segments WHERE path=?',(result['path']+'/文.md',)).fetchone()==('done',)
                conn.close()
                undo=await c.post('/api/files/undo',json={'operationId':result['operationId']})
                assert undo.status_code==200,undo.text
                print('PASS F1 actual '+phase+' result/retry/mismatch/progress/undo',flush=True)
            unknown=await c.get('/api/files/operation',params={'request_id':uuid.uuid4().hex})
            assert unknown.json()=={'status':'not_found'}
            print('PASS F1 no unrelated history',flush=True)
            seed('重命名源'); request=uuid.uuid4().hex
            body=dict(path='重命名源',new_name='重命名目标',requestId=request)
            r=await c.post('/api/files/rename',json=body)
            assert r.status_code==200,r.text
            result=r.json()  # response lost; same path is now absent
            retry=await c.post('/api/files/rename',json=body)
            assert retry.status_code==200 and retry.json()['operationId']==result['operationId'],retry.text
            (app.NOTES_DIR/'重命名目标/文.md').write_text('changed after response loss')
            history=await c.get('/api/files/operation',params={'request_id':request})
            assert history.status_code==409 and '曾完成' in history.json()['detail'],history.text
            retry=await c.post('/api/files/rename',json=body)
            assert retry.status_code==409,retry.text
            print('PASS F1 rename replay before missing-source check; changed historical result rejects canonical remap',flush=True)
            invalid=await c.get('/api/files/operation',params={'request_id':'../outside'})
            assert invalid.status_code==409,invalid.text
            print('PASS F1 invalid ID cannot escape journal',flush=True)
        else:
            for change in ['move','edit','progress','conflict']:
                old='撤销-'+change; seed(old)
                op=app._files_relocator().move(old,'中间-'+change,True)
                undone=app._files_relocator().undo(op['operationId'])
                assert app._files_relocator().undo(op['operationId'])==undone
                if change=='move': app._files_relocator().move(old,'之后-'+change,True)
                elif change=='edit': (app.NOTES_DIR/old/'文.md').write_text('edited')
                elif change=='conflict': (app.NOTES_DIR/('中间-'+change)).mkdir()
                else:
                    conn=app._reading_conn()
                    with conn: conn.execute("UPDATE segments SET status='todo' WHERE path=?",(old+'/文.md',))
                    conn.close()
                r=await c.post('/api/files/undo',json={'operationId':op['operationId']})
                assert r.status_code==409,(change,r.text)
                print('PASS F2 unchanged retry + reject historical '+change,flush=True)

async def run():
    async def heartbeat():
        while True: await asyncio.sleep(.01)
    task=asyncio.create_task(heartbeat())
    try: await main(sys.argv[1] if len(sys.argv)>1 else 'F1')
    finally: task.cancel()

try: asyncio.run(run())
finally: cleanup()
