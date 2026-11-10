"""Actual ASGI/owner recovery without browser keys, never fake Anki effects."""
import asyncio,os,sys,importlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,run_closed
from lecture_real_documents_test import make_pdf

def test_server_operation_resume(tmp_path,monkeypatch):
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':monkeypatch.delenv(k)
 monkeypatch.setenv('ANKI_READING_MODE','1')
 configure();sys.modules.pop('app',None);b=importlib.import_module('app')
 async def run():
  import httpx
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
   async def post(path,**kw):
    r=await c.post(path,**kw);assert r.status_code==200,r.text;return r.json()
   rev=await post('/api/lectures/import',data={'path':'resume.pdf'},files={'file':('resume.pdf',make_pdf(),'application/pdf')})
   await post(f'/api/lectures/{rev["document_id"]}/revisions/{rev["revision_id"]}/read')
   seg=next(x for x in b._lectures().reading.all('page-segments') if x['document_id']==rev['document_id'])
   src=dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],segment_id=seg['segment_id'],page=3)
   draft=await post('/api/lectures/authoring',json={'source':src});aid=draft['authoring_id']
   draft.update(masks=[dict(mask_id='a'*32,rect=[.1,.1,.2,.2]),dict(mask_id='b'*32,rect=[.5,.5,.2,.2])])
   saved=await c.put('/api/lectures/authoring/'+aid,json={'value':draft,'expected_version':1});assert saved.status_code==200,saved.text
   service=b._lectures().authoring;original=service.publish
   def fault(phase):
    if phase=='note':raise RuntimeError('connection lost after actual native side effect')
   async def fail(*args,**kw):return await original(*args,**kw,fault=fault)
   service.publish=fail
   partial=await post('/api/lectures/authoring/'+aid+'/publish',json={'version':2,'request_id':'old-browser-key','confirmed_deletions':[]})
   assert partial['status']=='partial'
   assert len(await b.anki('findCards',{'query':'"note:ir4anki Image QA"'}))==1
   # Restart all lecture stores. The only remaining recovery identity is the
   # authoritative pending operation returned by GET; no local request key.
   b._lectures.cache_clear()
   server=(await c.get('/api/lectures/authoring/'+aid)).json();pending=server['pending_operation']
   assert pending['operation_id']==partial['operation_id']
   other=await post('/api/lectures/authoring',json={'source':src})
   wrong=await c.post('/api/lectures/authoring/'+other['authoring_id']+'/resume/'+pending['operation_id']);assert wrong.status_code==409
   reused=await c.post('/api/lectures/authoring/'+aid+'/publish',json={'version':1,'request_id':'old-browser-key','confirmed_deletions':[]});assert reused.status_code==409
   done=await post('/api/lectures/authoring/'+aid+'/resume/'+pending['operation_id']);assert done['status']=='complete'
   again=await post('/api/lectures/authoring/'+aid+'/resume/'+pending['operation_id']);assert again==done
   assert len(await b.anki('findCards',{'query':'"note:ir4anki Image QA"'}))==2
   for item in done['items']:
    source=(await c.get('/api/reading/source',params={'note_id':item['result']['note_id']})).json()
    assert source['page']==3 and source['page_start']==1
   await post('/api/files/delete',json={'path':'resume.pdf','confirm':'resume.pdf'})
   listing=(await c.get('/api/lectures')).json();assert not any(x['document_id']==rev['document_id'] for x in listing['documents'])
   # Deleted original does not erase immutable citation/raster recovery.
   source=(await c.get('/api/reading/source',params={'note_id':done['items'][0]['result']['note_id']})).json();assert source['page']==3
 asyncio.run(run_closed(b,run()))

def test_rejected_publication_has_durable_non_creation_receipt(tmp_path,monkeypatch):
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':monkeypatch.delenv(k)
 monkeypatch.setenv('ANKI_READING_MODE','1');configure();sys.modules.pop('app',None);b=importlib.import_module('app')
 async def run():
  import httpx
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
   async def post(path,**kw):
    r=await c.post(path,**kw);assert r.status_code==200,r.text;return r.json()
   rev=await post('/api/lectures/import',data={'path':'two-clients.pdf'},files={'file':('two-clients.pdf',make_pdf(),'application/pdf')})
   await post(f'/api/lectures/{rev["document_id"]}/revisions/{rev["revision_id"]}/read')
   seg=next(x for x in b._lectures().reading.all('page-segments') if x['document_id']==rev['document_id'])
   d=await post('/api/lectures/authoring',json={'source':dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],segment_id=seg['segment_id'],page=2)})
   aid=d['authoring_id'];endpoint='/api/lectures/authoring/'+aid
   d['masks']=[dict(mask_id='f'*32,rect=[.1,.1,.2,.2])]
   response=await c.put(endpoint,json={'value':d,'expected_version':1});assert response.status_code==200;local=response.json()
   first=await post(endpoint+'/publish',json={'version':2,'request_id':'initial-published','confirmed_deletions':[]});assert first['status']=='complete'
   ids=await b.anki('findCards',{'query':'"note:ir4anki Image QA"'});assert len(ids)==1
   from native_fixture import NativeData
   owner=NativeData(b);before=owner.native(lambda col:col.db.all('select * from cards'))
   remote=dict(local,question='second client newer server text')
   response=await c.put(endpoint,json={'value':remote,'expected_version':2});assert response.status_code==200
   rejected={'version':2,'request_id':'stale-clean-client','confirmed_deletions':[]}
   response=await c.post(endpoint+'/publish',json=rejected);assert response.status_code==409
   proof=await post(endpoint+'/publication-status',json=rejected)
   assert proof['state']=='rejected' and proof['draft']['version']==3 and proof['draft']['pending_operation'] is None
   assert proof['draft']['question']=='second client newer server text'
   assert not any(op['operation_id']==proof['rejection']['operation_id'] for op in b._lectures().authoring.all('authoring-operations'))
   b._lectures.cache_clear() # durable receipt, not a volatile HTTP error string
   assert (await post(endpoint+'/publication-status',json=rejected))['state']=='rejected'
   unknown=dict(rejected,request_id='network-outcome-never-seen')
   assert (await post(endpoint+'/publication-status',json=unknown))['state']=='unknown'
   # Rejected identity remains reserved; a new version cannot reuse it.
   response=await c.post(endpoint+'/publish',json=dict(rejected,version=3));assert response.status_code==409
   response=await c.put(endpoint,json={'value':dict(local,question='explicitly merged local text'),'expected_version':3});assert response.status_code==200
   fresh={'version':4,'request_id':'after-explicit-merge','confirmed_deletions':[]}
   result=await post(endpoint+'/publish',json=fresh);assert result['status']=='complete'
   assert (await post(endpoint+'/publication-status',json=fresh))['state']=='operation'
   assert await b.anki('findCards',{'query':'"note:ir4anki Image QA"'})==ids
   assert owner.native(lambda col:col.db.all('select * from cards'))==before,'schedule/card identity changed'
   assert result['items'][0]['result']['note_id']==first['items'][0]['result']['note_id']
 asyncio.run(run_closed(b,run()))
