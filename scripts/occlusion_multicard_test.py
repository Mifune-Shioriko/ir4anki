"""Actual ASGI + disposable Anki persistence; no owner/API mocks."""
import asyncio,json,os,sys
from pathlib import Path
import page_reading_probe as fixture
from native_fixture import NativeData,cleanup
async def check(b):
 import httpx
 seed=await fixture.populate(b)
 native=NativeData(b)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
  async def call(method,path,**kw):
   res=await c.request(method,path,**kw);assert res.status_code==200,(path,res.status_code,res.text);assert 'application/json' in res.headers.get('content-type',''),('MISSING_PAGE_AUTHORING_LIST',path,res.headers.get('content-type'));return res.json()
  source=seed['drafts']['3']['source'];a=seed['drafts']['3']['authoring_id'];aid='/api/lectures/authoring/'+a
  before=await call('GET',aid);schedule=native.native(lambda col:col.db.all('select * from cards'))
  bcard=await call('POST','/api/lectures/authoring',json={'source':source});bid='/api/lectures/authoring/'+bcard['authoring_id']
  assert bcard['authoring_id']!=a and bcard['source']==source
  # Deliberately reuse A's mask ID: engineering identity must scope note keys.
  bcard=await call('PUT',bid,json={'value':dict(bcard,crop=[.05,.2,.6,.6],masks=before['masks'],question='INDEPENDENT B'),'expected_version':1})
  request=dict(version=bcard['version'],request_id='independent-B',confirmed_deletions=[])
  op=await call('POST',bid+'/publish',json=request);assert op['status']=='complete',op
  assert await call('POST',bid+'/publish',json=request)==op
  note=op['items'][0]['result'];assert note['note_id']!=seed['notes']['3']['note_id']
  assert await call('GET',aid)==before,'creating B changed A'
  assert native.native(lambda col:col.db.all('select * from cards where id!=?',note['card_ids'][0]))==schedule
  path=f"/api/lectures/{source['document_id']}/revisions/{source['revision_id']}/pages/3/authoring"
  listed=await call('GET',path)
  assert {x['authoring_id'] for x in listed['drafts']}=={a,bcard['authoring_id']},listed
  from lecture_occlusion import Authoring
  reopened=Authoring(b._lectures().authoring.root).draft(bcard['authoring_id'])
  assert reopened['crop']==bcard['crop'] and reopened['masks']==bcard['masks']
  latest=await call('GET',bid)
  latest=await call('PUT',bid,json={'value':dict(latest,answer='EDIT B ONLY'),'expected_version':latest['version']})
  edited=await call('POST',bid+'/publish',json=dict(version=latest['version'],request_id='edit-B',confirmed_deletions=[]))
  assert edited['items'][0]['result']['note_id']==note['note_id']
  assert edited['items'][0]['result']['card_ids']==note['card_ids']
  assert await call('GET',aid)==before
  assert len(await b.anki('findCards',{'query':''}))==5
  for nid in (seed['notes']['3']['note_id'],note['note_id']):
   provenance=await call('GET','/api/reading/source',params={'note_id':nid})
   for k in ['document_id','revision_id','page','segment_id']:assert provenance[k]==source[k]
  other=await call('GET',path.replace('/pages/3/','/pages/4/'));assert [x['authoring_id'] for x in other['drafts']]==[seed['drafts']['4']['authoring_id']]
  assert (await call('GET',path.replace('/pages/3/','/pages/5/')))['drafts']==[]
  legacy=await call('PUT',aid,json={'value':dict(before,question='LEGACY EDIT'),'expected_version':before['version']})
  legacyop=await call('POST',aid+'/publish',json=dict(version=legacy['version'],request_id='legacy-edit',confirmed_deletions=[]))
  assert legacyop['items'][0]['result']['card_ids']==seed['notes']['3']['card_ids']
  assert native.native(lambda col:col.db.all('select * from cards where id!=?',note['card_ids'][0]))==schedule
  annotations=await call('GET',path.replace('/authoring','/annotation'));assert annotations==seed['annotations']['3']
  for name,extra in [('distinct.pdf',{}),('synthetic.pdf',{'document_id':source['document_id']})]:
   rev=await call('POST','/api/lectures/import',data=dict(path=name,**extra),files={'file':(name,fixture.pdf_bytes().replace(b'SYNTHETIC',b'REVISED  '),'application/pdf')})
   isolated=await call('GET',f"/api/lectures/{rev['document_id']}/revisions/{rev['revision_id']}/pages/3/authoring")
   assert isolated['drafts']==[],'document/revision identity leaked'
  from anki_backend import PylibClient
  await b._pylib.close()
  b._pylib=PylibClient(Path(os.environ['ANKI_COLLECTION_PATH']))
  assert len(await b.anki('findCards',{'query':''}))==5
  persisted=await b.anki('notesInfo',{'notes':[note['note_id']]})
  assert 'EDIT B ONLY' in persisted[0]['fields']['Answer']['value']
  print(json.dumps(dict(status='passed',native_cards=5,page3_projects=2,page4_projects=1,independent_ids=True,stable_edit_ids=True,retry_idempotent=True,provenance=True,scheduling=True)))
if __name__=='__main__':
 b=fixture.prepare(Path(os.environ['MULTICARD_DIST']).resolve())
 from native_fixture import run_closed
 asyncio.run(run_closed(b,check(b)))
