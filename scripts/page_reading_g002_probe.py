"""Focused real App/native and mounted production RelatedPanel acceptance.
Only disposable backend; held responses come from route.fetch(), never fakes.
"""
import argparse,asyncio,importlib,json,os,re,subprocess,sys,time,traceback
from pathlib import Path
from urllib.parse import urlencode,urlsplit
import page_reading_probe as base
from native_fixture import cleanup,reserve_loopback_port,verify_server

async def extra_seed(b,seed):
 import httpx
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
  async def call(method,path,**kw):
   res=await c.request(method,path,**kw);assert res.status_code==200,(path,res.status_code,res.text);return res.json()
  async def qa(root,page,question):
   return await call('POST','/api/card/add',json={'fields':{'正面':question,'背面':'SYNTHETIC ANSWER'},'reading_source':dict(kind='pdf',document_id=root['document_id'],revision_id=root['revision_id'],segment_id=root['segment_id'],page=page)})
  other=await call('POST','/api/lectures/import',data={'path':'other.pdf'},files={'file':('other.pdf',base.pdf_bytes(),'application/pdf')})
  other_root=await call('POST',f"/api/lectures/{other['document_id']}/revisions/{other['revision_id']}/read")
  await qa(other_root,3,'B_DOCUMENT_PAGE_3');seed['other']=dict(root=other_root)
  rev=await call('POST','/api/lectures/import',data={'path':'synthetic.pdf','document_id':seed['document_id']},files={'file':('synthetic.pdf',base.pdf_bytes().replace(b'SYNTHETIC',b'REVISED  '),'application/pdf')})
  root=b._lectures().reading.seed(rev['document_id'],rev['revision_id'],'synthetic.pdf',20)
  root=b._lectures().reading.payload(root);await qa(root,3,'NEW_REVISION_PAGE_3');seed['revised']=dict(root=root)
  large_ids=[]
  for n in range(105):large_ids.append((await qa(seed['root'],5,f'LARGE_PEER_{n:03d}'))['noteId'])
  peers=await call('GET',f"/api/lectures/{seed['document_id']}/revisions/{seed['revision_id']}/pages/5/related")
  assert set(peers['note_ids'])==set(large_ids) and len(peers['note_ids'])==105
  for ids in (large_ids[:100],large_ids[100:]):
   details=await call('GET','/api/reading/cards',params={'notes':','.join(map(str,ids))});assert len(details['cards'])==len(ids)
  seed['large_note_ids']=large_ids
  seed['native_card_count_after_extras']=len(await b.anki('findCards',{'query':''}));assert seed['native_card_count_after_extras']==111
  seed['large_peer_count']=105
  assert b._lectures().docs.document(seed['document_id'])['reading_revision']==seed['revision_id']
 corpus=Path(os.environ['ANKI_NOTES_DIR']);(corpus/'SYNTHETIC.md').write_text('# SYNTHETIC\n\nOnly disposable generated content.\n');(corpus/'SYNTHETIC destination').mkdir()
 return seed

def context(pw,baseurl):
 browser=pw.chromium.launch(headless=True);ctx=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block');page=ctx.new_page();errors=[]
 page.on('pageerror',lambda exc:errors.append(str(exc)))
 ctx.route('**/*',lambda route:route.continue_() if route.request.url.startswith(baseurl+'/') or route.request.url.startswith(('data:','blob:')) else route.abort())
 page.set_default_timeout(12000)
 return browser,ctx,page,errors

def go(page,n):
 pager=page.locator('.content').get_by_role('spinbutton',name='页码',exact=True);pager.fill(str(n));pager.press('Enter')
 page.wait_for_function("n=>document.querySelector('.content .textLayer')?.textContent.includes('SYNTHETIC PAGE '+String(n).padStart(2,'0'))",arg=n)

def reveal(page):page.locator('md-filled-button,md-filled-tonal-button').filter(has_text='显示答案').click()
def next_reading(page):
 page.locator('.content').get_by_text('下一张（稍后继续）',exact=True).click();page.locator('.flashcard-wrapper.stage-review').wait_for()

def split(page,baseurl,a,z):
 content=page.locator('.content');content.get_by_role('button',name='按页书签分割',exact=True).click()
 page.get_by_label('分割开始页',exact=True).fill(str(a));page.get_by_label('分割结束页',exact=True).fill(str(z))
 with page.expect_response(lambda r:r.url==baseurl+'/api/lectures/split' and r.request.method=='POST') as got:page.get_by_text('确认书签分割',exact=True).click()
 response=got.value;assert response.ok,('SPLIT_FAILED',response.status,response.text())
 result=response.json()['chunks'][0]
 page.wait_for_function("n=>document.querySelector('.content input[aria-label=页码]')?.min===String(n)",arg=a)
 return result

def r1_browser(pw,baseurl,seed,out):
 browser,ctx,page,errors=context(pw,baseurl)
 try:
  page.goto(baseurl);go(page,3);next_reading(page);reveal(page)
  page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True).click();page.locator('.content .pdf-page canvas').wait_for()
  child=split(page,baseurl,3,8);assert child['status']=='todo' and child['version']==1
  go(page,4)
  page.evaluate('([key,id])=>localStorage.setItem(key,id)',[f'ir4anki.authoring.page.{seed["document_id"]}.{seed["revision_id"]}.4',seed['drafts']['4']['authoring_id']])
  page.locator('.content').get_by_role('button',name=re.compile('^(编辑)?图片挖空$')).click()
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  assert page.locator('.occlusion-editor').get_attribute('data-authoring-id')==seed['drafts']['4']['authoring_id']
  # Actual republish, not an API call or reopen-only assertion.
  with page.expect_response(lambda r:r.url.endswith('/publish') and r.request.method=='POST') as published:
   page.locator('md-filled-button').filter(has_text='保存到 Anki').click()
  operation=published.value.json();assert published.value.ok and operation['status']=='complete',operation
  assert operation['items'][0]['result']['note_id']==seed['notes']['4']['note_id']
  assert operation['items'][0]['result']['card_ids']==seed['notes']['4']['card_ids']
  page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
  page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
  page.wait_for_function("document.querySelector('.content input[aria-label=页码]')?.value==='4'")
  # Wait until all real source refresh responses settle, then split the same
  # todo->active child. g001 sends expected_version 1 and receives real 409.
  page.wait_for_load_state('networkidle')
  with page.expect_request(lambda r:r.url==baseurl+'/api/lectures/split' and r.method=='POST') as request:
   second=split(page,baseurl,4,6)
  assert request.value.post_data_json['expected_version']>=2
  assert second['parent_segment_id']==child['segment_id']
  go(page,5);page.get_by_role('button',name='回到复习',exact=True).click();page.locator('.flashcard-wrapper.stage-review').wait_for()
  page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True).click();page.locator('.content .pdf-page canvas').wait_for()
  state=page.request.get(baseurl+'/api/flow/state').json()
  citation=page.request.get(baseurl+'/api/reading/source',params={'note_id':state['cards'][0]['noteId']}).json()
  assert page.locator('.content input[aria-label="页码"]').get_attribute('min')==str(citation['effective_chunk']['page_start'])
  assert not errors,errors
  return dict(actual_old_io_republish=True,preserved_page=4,split_expected_version=request.value.post_data_json['expected_version'],child=second,page_errors=errors)
 finally:browser.close()

def r3_app_browser(pw,baseurl,seed,out):
 browser,ctx,page,errors=context(pw,baseurl)
 try:
  page.goto(baseurl);go(page,3);next_reading(page)
  # Do not enter trace. Advance actual review cards through native UI until N2.
  for _ in range(4):
   state=page.request.get(baseurl+'/api/flow/state').json();current=state['cards'][0]
   if current['noteId']==seed['notes']['4']['note_id']:break
   reveal(page)
   with page.expect_response(lambda r:urlsplit(r.url).path=='/api/answer' and r.request.method=='POST') as answered:page.locator('.ease-good').click()
   assert answered.value.ok;page.wait_for_load_state('networkidle')
  else:raise AssertionError('fixture did not reach page-four native review note')
  assert page.locator('.related-panel .related-item').count()==0,'face-down answer leak'
  reveal(page);page.locator('.note-column md-primary-tab').filter(has_text='相关卡片').click()
  page.wait_for_function("(()=>{const t=document.querySelector('.related-panel')?.textContent??'';return t.includes('PEER_PAGE_4')&&!t.includes('PEER_PAGE_3')})()")
  assert not errors,errors
  return dict(last_left_page=3,review_source_page=4,entered_trace=False,exact_page_four_peers=True,page_errors=errors)
 finally:browser.close()

def mounted_peers(pw,baseurl,seed,out,component_dist):
 browser,ctx,page,errors=context(pw,baseurl);held=[];real_batches=[]
 try:
  page.on('response',lambda response:real_batches.append(len(response.json()['cards'])) if urlsplit(response.url).path=='/api/reading/cards' and response.ok else None)
  page.goto(baseurl+'/__probe__/tests/page-related-fixture.html?'+urlencode({'seed':json.dumps(seed)}))
  def click(label):page.get_by_role('button',name=label,exact=True).click()
  def assert_peer(marker,absent):
   page.wait_for_function("([wanted,absent])=>{const t=document.querySelector('.related-panel')?.textContent??'';return t.includes(wanted)&&!t.includes(absent)}",arg=[marker,absent])
  assert_peer('PEER_PAGE_3','PEER_PAGE_4')
  click('Review4');assert_peer('PEER_PAGE_4','PEER_PAGE_3')
  # Hold real responses, then release them after newer page/doc/revision data.
  oldpath=f'/api/lectures/{seed["document_id"]}/revisions/{seed["revision_id"]}/pages/3/related'
  arm=[False]
  def delayed(route):
   if arm[0] and urlsplit(route.request.url).path==oldpath:
    arm[0]=False;held.append((route,route.fetch()))
   else:route.continue_()
  ctx.route('**/api/lectures/**/related',delayed)
  cases=[]
  for label,marker in [('A4','PEER_PAGE_4'),('B3','B_DOCUMENT_PAGE_3'),('R3','NEW_REVISION_PAGE_3')]:
   arm[0]=True;click('A3')
   for _ in range(100):
    if held:break
    page.wait_for_timeout(20)
   assert held,'did not capture real old response'
   click(label);assert_peer(marker,'PEER_PAGE_3')
   route,response=held.pop(0);assert response.ok and response.json()['page']==3
   route.fulfill(response=response);page.wait_for_timeout(150)
   assert_peer(marker,'PEER_PAGE_3');cases.append(label)
  click('A5');page.wait_for_function("document.querySelectorAll('.related-panel .related-item').length===105")
  assert real_batches[-2:]==[100,5],real_batches
  assert 'LARGE_PEER_104' in page.locator('.related-panel').inner_text()
  click('A2');page.wait_for_function("document.querySelectorAll('.related-panel .related-item').length===0")
  assert not errors,errors
  return dict(actual_component='RelatedPanel',review_ignores_reading_locator=True,delayed_real_response_cases=cases,native_same_page_peer_count=105,detail_batches=real_batches[-2:],empty_page_clears=True,page_errors=errors)
 finally:
  for route,response in held:route.fulfill(response=response)
  browser.close()

def files_smoke(pw,baseurl,seed,out):
 browser,ctx,page,errors=context(pw,baseurl)
 try:
  page.goto(baseurl);page.locator('.nav-rail__item[title="文件"]').click();page.locator('.fb-workspace').wait_for()
  page.locator('[data-entry="SYNTHETIC.md"]').wait_for()
  page.locator('.fb-header md-outlined-button').click();page.get_by_label('名称',exact=True).fill('SYNTHETIC created');page.get_by_role('button',name='确认',exact=True).click()
  page.locator('[data-entry="SYNTHETIC created"]').wait_for()
  page.get_by_label('选择 SYNTHETIC.md',exact=True).check();page.locator('.fb-selection-actions md-text-button').filter(has_text='移动').click()
  page.get_by_label('目标文件夹',exact=True).select_option('SYNTHETIC destination')
  with page.expect_response(lambda r:urlsplit(r.url).path=='/api/files/move' and r.request.method=='POST') as moved:page.get_by_role('button',name='确认',exact=True).click()
  assert moved.value.ok,moved.value.text()
  assert page.locator('.fb-status,.fb-report').count()==0
  assert page.locator('.fb-footer').is_visible() and page.locator('.fb-operation-feedback').count()==0
  page.locator('.fb-header .files-menu md-icon-button').locator('button').click();page.get_by_role('menuitem',name='撤销上次单项迁移').wait_for()
  assert not errors,errors
  return dict(actual_files_mkdir_and_move=True,removed_bottom_rows=0,folder_count_footer_visible=True,undo_menu_preserved=True,page_errors=errors)
 finally:browser.close()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--dist',required=True);parser.add_argument('--component-dist');parser.add_argument('--output',required=True);parser.add_argument('--case',choices=['r1','r3','gaps','legacy','api'],default='r1');parser.add_argument('--seed-only',action='store_true');args=parser.parse_args()
 source=Path(args.source).resolve();dist=Path(args.dist).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
 assert source.is_relative_to(Path(os.environ['PAGE_PROBE_RUN_ROOT']).resolve()) and (source/'backend/app.py').is_file() and (dist/'index.html').is_file()
 base.ROOT=source;sys.path.insert(0,str(source/'backend'));proc=None;b=None;result=dict(schema_version=2,case=args.case,status='failed',source=str(source),dist=str(dist))
 try:
  b=base.prepare(dist)
  async def setup():
   seed=await base.populate(b)
   if args.case=='gaps':seed=await extra_seed(b,seed)
   if args.case=='api':result['api']=await base.api_checks(b,seed)
   await b._pylib.close();return seed
  seed=asyncio.run(setup());result['seed']=seed
  if not args.seed_only and args.case!='api':
   port=reserve_loopback_port();baseurl=f'http://127.0.0.1:{port}';env=dict(os.environ)
   if args.case=='gaps':
    assert args.component_dist and Path(args.component_dist).is_dir()
    env.update(PAGE_PROBE_SOURCE=str(source),PAGE_PROBE_COMPONENT_DIST=str(Path(args.component_dist).resolve()))
    argv=[sys.executable,'-m','uvicorn','page_reading_fixture_server:app','--app-dir',str(Path(__file__).resolve().parent),'--host','127.0.0.1','--port',str(port)]
   else:argv=[sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)]
   with (out/'server.log').open('w') as log:
    proc=subprocess.Popen(argv,cwd=source/'backend',env=env,stdout=log,stderr=subprocess.STDOUT);verify_server(proc,baseurl)
    from playwright.sync_api import sync_playwright
    if args.case=='legacy':result['browser']=base.browser_checks(baseurl,seed,out)
    else:
     with sync_playwright() as pw:
      if args.case=='r1':result['browser']=r1_browser(pw,baseurl,seed,out)
      elif args.case=='r3':result['browser']=r3_app_browser(pw,baseurl,seed,out)
      elif args.case=='gaps':result['browser']=dict(peers=mounted_peers(pw,baseurl,seed,out,args.component_dist),files=files_smoke(pw,baseurl,seed,out))
  result['status']='passed'
 except BaseException:result['traceback']=traceback.format_exc();raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=20)
  if b:
   try:asyncio.run(b._pylib.close())
   except Exception:pass
  (out/'probe-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));cleanup()
if __name__=='__main__':main()
