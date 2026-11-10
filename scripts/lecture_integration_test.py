"""Actual ASGI + disposable native collection mixed MD/PDF flow and restart."""
import asyncio,os,sys,importlib
from pathlib import Path
from native_fixture import configure,NativeData,run_closed
from lecture_real_documents_test import make_pdf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
def test_mixed_native_flow(tmp_path,monkeypatch):
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':monkeypatch.delenv(k)
 monkeypatch.setenv('ANKI_READING_MODE','1');monkeypatch.setenv('ANKI_PREVIEW_MODE','1')
 configure();sys.modules.pop('app',None);b=importlib.import_module('app')
 async def run():
  import httpx
  seed=NativeData(b)
  for i in range(4):seed.add_card(700+i,deck='2026',note=4000+i)
  corpus=Path(os.environ['ANKI_NOTES_DIR']);(corpus/'note.md').write_text('# Generated note\n\nA generated paragraph.\n')
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
   async def post(url,**kw):
    r=await c.post(url,**kw);assert r.status_code==200,r.text;return r.json()
   async def get(url):
    r=await c.get(url);assert r.status_code==200,r.text;return r.json()
   await post('/api/reading/list/add',json={'path':'note.md'})
   rev=await post('/api/lectures/import',data={'path':'lecture.pdf'},files={'file':('lecture.pdf',make_pdf(), 'application/pdf')})
   source=await post(f'/api/lectures/{rev["document_id"]}/revisions/{rev["revision_id"]}/read')
   state=await post('/api/flow/start');assert state['flow']['reading_slots']==2,state
   chunks=state['reading_round']['chunks'];assert [x.get('kind','markdown') for x in chunks]==['markdown','pdf']
   pdf=chunks[1];assert 'line_start' not in pdf and 'start_line' not in pdf
   await post('/api/reading/act',params={'path':chunks[0]['path'],'chunk_key':chunks[0]['chunk_key'],'action':'complete'})
   state=await get('/api/flow/state');assert state['flow']['phase']=='review'
   for _ in range(2):
    state=await get('/api/flow/state');cid=state['cards'][0]['cardId'];await post('/api/answer',params={'card_id':cid,'ease':3})
   state=await get('/api/flow/state');assert state['flow']['phase']=='reading'
   selected=await post('/api/lectures/split',json={'segment_id':pdf['segment_id'],'revision_id':pdf['revision_id'],'page_start':2,'page_end':3,'expected_version':1,'request_id':'generated-split'})
   state=await get('/api/flow/state');assert state['flow']['reading_consumed']==1
   current=state['reading_round']['chunks'][0];assert (current['page_start'],current['page_end'])==(2,3)
   assert len(state['reading_round']['chunks'])==1
   note=await post('/api/card/add',json={'fields':{'正面':'generated Q','背面':'generated A'},'reading_source':dict(kind='pdf',document_id=pdf['document_id'],revision_id=pdf['revision_id'],segment_id=current['segment_id'],page=2)})
   before=b._reading_read();src=await get('/api/reading/source?note_id='+str(note['noteId']));assert src['kind']=='pdf';assert b._reading_read()==before
   assert len(src['breadcrumb'])==2
   renamed=await post('/api/files/rename',json={'path':'lecture.pdf','new_name':'renamed.pdf','requestId':'ab'*16})
   state=await get('/api/flow/state');assert state['reading_round']['chunks'][0]['path']=='renamed.pdf'
   assert state['flow']['reading_consumed']==1
   src=await get('/api/reading/source?note_id='+str(note['noteId']));assert src['path']=='renamed.pdf'
   current=dict(current,path='renamed.pdf')
   # Pending replacement survives rebuilding all stores and flow from disk.
   b._lectures.cache_clear();state=await get('/api/flow/state');assert state['reading_round']['chunks'][0]['segment_id']==current['segment_id']
   await post('/api/reading/act',params={'path':current['path'],'chunk_key':current['chunk_key'],'action':'complete'})
   state=await get('/api/flow/state');assert state['flow']['phase']=='review'
   print('mixed actual flow, slots, parent citations, restart, scheduling-neutral source passed')
 asyncio.run(run_closed(b,run()))
