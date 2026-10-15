"""Actual isolated ASGI undo/restart and pending-index regressions; no socket."""
import os,sys,asyncio,json,uuid,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
if '--recover' in sys.argv:
 import app
 app._files_relocator().recover()
 print(json.dumps(app._files_relocator().request_result(sys.argv[-1])))
 raise SystemExit
for k in list(os.environ):
 if k.startswith('ANKI_'): os.environ.pop(k)
from native_fixture import configure,cleanup
configure()
import app,httpx
from unittest.mock import patch
class Lost(httpx.AsyncBaseTransport):
 async def handle_async_request(self,request):
  response=await httpx.ASGITransport(app=app.app).handle_async_request(request)
  assert response.status_code==200,response.status_code
  await response.aread();await response.aclose()
  raise httpx.ReadError('lost actual response',request=request)
async def main():
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://synthetic.fixture') as c:
  for phase in ['lost','fs','db','flow_before']:
   name='undo-'+phase+'.md';(app.NOTES_DIR/name).write_text('# Synthetic\nbody')
   conn=app._reading_conn()
   with conn:
    conn.execute('INSERT INTO rfiles(path,in_list,file_sha) VALUES(?,1,?)',(name,'same-sha'))
    conn.execute("INSERT INTO segments(path,seg_id,start_line,end_line,fingerprint,status) VALUES(?,1,1,2,'same','done')",(name,))
   conn.close()
   app._flow_write(dict(status='active',policy='even-floor-v1',slots=[[[name,'1']]],card_ids=[]))
   move=await c.post('/api/files/move',json={'path':name,'new_dir':'dest'})
   assert move.status_code==200,move.text
   original=move.json()['operationId'];rid=uuid.uuid4().hex;body={'operationId':original,'requestId':rid}
   if phase=='lost':
    try:
     async with httpx.AsyncClient(transport=Lost(),base_url='http://synthetic.fixture') as lost: await lost.post('/api/files/undo',json=body)
    except httpx.ReadError: pass
   else:
    relocator=app._files_relocator();relocator.fault=lambda p: (_ for _ in ()).throw(OSError('injected')) if p==phase else None
    with patch.object(app,'_files_relocator',return_value=relocator):
     r=await c.post('/api/files/undo',json=body);assert r.status_code==503,r.text
    restart=subprocess.run([sys.executable,__file__,'--recover',rid],capture_output=True,text=True,env=os.environ)
    assert restart.returncode==0,restart.stderr
   result=await c.get('/api/files/operation',params={'request_id':rid})
   assert result.status_code==200,result.text
   assert result.json()['old_path']=='dest/'+name and result.json()['path']==name and result.json()['operationId']==rid,result.text
   assert (await c.post('/api/files/undo',json=body)).status_code==200
   # A new guarded repeat request also needs its own durable query receipt;
   # losing its response must not become not_found after the inverse completed.
   repeat=uuid.uuid4().hex
   try:
    async with httpx.AsyncClient(transport=Lost(),base_url='http://synthetic.fixture') as lost:
     await lost.post('/api/files/undo',json={'operationId':original,'requestId':repeat})
   except httpx.ReadError:pass
   receipt=await c.get('/api/files/operation',params={'request_id':repeat})
   assert receipt.json().get('status')=='complete' and receipt.json().get('path')==name,receipt.text
   conn=app._reading_conn()
   assert conn.execute('SELECT file_sha FROM rfiles WHERE path=?',(name,)).fetchone()[0]=='same-sha'
   assert conn.execute('SELECT status FROM segments WHERE path=?',(name,)).fetchone()[0]=='done'
   conn.close()
   assert app._flow_read()['slots'][0][0][0]==name
   (app.NOTES_DIR/name).write_text('edited')
   assert (await c.post('/api/files/undo',json=body)).status_code==409
   assert (await c.get('/api/files/operation',params={'request_id':repeat})).status_code==409
   print('PASS actual undo response/restart/current guard',phase)
  # An undelivered opaque intent does not grant permission to move an upload
  # replacement: status lookup is read-only and the client must renew action.
  source='replacement.md';(app.NOTES_DIR/source).write_text('original')
  pending=uuid.uuid4().hex
  assert (await c.get('/api/files/operation',params={'request_id':pending})).json()['status']=='not_found'
  upload=await c.post('/api/files/upload',data={'rel_paths':source,'dir':'','on_conflict':'overwrite'},files={'files':(source,b'replacement','text/markdown')})
  assert upload.status_code==200,upload.text
  assert (await c.get('/api/files/operation',params={'request_id':pending})).json()['status']=='not_found'
  assert (app.NOTES_DIR/source).read_text()=='replacement' and not (app.NOTES_DIR/'dest'/source).exists()
  print('PASS actual upload overwrite + unknown intent queries preserve replacement in root')
  # No completed records are parsed by ordinary recovery or middleware.
  relocator=app._files_relocator();relocator.recover()
  for n in range(250):
   (relocator.directory/(f'{n:032x}'+'.json')).write_text(json.dumps({'phase':'complete','fingerprint':['large']*100}))
  reads=[];read=Path.read_text
  def tracked(p,*a,**kw):
   if p.parent==relocator.directory and p.suffix=='.json':reads.append(str(p))
   return read(p,*a,**kw)
  with patch.object(Path,'read_text',tracked):
   relocator.recover()
   r=await c.get('/api/files/membership');assert r.status_code==200,r.text
  assert reads==[],reads
  print('PASS recovery/membership parse zero of 250 completed journals')
async def runner():
 async def heartbeat():
  while True: await asyncio.sleep(.05)
 h=asyncio.create_task(heartbeat())
 try: await main()
 finally: h.cancel()
try:asyncio.run(runner())
finally:cleanup()
