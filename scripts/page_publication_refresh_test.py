"""Real ASGI/native contract supporting exact trace refresh after publication."""
import asyncio,importlib,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,NativeData,run_closed
from page_reading_probe import populate

def test_exact_displayed_leaf_version_even_after_trace_excludes_origin(tmp_path,monkeypatch):
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':monkeypatch.delenv(k)
 configure();monkeypatch.setenv('ANKI_READING_MODE','1');monkeypatch.setenv('ANKI_PREVIEW_MODE','0');monkeypatch.setenv('ANKI_SYNC_POLICY','manual')
 sys.modules.pop('app',None);b=importlib.import_module('app')
 async def run():
  import httpx
  seed=await populate(b);native=NativeData(b);before=native.native(lambda col:col.db.all('select * from cards'))
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
   async def call(method,path,**kw):
    response=await c.request(method,path,**kw);assert response.status_code==200,(path,response.status_code,response.text);return response.json()
   async def split(parent,a,z,key):
    return (await call('POST','/api/lectures/split',json=dict(segment_id=parent['segment_id'],revision_id=parent['revision_id'],page_start=a,page_end=z,expected_version=parent['version'],request_id=key)))['chunks'][0]
   async def resolve(parent,page):
    return await call('GET',f"/api/lectures/{parent['document_id']}/revisions/{parent['revision_id']}/pages/{page}/reading-source",params={'segment_id':parent['segment_id']})
   project=seed['drafts']['4'];child=await split(seed['root'],3,8,'trace-initial')
   assert child['version']==1 and child['status']=='todo'
   for index in range(2):
    published=await call('POST','/api/lectures/authoring/'+project['authoring_id']+'/publish',json={'version':project['version'],'request_id':f'trace-republish-{index}','confirmed_deletions':[]})
    assert published['status']=='complete' and published['items'][0]['result']['note_id']==seed['notes']['4']['note_id']
    fresh=await resolve(child,4);assert fresh['segment_id']==child['segment_id'] and fresh['version']==2 and fresh['page']==4
    child=await split(fresh,4,6,'trace-recursive') if index==0 else child
   origin=await call('GET','/api/reading/source',params={'note_id':seed['notes']['3']['note_id']})
   assert origin['segment_id']==seed['root']['segment_id'] and origin['effective_chunk']['page_start']==3 and origin['effective_chunk']['page_end']==3
   current=await resolve(child,5);assert current['segment_id']==child['segment_id'] and current['version']==2
   assert native.native(lambda col:col.db.all('select * from cards'))==before
   wrong=await c.get(f"/api/lectures/{seed['document_id']}/revisions/{seed['revision_id']}/pages/5/reading-source",params={'segment_id':'f'*32});assert wrong.status_code==409
 asyncio.run(run_closed(b,run()))
