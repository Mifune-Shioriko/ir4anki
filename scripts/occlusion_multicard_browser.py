"""Real production App + disposable ASGI/native owner. Synthetic PDF only."""
import argparse,asyncio,json,os,subprocess,sys,traceback
from pathlib import Path

def browser_flow(pw,url,seed,out):
 import page_reading_g002_probe as helpers
 from playwright.sync_api import expect
 browser,ctx,page,errors=helpers.context(pw,url);page.set_viewport_size({'width':1440,'height':1600});held=[];ledger={'phases':[],'errors':errors}
 s=seed['drafts']['3']['source'];a=seed['drafts']['3']['authoring_id'];prefix='/api/lectures'
 def get(path):
  res=ctx.request.get(url+prefix+path);assert res.ok,(path,res.status);return res.json()
 def read(id):return get('/authoring/'+id)
 def ready(id=None):
  page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true'")
  value=page.locator('.occlusion-editor').get_attribute('data-authoring-id')
  if id:assert value==id,(value,id)
  return value
 def button(text):return page.locator('.content').get_by_role('button',name=text,exact=True)
 def close():
  button('保存草稿并返回').click();page.locator('.occlusion-editor').wait_for(state='detached')
 def picker(id):
  button('本页卡片').click();page.locator('[aria-label="本页图片挖空卡片"]').wait_for()
  page.locator(f'[aria-label="本页图片挖空卡片"] [data-authoring-id="{id}"] md-text-button').click();ready(id)
 def drag(tool,start,end):
  button(tool).click();box=page.get_by_role('group',name='遮罩画布',exact=True).bounding_box();assert box
  page.mouse.move(box['x']+box['width']*start[0],box['y']+box['height']*start[1]);page.mouse.down();page.mouse.move(box['x']+box['width']*end[0],box['y']+box['height']*end[1],steps=8);page.mouse.up()
 def publish():
  with page.expect_response(lambda r:r.url.endswith('/publish') and r.request.method=='POST') as response:button('保存到 Anki').click()
  op=response.value.json();assert op['status']=='complete',op
  page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for();ledger['last_publish_request']=response.value.request.post_data_json;return op
 try:
  page.goto(url);helpers.go(page,3)
  before=read(a);ledger.update(a=a,phases=['actual reading page 3'])
  # Compatibility: cached single-page authoring still opens the same project.
  page.evaluate('([key,id])=>localStorage.setItem(key,id)',[f'ir4anki.authoring.page.{s["document_id"]}.{s["revision_id"]}.3',a])
  button('图片挖空').click();ready(a);close()
  expect(button('新建图片挖空')).to_be_visible();button('新建图片挖空').click();b=ready();assert b!=a;ledger.update(b=b);ledger['phases'].append('new independent editor B')
  expect(button('新建图片挖空')).to_be_disabled();expect(button('本页卡片')).to_be_disabled()
  drag('裁剪',(.05,.1),(.9,.9));drag('矩形遮罩',(.15,.2),(.4,.45))
  op=publish();note=op['items'][0]['result'];assert note['note_id']!=seed['notes']['3']['note_id']
  close();assert read(a)==before;ledger['phases'].append('published B; A unchanged')
  endpoint=f'/{s["document_id"]}/revisions/{s["revision_id"]}/pages/3/authoring'
  assert {x['authoring_id'] for x in get(endpoint)['drafts']}=={a,b}
  page.reload();helpers.go(page,3);picker(b)
  bdraft=read(b);assert len(bdraft['masks'])==1 and bdraft['crop']!=[0,0,1,1]
  # Hold a real committed PUT response. Close must await writer acknowledgement.
  def hold(route):
   if route.request.method!='PUT':route.continue_();return
   response=route.fetch();assert response.ok;held.append((route,response))
  ctx.route(url+prefix+'/authoring/'+b,hold)
  mask=page.get_by_role('button',name='遮罩 1',exact=True);mask.focus();mask.press('ArrowRight')
  button('保存草稿并返回').click()
  for _ in range(100):
   if held:break
   page.wait_for_timeout(20)
  assert held,'no real pending PUT';ledger['phases'].append('actual PUT acknowledgement held')
  assert page.locator('.occlusion-editor').is_visible()
  expect(button('新建图片挖空')).to_be_disabled();expect(button('本页卡片')).to_be_disabled()
  route,response=held.pop();route.fulfill(response=response);page.locator('.occlusion-editor').wait_for(state='detached');ctx.unroute(url+prefix+'/authoring/'+b,hold)
  picker(b);assert read(b)['masks']!=bdraft['masks'];op2=publish();assert all(op2['items'][0]['result'][k]==note[k] for k in ['note_id','card_ids']);close()
  assert read(a)==before
  # An unpublished independent draft survives leaving it and selecting B.
  button('新建图片挖空').click();c=ready();drag('矩形遮罩',(.6,.6),(.8,.8));close();picker(b);close();picker(c)
  assert len(read(c)['masks'])==1;close()
  page.reload();helpers.go(page,3);picker(c);assert page.get_by_role('button',name='遮罩 1',exact=True).count()==1;close()
  picker(a);assert read(a)==before;close()
  helpers.go(page,4);button('本页卡片').click();entries=page.locator('[aria-label="本页图片挖空卡片"] [data-authoring-id]');assert entries.count()==1
  assert entries.first.get_attribute('data-authoring-id')==seed['drafts']['4']['authoring_id'];button('收起').click()
  helpers.go(page,3);button('本页卡片').click();assert page.locator('[aria-label="本页图片挖空卡片"] [data-authoring-id]').count()==3;button('收起').click()
  # Retry the exact request via the real API; no second note/card may appear.
  retry=ctx.request.post(url+prefix+'/authoring/'+b+'/publish',data=ledger['last_publish_request']);assert retry.ok and retry.json()['items'][0]['result']['card_ids']==note['card_ids']
  assert not errors,errors
  ledger=dict(phases=ledger['phases']+['reload/edit/unpublished draft/page-scope/retry complete'],status='passed',a=a,b=b,unpublished=c,b_note=note,native_card_count_expected=5,page3_projects=3,page4_projects=1,pending_write_guard=True,reload=True,crop=True,independent_edit=True,errors=errors)
  page.screenshot(path=str(out/'browser.png'),full_page=True)
  return ledger
 finally:
  for route,response in held:
   try:route.fulfill(response=response)
   except Exception:pass
  (out/'browser-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2));browser.close()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--dist',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 source=Path(args.source).resolve();dist=Path(args.dist).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
 sys.path.insert(0,str(source/'backend'))
 import page_reading_probe as fixture
 fixture.ROOT=source
 sys.path.insert(0,str(source/'backend'))
 from native_fixture import cleanup,reserve_loopback_port,verify_server
 result=dict(schema_version=1,status='failed',source=str(source),dist=str(dist));proc=None;b=None
 try:
  b=fixture.prepare(dist)
  async def seed():
   value=await fixture.populate(b);await b._pylib.close();return value
  value=asyncio.run(seed());result['seed']=value
  port=reserve_loopback_port();url=f'http://127.0.0.1:{port}'
  with (out/'server.log').open('w') as log:
   proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=source/'backend',env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT);verify_server(proc,url)
   from playwright.sync_api import sync_playwright
   with sync_playwright() as pw:result['browser']=browser_flow(pw,url,value,out)
  proc.terminate();proc.wait(timeout=20);proc=None
  # Server is closed before reopening the actual collection, proving durability.
  from anki.collection import Collection
  col=Collection(os.environ['ANKI_COLLECTION_PATH'])
  try:
   assert len(col.find_cards(''))==5
   note=col.get_note(result['browser']['b_note']['note_id']);assert note['AuthoringKey'].startswith(result['browser']['b']+':')
   assert [c.id for c in note.cards()]==result['browser']['b_note']['card_ids']
  finally:col.close()
  result.update(status='passed',native_reopened_cards=5)
 except BaseException:result['traceback']=traceback.format_exc();raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=20)
  (out/'probe-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));cleanup()
if __name__=='__main__':main()
