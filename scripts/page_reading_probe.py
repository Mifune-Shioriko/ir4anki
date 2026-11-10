"""Actual mounted App + ASGI/native fixture. No routed successful API fakes.
Usage: python scripts/lecture_test_driver.py scripts/page_reading_probe.py
  --dist /absolute/candidate-dist --output /absolute/evidence [--seed-only|--api-only]
All environment/data roots are scrubbed and disposable before importing app.
"""
import argparse,asyncio,importlib,json,os,re,subprocess,sys,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from native_fixture import configure,cleanup,reserve_loopback_port,verify_server

def pdf_bytes():
 # Recognizable nonblank 20-page vector PDF; no real corpus or external fonts.
 objects=[]
 def add(s):objects.append(s.encode());return len(objects)
 catalog=add('');pages=add('');font=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');kids=[]
 for n in range(1,21):
  text=f'q 1 1 1 rg 0 0 640 480 re f .2 .4 .7 rg 30 200 580 200 re f BT /F1 32 Tf 1 1 1 rg 60 300 Td (SYNTHETIC PAGE {n:02d}) Tj ET Q'
  stream=add(f'<< /Length {len(text)} >>\nstream\n{text}\nendstream')
  kids.append(add(f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 640 480] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {stream} 0 R >>'))
 objects[catalog-1]=f'<< /Type /Catalog /Pages {pages} 0 R >>'.encode();objects[pages-1]=f'<< /Type /Pages /Count 20 /Kids [{" ".join(f"{k} 0 R" for k in kids)}] >>'.encode()
 data=bytearray(b'%PDF-1.7\n');offsets=[0]
 for i,body in enumerate(objects,1):offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+body+b'\nendobj\n'
 x=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
 for off in offsets[1:]:data+=f'{off:010d} 00000 n \n'.encode()
 data+=f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode();return bytes(data)

def prepare(dist):
 for k in list(os.environ):
  if k.startswith('ANKI_') or k in ('ANKICONNECT_URL','REVIEW_DIST_DIR'):os.environ.pop(k)
 assert os.environ.get('NATIVE_FIXTURE_ROOT'),'explicit scratch root required'
 configure()
 state=Path(os.environ['ANKI_STATE_DIR'])
 os.environ.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='0',ANKI_SYNC_POLICY='manual',ANKI_READING_DB=str(state/'reading.db'),ANKI_EXPORT_DIR=str(state/'exports'),REVIEW_DIST_DIR=str(dist))
 b=importlib.import_module('app')
 for p in (b.STATE_DIR,b.NOTES_DIR,b.READING_DB_FILE,b.EXPORT_DIR,b.BACKUP_DIR,b._pylib.path if hasattr(b._pylib,'path') else Path(os.environ['ANKI_COLLECTION_PATH'])):
  assert Path(p).resolve().is_relative_to(Path(os.environ['ANKI_COLLECTION_PATH']).parent),p
 return b

async def populate(b):
 import httpx
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
  async def call(method,path,**kw):
   res=await c.request(method,path,**kw);assert res.status_code==200,(path,res.status_code,res.text);return res.json()
  rev=await call('POST','/api/lectures/import',data={'path':'synthetic.pdf'},files={'file':('synthetic.pdf',pdf_bytes(),'application/pdf')})
  d,r=rev['document_id'],rev['revision_id'];seg=await call('POST',f'/api/lectures/{d}/revisions/{r}/read')
  notes={};drafts={};annotations={}
  for page in (3,4):
   source=dict(kind='pdf',document_id=d,revision_id=r,segment_id=seg['segment_id'],page=page)
   annotation=await call('GET',f'/api/lectures/{d}/revisions/{r}/pages/{page}/annotation')
   annotation=await call('PUT',f'/api/lectures/{d}/revisions/{r}/pages/{page}/annotation',json=dict(body=f'LOCAL ANNOTATION PAGE {page}',expected_version=annotation['version'],request_id=f'annotation-{page}'));annotations[str(page)]=annotation
   draft=await call('POST','/api/lectures/authoring',json={'source':source})
   draft=await call('PUT','/api/lectures/authoring/'+draft['authoring_id'],json={'value':dict(draft,masks=[dict(mask_id=str(page)*32,rect=[.1,.25,.25,.25])],question=f'PEER_PAGE_{page}'),'expected_version':1})
   op=await call('POST','/api/lectures/authoring/'+draft['authoring_id']+'/publish',json={'version':draft['version'],'request_id':f'seed-{page}','confirmed_deletions':[]});assert op['status']=='complete',op
   notes[str(page)]=op['items'][0]['result'];drafts[str(page)]=draft
  # Page annotation QA provenance is exact and survives a split alongside IO.
  qa=await call('POST','/api/card/add',json={'fields':{'正面':'QA_PAGE_3','背面':'QA answer'},'reading_source':dict(drafts['3']['source'],annotation_id=annotations['3']['annotation_id'])})
  cloze=await call('POST','/api/card/add',json={'kind':'cloze','fields':{'文字':'PAGE_3 {{c1::annotation}}','背面额外':''},'reading_source':dict(drafts['3']['source'],annotation_id=annotations['3']['annotation_id'])})
  state=await call('POST','/api/flow/start');assert state['flow']['phase']=='reading',state
  assert len(state['reading_round']['chunks'])==1
  for page in (3,4):
   peer=await call('GET',f'/api/lectures/{d}/revisions/{r}/pages/{page}/related')
   assert notes[str(page)]['note_id'] in peer['note_ids'] and notes[str(7-page)]['note_id'] not in peer['note_ids']
  return dict(schema_version=1,document_id=d,revision_id=r,root=seg,notes=notes,drafts=drafts,annotations=annotations,qa_note_id=qa['noteId'],cloze_note_id=cloze['noteId'],reading_slots=state['flow']['reading_slots'],native_card_count=len(await b.anki('findCards',{'query':''})))

async def api_checks(b,seed):
 import httpx
 from native_fixture import NativeData
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
  async def call(method,path,**kw):
   res=await c.request(method,path,**kw);assert res.status_code==200,(path,res.status_code,res.text);return res.json()
  async def split(s,a,z,key):return await call('POST','/api/lectures/split',json=dict(segment_id=s['segment_id'],revision_id=s['revision_id'],page_start=a,page_end=z,expected_version=s['version'],request_id=key))
  native=NativeData(b);before=native.native(lambda col:col.db.all('select * from cards'))
  first=await split(seed['root'],3,8,'api-first');second=await split(first['chunks'][0],3,3,'api-second')
  child=second['chunks'][0];d,r=seed['document_id'],seed['revision_id'];a=seed['drafts']['3'];endpoint='/api/lectures/authoring/'+a['authoring_id']
  opened=await call('GET',endpoint)
  assert opened['source']==a['source'] and opened['effective_source']['segment_id']==child['segment_id']
  assert opened['crop']==a['crop'] and opened['masks']==a['masks']
  saved=await call('PUT',endpoint,json={'value':dict(opened,question='PEER_PAGE_3 EDITED'),'expected_version':opened['version']})
  op=await call('POST',endpoint+'/publish',json={'version':saved['version'],'request_id':'republish-after-split','confirmed_deletions':[]})
  assert op['status']=='complete' and op['items'][0]['result']['note_id']==seed['notes']['3']['note_id']
  assert native.native(lambda col:col.db.all('select * from cards'))==before,'native card IDs/schedule changed'
  for p in (3,4):
   link=await call('GET','/api/reading/source',params={'note_id':seed['notes'][str(p)]['note_id']})
   assert link['segment_id']==seed['root']['segment_id'] and link['page']==p
  qa=await call('GET','/api/reading/source',params={'note_id':seed['qa_note_id']});assert qa['annotation_id']==seed['annotations']['3']['annotation_id']
  cloze=await call('GET','/api/reading/source',params={'note_id':seed['cloze_note_id']});assert cloze['page']==3 and cloze['annotation_id']==seed['annotations']['3']['annotation_id']
  # Actual native preview gate, using the unchanged gate implementation.
  b.PREVIEW_MODE=True
  data=b._reading_read();gated=await b._reading_gates(data)
  assert (child['path'],child['segment_id']) not in gated
  cid=seed['notes']['3']['card_ids'][0]
  native.native(lambda col:col.sched.suspend_cards([cid]))
  gated=await b._reading_gates(b._reading_read())
  assert (child['path'],child['segment_id']) in gated
  for e in b._lectures().entries():
   for s in e['segments']:
    if s['status']!='container' and s['page_start']<=4<=s['page_end']:assert (s['path'],s['segment_id']) not in gated
  b.PREVIEW_MODE=False
  state=await call('GET','/api/flow/state');assert len(state['reading_round']['chunks'])==1 and state['reading_round']['chunks'][0]['segment_id']==child['segment_id']
  assert state['flow']['reading_slots']==seed['reading_slots']
  # A partial native publication crosses a further split without rewriting
  # its snapshot, operation ID or original authoring source.
  fourth=seed['drafts']['4'];fourth_endpoint='/api/lectures/authoring/'+fourth['authoring_id']
  fourth=await call('PUT',fourth_endpoint,json={'value':dict(fourth,masks=fourth['masks']+[dict(mask_id='f'*32,rect=[.5,.5,.2,.2])]),'expected_version':fourth['version']})
  service=b._lectures().authoring;original=service.publish
  def fault(phase):
   if phase=='note':raise RuntimeError('synthetic disconnect after real native note')
  async def interrupted(*args,**kwargs):return await original(*args,**kwargs,fault=fault)
  service.publish=interrupted
  partial=await call('POST',fourth_endpoint+'/publish',json={'version':fourth['version'],'request_id':'partial-cross-split','confirmed_deletions':[]})
  assert partial['status']=='partial'
  service.publish=original
  leaf4=b._lectures().effective_source(fourth['source'])
  current=b._lectures().reading.segment(leaf4['segment_id'])
  await split(current,4,4,'split-during-partial')
  resumed=await call('POST',fourth_endpoint+'/resume/'+partial['operation_id'])
  assert resumed['status']=='complete' and resumed['operation_id']==partial['operation_id']
  assert resumed['snapshot']['source']==seed['drafts']['4']['source']
  assert resumed['items'][0]['result']['note_id']==seed['notes']['4']['note_id']
  # Wrong-page record must fail explicitly, leaving historical locator untouched.
  try:b._lectures().record(seed['notes']['3']['note_id'],seed['drafts']['4']['source'])
  except Exception as exc:assert '改挂' in str(exc)
  else:raise AssertionError('wrong-page reattachment accepted')
  # Same page number in another document/new revision must stay disjoint.
  other=await call('POST','/api/lectures/import',data={'path':'distinct.pdf'},files={'file':('distinct.pdf',pdf_bytes(),'application/pdf')})
  other_root=await call('POST',f"/api/lectures/{other['document_id']}/revisions/{other['revision_id']}/read")
  other_note=await call('POST','/api/card/add',json={'fields':{'正面':'OTHER_DOCUMENT_PAGE_3','背面':'other'},'reading_source':dict(kind='pdf',document_id=other['document_id'],revision_id=other['revision_id'],segment_id=other_root['segment_id'],page=3)})
  peer=await call('GET',f"/api/lectures/{other['document_id']}/revisions/{other['revision_id']}/pages/3/related")
  assert peer['note_ids']==[other_note['noteId']]
  revised=await call('POST','/api/lectures/import',data={'path':'synthetic.pdf','document_id':d},files={'file':('synthetic.pdf',pdf_bytes().replace(b'SYNTHETIC',b'REVISED  '),'application/pdf')})
  assert revised['revision_id']!=r
  peer=await call('GET',f"/api/lectures/{d}/revisions/{revised['revision_id']}/pages/3/related");assert peer['note_ids']==[]
  assert b._lectures().docs.document(d)['reading_revision']==r
  return dict(recursive_split=True,native_gate=True,republish_same_native_identity_and_schedule=True,immutable_origin=True,qa_annotation_identity=True,deferred_tail_and_slot=True,partial_publication_across_split=True,distinct_documents_and_revised_path_disjoint=True)

def browser_checks(base,seed,out):
 from playwright.sync_api import sync_playwright
 checks=[];errors=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000})
  page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
  context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') or route.request.url.startswith(('data:','blob:')) else route.abort())
  page.goto(base);content=page.locator('.content')
  def go(n):
   pager=content.get_by_role('spinbutton',name='页码',exact=True);pager.fill(str(n));pager.press('Enter')
   page.wait_for_function("n=>document.querySelector('.content .textLayer')?.textContent.includes('SYNTHETIC PAGE '+String(n).padStart(2,'0'))",arg=n)
   canvas=content.locator('.pdf-page canvas');assert canvas.get_attribute('aria-label')==f'第 {n} 页课件'
   assert canvas.evaluate("c=>{const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return a.some((v,i)=>i%4!==3&&v<240)}"),'blank PDF'
  def related(n):
   page.locator('.note-column md-primary-tab').filter(has_text='相关卡片').click()
   page.wait_for_function("n=>{const t=document.querySelector('.related-panel')?.textContent??'';return t.includes('PEER_PAGE_'+n)&&!t.includes('PEER_PAGE_'+(7-n))}",arg=n)
  go(3);related(3);go(4);related(4)
  wrong_key=f'ir4anki.authoring.page.{seed["document_id"]}.{seed["revision_id"]}.4'
  page.evaluate('([key,id])=>localStorage.setItem(key,id)',[wrong_key,seed['drafts']['3']['authoring_id']])
  content.get_by_role('button',name='图片挖空',exact=True).click()
  content.get_by_role('alert').filter(has_text='已保存工程属于其他文档、版本或页').wait_for()
  assert page.locator('.occlusion-editor').count()==0
  page.evaluate('key=>localStorage.removeItem(key)',wrong_key)
  go(2)
  page.wait_for_function("document.querySelectorAll('.related-panel .related-item').length===0")
  go(3);related(3);checks.append('nonblank p3; exact native p3/p4 peers; empty p2 clears')
  page.locator('.note-column md-primary-tab').filter(has_text='全文参考').click()
  reference=page.locator('.pdf-reference-scroll');reference.wait_for()
  reference.hover();page.mouse.wheel(0,100000)
  page.locator('.note-column canvas[aria-label="参考第 20 页"]').wait_for()
  assert content.get_by_role('spinbutton',name='页码',exact=True).input_value()=='3'
  content.get_by_role('button',name='图片挖空',exact=True).click()
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  assert page.locator('.occlusion-editor').get_attribute('data-page')=='3'
  page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
  checks.append('right ordinary scroll to p20 leaves left p3 and authoring p3')
  def split(a,z):
   content.get_by_role('button',name='按页书签分割',exact=True).click()
   page.get_by_label('分割开始页',exact=True).fill(str(a));page.get_by_label('分割结束页',exact=True).fill(str(z))
   with page.expect_response(lambda r:r.url==base+'/api/lectures/split' and r.request.method=='POST') as response:
    page.get_by_text('确认书签分割',exact=True).click()
   assert response.value.ok,response.value.text()
   child=response.value.json()['chunks'][0]
   page.wait_for_function("n=>document.querySelector('.content input[aria-label=页码]')?.min===String(n)",arg=a)
   return child
  selected=split(3,8);go(3);related(3)
  # Old saved project is reused from current child, retains its page and masks.
  page.evaluate('([key,id])=>localStorage.setItem(key,id)',[f'ir4anki.authoring.page.{seed["document_id"]}.{seed["revision_id"]}.3',seed['drafts']['3']['authoring_id']])
  content.get_by_role('button',name='图片挖空',exact=True).click()
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  assert page.locator('.occlusion-editor').get_attribute('data-authoring-id')==seed['drafts']['3']['authoring_id']
  assert page.locator('.occlusion-mask').count()==1
  page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
  content.get_by_text('下一张（稍后继续）',exact=True).click()
  page.locator('.flashcard-wrapper.stage-review').wait_for()
  assert page.locator('.related-panel .related-item').count()==0,'face-down leak'
  page.locator('md-filled-button,md-filled-tonal-button').filter(has_text='显示答案').click()
  page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True).click()
  content.locator('.pdf-page canvas').wait_for()
  current=int(content.get_by_role('spinbutton',name='页码',exact=True).input_value());assert current in (3,4)
  one=split(3,6);two=split(3,4);assert two['parent_segment_id']==one['segment_id']
  go(3);related(3);go(4);related(4)
  # A trace authoring ID from another page must not take precedence after paging.
  content.get_by_role('button',name=re.compile('^(编辑)?图片挖空$')).click()
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  assert page.locator('.occlusion-editor').get_attribute('data-page')=='4'
  page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
  checks.append('actual split control; next; trace recursive splits adopt fresh children; trace IO follows selected page')
  go(3)
  assert 'LOCAL ANNOTATION PAGE 3' in content.locator('.page-annotation').inner_text()
  go(4)
  assert 'LOCAL ANNOTATION PAGE 4' in content.locator('.page-annotation').inner_text()
  checks.append('annotation locality survives recursive splits')
  assert not errors,errors
  page.screenshot(path=str(out/'probe.png'));browser.close()
 return dict(checks=checks,page_errors=errors)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--dist',required=True);parser.add_argument('--output',required=True)
 mode=parser.add_mutually_exclusive_group();mode.add_argument('--seed-only',action='store_true');mode.add_argument('--api-only',action='store_true');args=parser.parse_args()
 out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False);dist=Path(args.dist).resolve();assert (dist/'index.html').is_file()
 proc=None;b=None;result=dict(schema_version=1,mode='seed' if args.seed_only else 'api' if args.api_only else 'browser',status='failed')
 try:
  b=prepare(dist)
  async def setup():
   seed=await populate(b)
   result['seed']=seed
   if args.api_only:result['api']=await api_checks(b,seed)
   # Freeze seed identities before handing the collection to the server process.
   await b._pylib.close()
   return seed
  seed=asyncio.run(setup())
  if not(args.seed_only or args.api_only):
   port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
   with (out/'server.log').open('w') as log:
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT)
    verify_server(proc,base);result['browser']=browser_checks(base,seed,out)
  result['status']='passed'
 except BaseException:
  result['traceback']=traceback.format_exc();raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=20)
  if b:
   try:asyncio.run(b._pylib.close())
   except Exception:pass
  (out/'probe-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));cleanup()
if __name__=='__main__':main()
