"""Pre-open bookmark form through actual IO publication, holding real refresh.
No substituted API success data. Reuses the accepted g002 native seed/helpers.
"""
import argparse,asyncio,importlib,json,os,subprocess,sys,traceback
from pathlib import Path
from urllib.parse import urlsplit
import page_reading_g002_probe as accepted
from native_fixture import cleanup,reserve_loopback_port,verify_server

def busy_browser(pw,baseurl,seed,out):
 browser,ctx,page,errors=accepted.context(pw,baseurl);held=[];posts=[];ledger=dict(phases=[],page_errors=errors)
 def save(): (out/'busy-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
 def record(request):
  if urlsplit(request.url).path=='/api/lectures/split' and request.method=='POST':posts.append(request.post_data_json)
 page.on('request',record)
 try:
  page.goto(baseurl);accepted.go(page,3);accepted.next_reading(page);accepted.reveal(page)
  page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True).click();page.locator('.content .pdf-page canvas').wait_for()
  child=accepted.split(page,baseurl,3,8);assert child['status']=='todo' and child['version']==1
  accepted.go(page,4)
  content=page.locator('.content');content.get_by_role('button',name='按页书签分割',exact=True).click()
  page.get_by_label('分割开始页',exact=True).fill('4');page.get_by_label('分割结束页',exact=True).fill('6')
  assert content.get_by_role('spinbutton',name='页码',exact=True).input_value()=='4'
  ledger['phases'].append('todo v1 trace child; left p4; bookmark form 4–6 opened before IO');save()
  page.evaluate('([key,id])=>localStorage.setItem(key,id)',[f'ir4anki.authoring.page.{seed["document_id"]}.{seed["revision_id"]}.4',seed['drafts']['4']['authoring_id']])
  content.get_by_role('button',name=accepted.re.compile('^(编辑)?图片挖空$')).click()
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  assert page.locator('.occlusion-editor').get_attribute('data-authoring-id')==seed['drafts']['4']['authoring_id']
  path=f'/api/lectures/{seed["document_id"]}/revisions/{seed["revision_id"]}/pages/4/reading-source'
  def hold_actual(route):
   response=route.fetch() # the real native backend has already committed v2
   assert response.ok,(response.status,response.text())
   value=response.json();assert value['segment_id']==child['segment_id'] and value['version']==2 and value['page']==4,value
   held.append((route,response));ledger['held_actual_refresh']=dict(version=value['version'],segment_id=value['segment_id'],page=value['page']);save()
  ctx.route(baseurl+path+'?*',hold_actual)
  with page.expect_response(lambda r:r.url.endswith('/publish') and r.request.method=='POST') as pub:
   page.locator('md-filled-button').filter(has_text='保存到 Anki').click()
  operation=pub.value.json();assert pub.value.ok and operation['status']=='complete',operation
  assert operation['items'][0]['result']['note_id']==seed['notes']['4']['note_id']
  assert operation['items'][0]['result']['card_ids']==seed['notes']['4']['card_ids']
  page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
  for _ in range(100):
   if held:break
   page.wait_for_timeout(20)
  assert len(held)==1,'actual reading-source refresh was not held'
  page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
  page.locator('.lecture-split').wait_for(state='visible')
  assert page.get_by_label('分割开始页',exact=True).input_value()=='4'
  assert page.get_by_label('分割结束页',exact=True).input_value()=='6'
  assert content.get_by_role('spinbutton',name='页码',exact=True).input_value()=='4'
  assert content.get_by_role('button',name='按页书签分割',exact=True).is_disabled(),'fixture did not enter parent busy boundary'
  confirm=page.get_by_role('button',name='确认书签分割',exact=True)
  before=len(posts);ledger['confirm_disabled_while_refresh_held']=confirm.is_disabled();save()
  if not confirm.is_disabled():
   # Genuine g002 browser RED: prove this open form sends native stale v1/409.
   with page.expect_response(lambda r:urlsplit(r.url).path=='/api/lectures/split' and r.request.method=='POST') as bad:
    confirm.click()
   ledger['stale_split_response_status']=bad.value.status;ledger['split_posts']=posts;save()
   assert bad.value.status==409 and posts[-1]['expected_version']==1,(bad.value.status,posts)
   raise AssertionError('BUSY_CONFIRM_SENT_STALE_SPLIT: enabled old form sent expected_version=1 and received real 409')
  # Native disabled control plus deliberately dispatched host event: the latter
  # also exercises the handler boundary; the separate unit test counts begin().
  page.locator('.lecture-split md-filled-tonal-button').evaluate("el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true,composed:true}))")
  page.wait_for_timeout(150);assert len(posts)==before,'busy confirmation dispatched a split request'
  ledger['no_split_while_busy']=True;ledger['phases'].append('returned with form open; confirm disabled; dispatched host click produced no split');save()
  route,response=held.pop();route.fulfill(response=response) # unchanged actual body
  from playwright.sync_api import expect
  expect(confirm).to_be_enabled();page.wait_for_load_state('networkidle')
  assert page.locator('.lecture-split').is_visible(),'fresh metadata closed the pre-open form'
  assert page.get_by_label('分割开始页',exact=True).input_value()=='4'
  assert page.get_by_label('分割结束页',exact=True).input_value()=='6'
  assert content.get_by_role('spinbutton',name='页码',exact=True).input_value()=='4'
  with page.expect_response(lambda r:urlsplit(r.url).path=='/api/lectures/split' and r.request.method=='POST') as done:confirm.click()
  assert done.value.ok,done.value.text()
  second=done.value.json()['chunks'][0]
  assert len(posts)==before+1 and posts[-1]['expected_version']==2
  assert (posts[-1]['page_start'],posts[-1]['page_end'])==(4,6)
  assert second['parent_segment_id']==child['segment_id'] and (second['page_start'],second['page_end'])==(4,6)
  ledger.update(fresh_split_expected_version=2,form_values_preserved=[4,6],selected_page_preserved=4,split_posts=posts,child=second)
  ledger['phases'].append('released real v2 refresh; same form and p4 preserved; native recursive split succeeded with v2');save()
  assert not errors,errors
  return ledger
 finally:
  for route,response in held:
   try:route.fulfill(response=response)
   except Exception:pass
  save();browser.close()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--dist',required=True);parser.add_argument('--output',required=True);parser.add_argument('--seed-only',action='store_true');a=parser.parse_args()
 source=Path(a.source).resolve();dist=Path(a.dist).resolve();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
 assert source.is_relative_to(Path(os.environ['PAGE_PROBE_RUN_ROOT']).resolve()) and (source/'backend/app.py').is_file() and (dist/'index.html').is_file()
 accepted.base.ROOT=source;sys.path.insert(0,str(source/'backend'));b=None;proc=None;result=dict(schema_version=3,case='pre-open-split-publication-busy',source=str(source),dist=str(dist),status='failed')
 try:
  b=accepted.base.prepare(dist)
  async def setup():
   seed=await accepted.base.populate(b);await b._pylib.close();return seed
  seed=asyncio.run(setup());result['seed']=seed
  if not a.seed_only:
   port=reserve_loopback_port();baseurl=f'http://127.0.0.1:{port}'
   with (out/'server.log').open('w') as log:
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=source/'backend',env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT);verify_server(proc,baseurl)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:result['browser']=busy_browser(pw,baseurl,seed,out)
  result['status']='passed'
 except BaseException:result['traceback']=traceback.format_exc();raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=20)
  if b:
   try:asyncio.run(b._pylib.close())
   except Exception:pass
  (out/'probe-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));cleanup()
if __name__=='__main__':main()
