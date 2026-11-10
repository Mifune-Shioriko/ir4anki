"""Disposable managed-image portability: two official owner clients + local syncserver.
Full upload/download is explicit fixture bootstrap only. Never use operational paths.
"""
import asyncio,os,re,json,hashlib,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,cleanup,reserve_loopback_port
from anki_backend import PylibClient
from lecture_documents import Documents
from lecture_occlusion import Authoring
from lecture_real_documents_test import make_pdf

def test_managed_image_second_client(tmp_path):
 scratch=Path(os.environ['NATIVE_FIXTURE_ROOT']).resolve()
 assert tmp_path.resolve().is_relative_to(scratch.parent), 'pytest basetemp must be beside isolated fixtures'
 port=reserve_loopback_port();endpoint=f'http://127.0.0.1:{port}/'
 server_env=dict(PATH=os.defpath,LANG='C.UTF-8',TMPDIR=str(tmp_path),SYNC_HOST='127.0.0.1',SYNC_PORT=str(port),SYNC_BASE=str(tmp_path/'syncserver'),SYNC_USER1='fixture:disposable-image-password',RUST_LOG='error')
 with (tmp_path/'syncserver.log').open('w') as log:
  proc=subprocess.Popen([sys.executable,'-m','anki.syncserver'],env=server_env,stdout=log,stderr=log)
  try:
   for _ in range(100):
    try:urllib.request.urlopen(endpoint,timeout=.1)
    except urllib.error.HTTPError:break
    except OSError:time.sleep(.05)
   else:raise AssertionError('disposable syncserver not ready')
   first_env={};second_env={};first=configure(first_env);second=configure(second_env)
   async def run():
    clients=[PylibClient(p,sync_endpoint=endpoint,sync_username='fixture',sync_password='disposable-image-password') for p in [first,second]]
    async def owner(client,fn):return await client._wait_owner(asyncio.get_running_loop().run_in_executor(client._executor,lambda:fn(client._open())))
    try:
     root=tmp_path/'lecture';docs=Documents(root);a=Authoring(root);rev=docs.import_bytes('generated.pdf',make_pdf())
     raster=docs.raster(rev['document_id'],rev['revision_id'],2)
     d=a.create(dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],segment_id='c'*32,page=2),raster)
     d=a.save(d['authoring_id'],dict(d,masks=[dict(mask_id='d'*32,rect=[.1,.1,.2,.2]),dict(mask_id='e'*32,rect=[.5,.5,.2,.2])]),expected=1)
     op=a.stage(d['authoring_id'],d['version'],'disposable-media-sync',[])
     async def source(*args):pass
     result=await a.publish(op['operation_id'],clients[0].call,deck='2026',suspended=False,record_source=source)
     assert result['status']=='complete',result
     ids=[item['result']['note_id'] for item in result['items']]
     assert len(set(ids))==2
     def inspect(col):
      output=[]
      for nid in ids:
       n=col.get_note(nid);cards=n.cards();model=n.note_type()
       assert len(cards)==1 and len(model['tmpls'])==1
       assert [f['name'] for f in model['flds']]==['Question','Answer','AuthoringKey']
       html=[cards[0].question(),cards[0].answer()]
       refs=set(re.findall(r'<img[^>]+src="([^"<>]+)"', ''.join(html)))
       assert len(refs)>=2 and all(not x.startswith(('http:','https:','data:')) for x in refs)
       media={name:(Path(col.media.dir())/name).read_bytes() for name in refs}
       assert all(data.startswith(b'\x89PNG\r\n\x1a\n') for data in media.values())
       output.append((nid,cards[0].id,html,media))
      return output
     before=await owner(clients[0],inspect)
     # Each collection is new and disposable, with no competing owner. This
     # explicit initial full bootstrap is never part of app startup or sync.
     def bootstrap(col,upload):
      auth=col.sync_login('fixture','disposable-image-password',endpoint)
      col.sync_collection(auth,sync_media=False)
      col.full_upload_or_download(auth=auth,server_usn=None,upload=upload)
      col.sync_media(auth)
      for _ in range(200):
       if not col.media_sync_status().active:break
       time.sleep(.05)
      else:raise AssertionError('media sync did not finish')
     await owner(clients[0],lambda col:bootstrap(col,True))
     await owner(clients[1],lambda col:bootstrap(col,False))
     after=await owner(clients[1],inspect)
     assert after==before,'second collection fields/card/model/PNG bytes differ'
     assert len(await clients[1].call('findCards',{'query':'"note:ir4anki Image QA"'}))==2
     return after,Path(second_env['ANKI_MEDIA_DIR'])
    finally:
     for client in clients:await client.close()
   records,media=asyncio.run(run())
   (tmp_path/'media-portability.json').write_text(json.dumps([dict(note_id=nid,card_id=cid,front=html[0],back=html[1],media={name:hashlib.sha256(data).hexdigest() for name,data in images.items()}) for nid,cid,html,images in records],indent=2))
   from playwright.sync_api import sync_playwright
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True);page=browser.new_page()
    for nid,cid,html,images in records:
     for side,body in zip(['front','back'],html):
      artifact=media/f'fixture-{nid}-{side}.html';artifact.write_text('<meta charset="utf-8">'+body)
      page.goto(artifact.as_uri());page.wait_for_function('document.images.length>0 && [...document.images].every(i=>i.complete&&i.naturalWidth>0)')
      assert all(page.locator('img').nth(i).evaluate('i=>i.naturalWidth>0&&i.naturalHeight>0') for i in range(page.locator('img').count()))
      page.screenshot(path=str(tmp_path/f'{nid}-{side}.png'))
    browser.close()
  finally:proc.terminate();proc.wait(timeout=15);cleanup()
