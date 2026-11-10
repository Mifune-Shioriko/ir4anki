"""Actual isolated app screenshots and interactions, never a static mock.
Run with --dist pointing at a fresh scratch build. Requires loopback capability.
"""
import argparse,asyncio,hashlib,json,os,subprocess,sys,time,traceback
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,cleanup,reserve_loopback_port,verify_server
from lecture_documents import Documents
from lecture_reading import PageReading
from lecture_real_documents_test import make_pdf

def visual_pdf():
 # A deterministic, non-private vector lecture fixture, standard embedded-free
 # PDF font; deliberately includes color patches for the dark-mode contract.
 objects=[]
 def add(body):objects.append(body.encode());return len(objects)
 catalog=add('');pages=add('');font=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');kids=[]
 for i in range(4):
  content=f'''q 1 1 1 rg 0 0 640 480 re f .31 .22 .56 rg 0 380 640 100 re f
 BT /F1 14 Tf 1 1 1 rg 36 436 Td (SYNTHETIC / LECTURE {i+1:02d}) Tj ET
 BT /F1 28 Tf 1 1 1 rg 36 398 Td (Learning loops) Tj ET
 .93 .91 .98 rg 36 210 260 132 re f .85 .94 .89 rg 320 210 284 132 re f
 BT /F1 20 Tf .15 .12 .2 rg 54 304 Td (Observe) Tj 284 0 Td (Explain) Tj ET
 BT /F1 14 Tf 54 268 Td (What changed?) Tj 284 0 Td (Why did it change?) Tj ET
 .8 .25 .18 rg 54 230 52 16 re f .1 .55 .4 rg 338 230 52 16 re f
 BT /F1 20 Tf .15 .12 .2 rg 36 152 Td (Question - recall - revise) Tj ET
 BT /F1 14 Tf 36 110 Td (Generated fixture. No private lecture content.) Tj ET Q'''.encode()
  stream=add('<< /Length '+str(len(content))+' >>\nstream\n'+content.decode()+'\nendstream')
  kids.append(add(f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 640 480] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {stream} 0 R >>'))
 objects[catalog-1]=f'<< /Type /Catalog /Pages {pages} 0 R >>'.encode();objects[pages-1]=f'<< /Type /Pages /Count 4 /Kids [{" ".join(str(k)+" 0 R" for k in kids)}] >>'.encode()
 data=bytearray(b'%PDF-1.7\n');offsets=[0]
 for index,body in enumerate(objects,1):offsets.append(len(data));data+=f'{index} 0 obj\n'.encode()+body+b'\nendobj\n'
 xref=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
 for off in offsets[1:]:data+=f'{off:010d} 00000 n \n'.encode()
 data+=f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode();return bytes(data)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--dist',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
 out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True);dist=Path(args.dist).resolve();root=Path(__file__).resolve().parents[1]
 assert dist!=root/'frontend/dist' and (dist/'index.html').is_file()
 ledger=[];errors=[];error_details=[];network=[]
 def write(): (out/'ui-ledger.json').write_text(json.dumps(dict(cases=ledger,page_errors=errors,page_error_details=error_details,fixture='generated synthetic vector PDF; no trusted real lecture visual comparison'),ensure_ascii=False,indent=2))
 port=reserve_loopback_port() # fail once before any fixture if sandbox denies
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':os.environ.pop(k)
 env=dict(os.environ);configure(env);env.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='0',REVIEW_DIST_DIR=str(dist),ANKI_SYNC_POLICY='manual')
 state=Path(env['ANKI_STATE_DIR']);corpus=Path(env['ANKI_NOTES_DIR']);docs=Documents(state/'lectures');reading=PageReading(state/'lectures');pdf=visual_pdf();(corpus/'generated-lecture.pdf').write_bytes(pdf);(corpus/'relocated').mkdir()
 rev=docs.import_bytes('generated-lecture.pdf',pdf);seg=reading.seed(rev['document_id'],rev['revision_id'],'generated-lecture.pdf',4);doc=docs.document(rev['document_id']);doc.update(listed=True,reading_revision=rev['revision_id']);docs.put('documents',doc['document_id'],doc)
 # Seed a portable managed note through the supported owner before server owns it.
 from anki_backend import PylibClient
 async def seed():
  c=PylibClient(env['ANKI_COLLECTION_PATH'])
  try:
   from lecture_integration import Lectures
   lectures=Lectures(state/'lectures');raster=docs.raster(rev['document_id'],rev['revision_id'],3)
   draft=lectures.authoring.create(dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],segment_id=seg['segment_id'],page=3),raster)
   draft=lectures.authoring.save(draft['authoring_id'],dict(draft,masks=[dict(mask_id='b'*32,rect=[.08,.28,.3,.2])],question='Which step comes first?'),expected=1)
   op=lectures.authoring.stage(draft['authoring_id'],draft['version'],'ui-seed',[])
   async def record(n,source):lectures.record(n,source)
   result=await lectures.authoring.publish(op['operation_id'],c.call,deck='2026',suspended=False,record_source=record)
   assert result['status']=='complete',result
   seed_result=result['items'][0]['result']
   pending=lectures.authoring.create(dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],segment_id=seg['segment_id'],page=4),docs.raster(rev['document_id'],rev['revision_id'],4))
   pending=lectures.authoring.save(pending['authoring_id'],dict(pending,masks=[dict(mask_id='c'*32,rect=[.1,.1,.2,.2]),dict(mask_id='d'*32,rect=[.5,.5,.2,.2])]),expected=1)
   operation=lectures.authoring.stage(pending['authoring_id'],pending['version'],'lost-browser-key',[])
   def fail(phase):
    if phase=='note':raise RuntimeError('fixture disconnect after real note write')
   partial=await lectures.authoring.publish(operation['operation_id'],c.call,deck='2026',suspended=False,record_source=record,fault=fail)
   assert partial['status']=='partial'
   return seed_result,pending['authoring_id'],operation['operation_id']
  finally:await c.close()
 seed_note,pending_id,pending_operation=asyncio.run(seed())
 base=f'http://127.0.0.1:{port}';log=(out/'server.log').open('w');proc=None
 try:
  proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=root/'backend',env=env,stdout=log,stderr=subprocess.STDOUT);verify_server(proc,base)
  from playwright.sync_api import sync_playwright
  with sync_playwright() as pw:
   try:
    browser=pw.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1400,'height':1000});page=context.new_page()
    def page_error(e):
     errors.append(str(e));error_details.append(dict(message=str(e),name=getattr(e,'name',None),stack=getattr(e,'stack',None),url=page.url,stage=ledger[-1].get('stage') if ledger else 'startup',width=page.viewport_size['width']));write()
    page.on('pageerror',page_error)
    def record_response(response):
     path=urlsplit(response.url).path
     if '/authoring' not in path and not ('/pages/' in path and path.endswith(('/image','/thumbnail'))) and not path.startswith('/media/'):return
     row=dict(method=response.request.method,path=path,url=response.url,status=response.status,content_type=response.headers.get('content-type',''))
     try:
      if '/authoring' in path:
       value=response.json();body=response.request.post_data_json if response.request.post_data else {}
       row.update(request_version=body.get('expected_version',body.get('version')) if isinstance(body,dict) else None,body_version=value.get('version'),published_version=value.get('published_version'),operation_id=value.get('operation_id'),operation_status=value.get('status'),request_id=body.get('request_id') if isinstance(body,dict) else None,source=value.get('source'),detail=value.get('detail'))
      else:
       data=response.body();row.update(png=data.startswith(b'\x89PNG\r\n\x1a\n'),sha256=hashlib.sha256(data).hexdigest(),size=len(data))
     except Exception as exc:row['diagnostic_error']=repr(exc)
     network.append(row);(out/'network-ledger.json').write_text(json.dumps(network,ensure_ascii=False,indent=2))
    page.on('response',record_response)
    # Explicit offline proof: only fixture origin is allowed.
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) or route.request.url.startswith(('data:','blob:')) else route.abort())
    def capture(stage,width,theme,scenario):
     # Only these direct checks are called assertions in the ledger. Scenario
     # descriptions are context, never a substitute for measured checks.
     checks=[];metrics={}
     assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),'horizontal document overflow'
     checks.append('no document horizontal overflow')
     if stage=='reading':
      page.wait_for_function("!document.querySelector('.content .lecture-loading') && document.querySelector('.content canvas')?.width>100")
      pixels=page.locator('.content .pdf-page canvas').evaluate("c=>({sample:Array.from(c.getContext('2d').getImageData(Math.floor(c.width*.2),Math.floor(c.height*.5),1,1).data),filter:getComputedStyle(c).filter})")
      assert pixels['filter']=='none',pixels
      assert all(abs(a-b)<=3 for a,b in zip(pixels['sample'],[237,232,250,255])),pixels
      current_page=int(page.locator('.content input[aria-label="页码"]').input_value())
      assert page.locator('.content .pdf-page canvas').get_attribute('aria-label')==f'第 {current_page} 页课件'
      assert page.locator('.content .page-annotation').get_attribute('aria-label')==f'第 {current_page} 页理解笔记'
      page.wait_for_function("n=>document.querySelector('.content .textLayer')?.textContent.includes('LECTURE '+String(n).padStart(2,'0'))",arg=current_page)
      checks.append('displayed page, annotation label and actual PDF text identify the same fixture page')
      pager=page.locator('.content').get_by_role('toolbar',name='课件页导航',exact=True)
      controls=[pager.get_by_role('button',name=name,exact=True).bounding_box() for name in ['上一页','下一页']]+[pager.locator('details.lecture-options > summary[aria-label="页面视图"]').bounding_box()]
      assert all(box and box['width']>=48 and box['height']>=48 for box in controls),controls
      assert max(box['y'] for box in controls)-min(box['y'] for box in controls)<2,controls
      metrics['pager_targets']=controls;checks.append('single local pager row; previous/next/view targets at least 48px')
      metrics['original_color_patch']=pixels;checks.append('PDF color patch matches fixture RGB in current theme')
      side=page.locator('.note-column');assert side.is_visible() if width>=1180 else side.count()==0
      checks.append('two panes at/above 1180; reference pane absent below threshold')
     if stage=='annotation-edit':
      editor=page.locator('.content .page-annotation .cm-content')
      editor.click();editor.press('Control+End');editor.press('Shift+Home')
      page.wait_for_function("getSelection()?.toString().includes('TARGET40')")
      assert page.locator('.content .study-action-dock').count()==0,'study dock must be absent throughout annotation editing'
      geometry=editor.evaluate("""el=>{const range=getSelection().getRangeAt(0),r=range.getBoundingClientRect(),box=el.closest('.cm-editor').getBoundingClientRect(),x=Math.max(r.left,box.left)+Math.min(r.width,box.width)/2,y=r.top+r.height/2;return {selection:getSelection().toString(),selected:{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom},editor:{x:box.x,y:box.y,width:box.width,height:box.height,bottom:box.bottom},viewport:innerHeight,hit:!!document.elementFromPoint(x,y)?.closest('.cm-editor')}}""")
      chosen=geometry['selected'];area=geometry['editor']
      assert chosen['height']>0 and chosen['y']>=max(0,area['y'])-2 and chosen['bottom']<=min(geometry['viewport'],area['bottom'])+2,geometry
      assert geometry['hit'],'selected editor text must be topmost, not covered by a float'
      metrics['below_fold_selection']=geometry;checks+=['long annotation last-line selection is visible below initial fold','selection is topmost and inside editor/viewport','study dock absent while annotation editing']
     if stage=='QA':
      container=page.locator('.lecture-inline-qa .lecture-qa-fields');fields=container.locator(':scope > section');assert fields.count()==2
      area=container.bounding_box();boxes=[fields.nth(i).bounding_box() for i in range(2)]
      for box in boxes:assert box['x']>=area['x']-1 and box['x']+box['width']<=area['x']+area['width']+1
      if area['width']>540 and width>700:assert abs(boxes[0]['y']-boxes[1]['y'])<3 and boxes[0]['x']+boxes[0]['width']<=boxes[1]['x']+1
      else:assert boxes[1]['y']>=boxes[0]['y']+boxes[0]['height']-1
      previews=page.locator('.lecture-inline-qa .authoring-preview');assert previews.count()==2
      assert all(previews.nth(i).bounding_box()['height']>=139 for i in range(2))
      metrics['fields']=boxes;checks+=['QA geometry matches available main-panel space','both preview areas reserve at least 140px']
     if stage=='review-front':
      assert page.locator('.note-column canvas,.note-column .page-annotation .note-body').count()==0
      trace=page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True);assert trace.is_disabled()
      checks.append('front has no source raster/annotation and trace is disabled')
     path=out/f'{width}-{theme}-{stage}.png';page.screenshot(path=str(path),full_page=True)
     row=dict(stage=stage,width=width,theme=theme,screenshot=str(path),scenario=scenario,assertions=checks,metrics=metrics,errors=list(errors));ledger.append(row);write();assert not errors,errors
    def matrix(stage,assertions):
     for width in [1400,1180,1179,760,420]:
      for theme in ['light','dark']:
       page.set_viewport_size({'width':width,'height':1000});page.emulate_media(color_scheme=theme);page.wait_for_timeout(200)
       assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),'horizontal document overflow'
       capture(stage,width,theme,assertions+['no document horizontal overflow'])
     page.set_viewport_size({'width':1400,'height':1000});page.emulate_media(color_scheme='light')
    def assert_page(n,scope='.content'):
     page.wait_for_function("""([scope,n])=>{const root=document.querySelector(scope);return root&&!root.querySelector('.lecture-loading')&&root.querySelector('input[aria-label=页码]')?.value===String(n)&&root.querySelector('.pdf-page canvas')?.getAttribute('aria-label')===`第 ${n} 页课件`&&root.querySelector('.textLayer')?.textContent.includes('LECTURE '+String(n).padStart(2,'0'))&&root.querySelector('.page-annotation')?.getAttribute('aria-label')===`第 ${n} 页理解笔记`}""",arg=[scope,n])
     ledger.append(dict(stage='page-agreement',width=page.viewport_size['width'],theme=page.evaluate("matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'"),page=n,scope=scope,assertions=['input, canvas label, actual PDF text and annotation identify exact page'],errors=list(errors)));write()
    def go_page(n,scope='.content'):
     control=page.locator(scope).get_by_role('spinbutton',name='页码',exact=True)
     control.fill(str(n));control.press('Enter');assert_page(n,scope)
    def toggle_view(pager):
     summary=pager.locator('details.lecture-options > summary[aria-label="页面视图"]')
     before=summary.evaluate('el=>el.parentElement.open')
     summary.press('Enter')
     assert summary.evaluate('el=>el.parentElement.open') is (not before),'native summary must toggle its actual details via keyboard'
    def png_resource(image,expected_hash=None,prefix='/api/lectures/'):
     image.scroll_into_view_if_needed()
     image.evaluate("async image=>{await image.decode();if(!image.naturalWidth||!image.naturalHeight)throw Error('image did not decode')}")
     path=image.get_attribute('data-resource-url') or image.get_attribute('src')
     assert path.startswith(prefix),path
     response=page.request.get(base+path);data=response.body()
     assert response.ok and response.headers.get('content-type','').split(';')[0]=='image/png',(path,response.status,response.headers)
     assert data.startswith(b'\x89PNG\r\n\x1a\n'),(path,data[:80])
     digest=hashlib.sha256(data).hexdigest()
     if expected_hash is not None:assert digest==expected_hash,(digest,expected_hash)
     assert any(row['url']==base+path and row['status']==200 and row.get('png') and row.get('sha256')==digest and row['content_type'].split(';')[0]=='image/png' for row in network),('actual browser raster/thumbnail request missing',path,network)
     return dict(url=path,sha256=digest,bytes=len(data),natural=image.evaluate('i=>[i.naturalWidth,i.naturalHeight]'))
    def raster_ready():
     page.wait_for_function("document.querySelector('.occlusion-editor')?.dataset.rasterReady==='true' && document.querySelector('.occlusion-canvas img')?.naturalWidth>0")
     image=page.locator('.occlusion-canvas img');aid=page.locator('.occlusion-editor').get_attribute('data-authoring-id')
     server=page.request.get(base+'/api/lectures/authoring/'+aid).json()
     resource=png_resource(image,server['raster']['raster_hash'])
     assert resource['natural']==[server['raster']['width'],server['raster']['height']]
     assert image.get_attribute('data-raster-hash')==server['raster']['raster_hash']
     assert int(page.locator('.occlusion-editor').get_attribute('data-page'))==server['source']['page']
     ledger.append(dict(stage='verified-raster',width=page.viewport_size['width'],source=server['source'],resource=resource,writer=json.loads(page.locator('.occlusion-editor').get_attribute('data-writer-state')),assertions=['actual browser API image/png response and PNG signature','byte SHA256 matches immutable raster hash','decoded dimensions and authoring page match server'],errors=list(errors)));write()
     return server
    def close_authoring(expected_page,clean=True):
     editor=page.locator('.occlusion-editor');aid=editor.get_attribute('data-authoring-id')
     before=page.request.get(base+'/api/lectures/authoring/'+aid).json();start=len(network)
     state=json.loads(editor.get_attribute('data-writer-state'))
     page.get_by_role('button',name='保存草稿并返回',exact=True).click()
     page.locator('.occlusion-editor').wait_for(state='detached');assert_page(expected_page)
     after=page.request.get(base+'/api/lectures/authoring/'+aid).json()
     puts=[row for row in network[start:] if row['method']=='PUT' and row['path']=='/api/lectures/authoring/'+aid]
     assert all(row['status']==200 for row in puts),puts
     if clean:assert not puts and after['version']==before['version'],(state,puts,before['version'],after['version'])
     ledger.append(dict(stage='authoring-closed',width=page.viewport_size['width'],source=after['source'],writer=state,before_version=before['version'],after_version=after['version'],responses=puts,assertions=['reader restored and exact page agreement','no stale/duplicate cleanup PUT','clean close has no version bump' if clean else 'dirty draft acknowledged before return'],errors=list(errors)));write()
     return after
    page.goto(base);page.locator('md-filled-button').filter(has_text='开始').click();page.locator('.content .pdf-page canvas').wait_for();page.wait_for_function("document.querySelector('.content canvas')?.width>100")
    assert_page(1)
    page.locator('.content').get_by_role('button',name='下一页',exact=True).click();assert_page(2)
    page.locator('.content').get_by_role('button',name='上一页',exact=True).click();assert_page(1)
    go_page(4)
    for invalid in ['0','99','','2.5']:
     control=page.locator('.content').get_by_role('spinbutton',name='页码',exact=True)
     control.fill(invalid);control.press('Tab');assert_page(4)
     assert page.locator('.content .lecture-page-error[role=alert]').is_visible()
     assert control.get_attribute('aria-invalid')=='true'
    # DOM resources must be API URLs, never HTML200 from the SPA fallback.
    pager=page.locator('.content').get_by_role('toolbar',name='课件页导航',exact=True)
    toggle_view(pager);pager.get_by_role('button',name='缩略图',exact=True).click();toggle_view(pager)
    assert page.locator('.content .lecture-thumbnails img').count()==4
    for image in page.locator('.content .lecture-thumbnails img').all():png_resource(image)
    toggle_view(pager);pager.get_by_role('button',name='缩略图',exact=True).click();toggle_view(pager)
    go_page(4)
    key=f'ir4anki.authoring.page.{rev["document_id"]}.{rev["revision_id"]}.4'
    page.evaluate('([key,id])=>{localStorage.setItem(key,id);localStorage.removeItem("ir4anki.occlusion."+id+".publish")}',[key,pending_id])
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();raster_ready()
    assert page.request.get(base+'/api/lectures/authoring/'+pending_id).json()['pending_operation']['operation_id']==pending_operation
    page.locator('md-filled-button').filter(has_text='恢复未完成项').click();page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
    recovered=page.request.get(base+'/api/lectures/authoring/'+pending_id).json();assert recovered['source']['page']==4 and recovered['pending_operation'] is None and len(recovered['note_links'])==2 and len({x['note_id'] for x in recovered['note_links']})==2
    assert json.loads(page.locator('.occlusion-editor').get_attribute('data-writer-state'))['dirty'] is False
    closed=close_authoring(4)
    assert closed['masks']==recovered['masks'] and closed['note_links']==recovered['note_links']
    go_page(1);go_page(4)
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();reopened=raster_ready()
    assert reopened['masks']==recovered['masks'] and reopened['note_links']==recovered['note_links']
    # A real keyboard draft edit closes while autosave may still be pending.
    page.get_by_role('button',name='遮罩 1',exact=True).click();page.locator('.occlusion-canvas').focus();page.keyboard.press('ArrowRight')
    edited=close_authoring(4,clean=False)
    assert edited['masks'][0]['rect'][0]>recovered['masks'][0]['rect'][0] and edited['note_links']==recovered['note_links']
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();reopened=raster_ready()
    assert reopened['masks']==edited['masks'] and reopened['note_links']==edited['note_links']
    page.get_by_role('button',name='保存到 Anki',exact=True).click();page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
    republished=close_authoring(4)
    assert {(x['mask_id'],x['note_id']) for x in republished['note_links']}=={(x['mask_id'],x['note_id']) for x in recovered['note_links']}
    # Actual second HTTP client changes server version while this editor is
    # clean. Stage rejects before any operation; no routed/fake response.
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();clean=raster_ready()
    endpoint=base+'/api/lectures/authoring/'+clean['authoring_id']
    remote=dict(clean,question='Second client newer revision')
    changed=page.request.put(endpoint,data={'value':remote,'expected_version':clean['version']});assert changed.ok,changed.text()
    with page.expect_response(lambda response:response.url==endpoint+'/publish' and response.request.method=='POST') as rejected:
     page.get_by_role('button',name='保存到 Anki',exact=True).click()
    assert rejected.value.status==409,rejected.value.text()
    page.locator('.occlusion-editor [role=alert]').filter(has_text='发布已确认未创建操作').wait_for()
    request=rejected.value.request.post_data_json
    proof=page.request.post(endpoint+'/publication-status',data=request);assert proof.ok,proof.text()
    assert proof.json()['state']=='rejected' and proof.json()['draft']['pending_operation'] is None
    marker='ir4anki.occlusion.'+clean['authoring_id']+'.publish'
    assert page.evaluate('(key)=>localStorage.getItem(key)',marker) is None
    assert json.loads(page.locator('.occlusion-editor').get_attribute('data-writer-state'))['locked'] is False
    close_authoring(4)
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();raster_ready()
    page.locator('.lecture-conflict').wait_for()
    page.once('dialog',lambda dialog:dialog.accept())
    page.get_by_role('button',name='检查最新版本并合并本地内容',exact=True).click()
    page.locator('.occlusion-editor [role=status]').filter(has_text='已合并').wait_for()
    page.get_by_role('button',name='遮罩 1',exact=True).click();page.locator('.occlusion-canvas').focus();page.keyboard.press('ArrowRight')
    page.get_by_role('button',name='保存到 Anki',exact=True).click();page.locator('.occlusion-editor [role=status]').filter(has_text='已发布').wait_for()
    merged=close_authoring(4)
    assert {(x['mask_id'],x['note_id']) for x in merged['note_links']}=={(x['mask_id'],x['note_id']) for x in clean['note_links']}
    assert merged['question']==clean['question'],'only explicit merge may replace the remote content'
    ledger.append(dict(stage='publication-rejection-merge',width=1400,source=merged['source'],request=request,receipt=proof.json()['state'],assertions=['actual second-client version advance -> stage409 with durable no-operation proof','rejected marker cleared and local draft survives close/reopen','explicit merge/edit/new publish succeeds with same managed mask/note identities'],errors=list(errors)));write()
    go_page(1)
    for width in [1400,1180,1179,760,420]:
     for theme in ['light','dark']:
      page.set_viewport_size({'width':width,'height':1000});page.emulate_media(color_scheme=theme);page.wait_for_timeout(250)
      assert page.locator('.content .pdf-page canvas').is_visible()
      overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth');assert not overflow,'horizontal document overflow'
      capture('reading',width,theme,['actual PDF canvas visible','no document horizontal overflow','original color raster'])
    page.set_viewport_size({'width':1400,'height':1000});page.emulate_media(color_scheme='light')
    page.locator('.content').get_by_text('添加理解笔记',exact=False).click();editor=page.locator('.content .page-annotation .cm-content');editor.wait_for();editor.fill('Generated understanding $x^2$.\n\nA personal explanation.\n'+''.join(f'Long understanding line {i:02d}\n' for i in range(1,40))+'Below-fold TARGET40');page.wait_for_timeout(1300)
    assert page.locator('.content .page-annotation').get_by_text('已保存',exact=True).is_visible();matrix('annotation-edit',['real CM6 editor','server save acknowledged'])
    page.locator('.content .page-annotation').get_by_role('button',name='收起',exact=True).click()
    page.locator('.content .study-action-dock').wait_for(state='visible')
    page.locator('.content .page-annotation').get_by_role('button',name='编辑理解笔记',exact=True).click()
    page.locator('.content .page-annotation .cm-content').wait_for()
    assert page.locator('.content .study-action-dock').count()==0
    page.reload();page.locator('.content .page-annotation .note-body').filter(has_text='Generated understanding').wait_for()
    # Select actual rendered annotation text. These actions create real QA
    # and Cloze notes only after explicit Save, with typed annotation identity.
    annotation_url=base+f'/api/lectures/{rev["document_id"]}/revisions/{rev["revision_id"]}/pages/1/annotation'
    annotation=page.request.get(annotation_url).json()
    def select_annotation():
     node=page.locator('.content .page-annotation .note-body')
     assert node.is_visible()
     assert 'Generated understanding' in node.inner_text()
     node.evaluate("el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}))}")
     assert 'Generated understanding' in page.evaluate('getSelection()?.toString()')
     page.locator('.content').get_by_role('toolbar',name='附注选文制卡',exact=True).wait_for()
    select_annotation();page.locator('.content').get_by_text('选文制问答',exact=True).click()
    page.locator('.lecture-inline-qa .cm-content').first.wait_for();assert 'Generated understanding' in page.locator('.lecture-inline-qa .cm-content').first.inner_text()
    page.locator('.lecture-inline-qa .cm-content').nth(1).fill('Explicit annotation answer')
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as added:
     page.locator('md-filled-button').filter(has_text='保存问答卡').click()
    assert added.value.ok,added.value.text();qa_note=added.value.json()['noteId']
    source=page.request.get(base+'/api/reading/source?note_id='+str(qa_note)).json()
    assert source['kind']=='pdf' and source['annotation_id']==annotation['annotation_id']
    page.locator('.content .page-annotation .note-body').wait_for();select_annotation()
    page.locator('.content').get_by_text('选文制文字挖空',exact=True).click();page.locator('md-dialog .cm-content').first.wait_for()
    assert '{{c1::' in page.locator('md-dialog .cm-content').first.inner_text()
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as added:
     page.locator('md-dialog md-filled-button').filter(has_text='添加').click()
    assert added.value.ok,added.value.text();cloze_note=added.value.json()['noteId']
    source=page.request.get(base+'/api/reading/source?note_id='+str(cloze_note)).json()
    assert source['kind']=='pdf' and source['annotation_id']==annotation['annotation_id']
    page.locator('.content').get_by_role('button',name='添加问答',exact=True).click();page.locator('.lecture-inline-qa .cm-content').first.wait_for();assert page.locator('.lecture-inline-qa .authoring-preview').count()==2
    page.locator('.lecture-crop-controls summary').click()
    with page.expect_response(lambda response:'/crop' in response.url and response.request.method=='POST') as cropped:
     page.locator('.lecture-crop-controls').get_by_role('button',name='插入到正面',exact=True).click()
    assert cropped.value.ok,cropped.value.text()
    image=page.locator('.lecture-inline-qa .authoring-preview img').first;image.wait_for();png_resource(image,prefix='/media/')
    matrix('QA',['question/answer remain inside main panel','two reserved previews','real crop screenshot loaded from managed media'])
    page.locator('.lecture-inline-qa md-text-button').filter(has_text='返回课件').click()
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();raster_ready()
    page.get_by_role('button',name='矩形遮罩',exact=True).click();box=page.locator('.occlusion-canvas').bounding_box();page.mouse.move(box['x']+box['width']*.15,box['y']+box['height']*.22);page.mouse.down();page.mouse.move(box['x']+box['width']*.45,box['y']+box['height']*.4);page.mouse.up();assert page.locator('.occlusion-mask').count()==1
    page.keyboard.press('Control+z');assert page.locator('.occlusion-mask').count()==0
    page.keyboard.press('Control+Shift+z');assert page.locator('.occlusion-mask').count()==1
    state=page.request.get(base+'/api/flow/state').json();assert state['flow']['card_answered']==0 and state['flow']['reading_consumed']==0
    matrix('occlusion-edit',['backend immutable raster','rectangle pointer gesture','stable mask identity'])
    page.locator('md-text-button').filter(has_text='预览所选卡片').click();page.locator('.lecture-qa-preview img').first.wait_for()
    for image in page.locator('.lecture-qa-preview img').all():
     image.evaluate('async image=>{await image.decode();if(!image.naturalWidth)throw Error("preview decode failed")}')
     data=image.get_attribute('src');assert data.startswith('data:image/png;base64,')
     import base64
     assert base64.b64decode(data.split(',',1)[1],validate=True).startswith(b'\x89PNG\r\n\x1a\n')
    capture('occlusion-preview',1400,'light',['actual backend generated opaque Q/A previews'])
    page.locator('md-filled-button').filter(has_text='保存到 Anki').click();page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for();capture('occlusion-published',1400,'light',['actual native owner publication','per-mask completion result'])
    page.locator('md-text-button').filter(has_text='保存草稿并返回').click();page.locator('.content .pdf-page canvas').wait_for(state='visible')
    # Reopen the published authoring source, crop without excluding masks,
    # update in place and verify exact note identities survive.
    local_key=f'ir4anki.authoring.page.{rev["document_id"]}.{rev["revision_id"]}.1'
    author_id=page.evaluate('(key)=>localStorage.getItem(key)',local_key)
    first_links=page.request.get(base+'/api/lectures/authoring/'+author_id).json()['note_links']
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();raster_ready();page.locator('.occlusion-mask').wait_for()
    assert page.locator('.occlusion-mask').count()==1
    page.get_by_role('button',name='裁剪',exact=True).click();box=page.locator('.occlusion-canvas').bounding_box()
    page.mouse.move(box['x']+box['width']*.04,box['y']+box['height']*.04);page.mouse.down();page.mouse.move(box['x']+box['width']*.96,box['y']+box['height']*.96);page.mouse.up()
    capture('occlusion-reedit-crop',1400,'light',['published masks rehydrated','crop keeps full mask','stable identities'])
    page.locator('md-filled-button').filter(has_text='保存到 Anki').click();page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
    second_links=page.request.get(base+'/api/lectures/authoring/'+author_id).json()['note_links']
    assert [x['note_id'] for x in first_links]==[x['note_id'] for x in second_links]
    page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
    # Crop-first uses a distinct generated page, followed by a rectangle.
    page.locator('.content').get_by_role('button',name='下一页',exact=True).click();assert_page(2)
    page.locator('.content').get_by_label('页码',exact=True).wait_for();assert page.locator('.content').get_by_label('页码',exact=True).input_value()=='2'
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click();raster_ready()
    page.get_by_role('button',name='裁剪',exact=True).click();box=page.locator('.occlusion-canvas').bounding_box()
    page.mouse.move(box['x']+box['width']*.1,box['y']+box['height']*.1);page.mouse.down();page.mouse.move(box['x']+box['width']*.9,box['y']+box['height']*.8);page.mouse.up()
    page.get_by_role('button',name='矩形遮罩',exact=True).click();box=page.locator('.occlusion-canvas').bounding_box()
    page.mouse.move(box['x']+box['width']*.2,box['y']+box['height']*.2);page.mouse.down();page.mouse.move(box['x']+box['width']*.6,box['y']+box['height']*.4);page.mouse.up()
    capture('crop-first',1400,'light',['crop-first pointer sequence; persisted geometry checked after publication'])
    page.locator('md-filled-button').filter(has_text='保存到 Anki').click();page.locator('.occlusion-editor [role="status"]').filter(has_text='已发布').wait_for()
    page.locator('md-text-button').filter(has_text='保存草稿并返回').click()
    page.locator('md-filled-button').filter(has_text='制卡完成').click();page.locator('.flashcard-wrapper.stage-review').wait_for();assert page.locator('.note-column .pdf-page canvas').count()==0;matrix('review-front',['source and annotations reveal-gated'])
    page.locator('md-filled-button,md-filled-tonal-button').filter(has_text='显示答案').click();page.locator('.note-column .pdf-page canvas').wait_for();matrix('review-back',['typed PDF source rehydrated after reveal'])
    assert_page(3,'.note-column')
    assert page.locator('.note-column input[aria-label="页码"]').input_value()=='3'
    assert page.locator('.note-column .page-annotation').get_attribute('aria-label')=='第 3 页理解笔记'
    page.get_by_role('button',name='溯源：回到制卡时的原文片段',exact=True).click()
    assert_page(3)
    assert page.locator('.content input[aria-label="页码"]').input_value()=='3'
    assert page.locator('.content .page-annotation').get_attribute('aria-label')=='第 3 页理解笔记'
    page.get_by_role('button',name='回到复习',exact=True).click()
    page.locator('.flashcard-wrapper.stage-review').wait_for()
    assert_page(3,'.note-column')
    assert page.locator('.note-column input[aria-label="页码"]').input_value()=='3' 
    source=page.request.get(base+'/api/reading/source?note_id='+str(seed_note['note_id'])).json()
    assert source['kind']=='pdf' and source['document_id']==rev['document_id'] and source['revision_id']==rev['revision_id']
    go_page(2,'.note-column')
    page.locator('.note-column md-primary-tab').filter(has_text='相关卡片').click();page.locator('.note-column .related-item').first.wait_for()
    assert page.locator('.note-column .related-item').count()>=3
    page.locator('.note-column md-primary-tab').filter(has_text='笔记').click();assert_page(2,'.note-column')
    # A fresh real document verifies split preview, slot replacement and tail.
    assert page.request.post(base+'/api/flow/exit').ok
    response=page.request.post(base+'/api/lectures/import',multipart={'path':'split-fixture.pdf','file':{'name':'split-fixture.pdf','mimeType':'application/pdf','buffer':pdf}});assert response.ok,response.text()
    split_rev=response.json();response=page.request.post(base+f'/api/lectures/{split_rev["document_id"]}/revisions/{split_rev["revision_id"]}/read');assert response.ok
    assert page.request.post(base+'/api/flow/start').ok
    page.reload();page.locator('.content .pdf-page canvas').wait_for()
    before=page.request.get(base+'/api/flow/state').json();parent=before['reading_round']['chunks'][0]
    page.locator('.content').get_by_text('按页书签分割',exact=True).click()
    page.get_by_label('分割开始页',exact=True).fill('2');page.get_by_label('分割结束页',exact=True).fill('3')
    page.wait_for_function("Array.from(document.querySelectorAll('.lecture-split-previews img')).every(i=>i.naturalWidth>10)")
    for image in page.locator('.lecture-split-previews img').all():png_resource(image)
    capture('split-preview',1400,'light',['actual first/last thumbnail preview'])
    page.get_by_text('确认书签分割',exact=True).click()
    page.wait_for_function("document.querySelector('.content input[aria-label=页码]')?.value==='2'")
    after=page.request.get(base+'/api/flow/state').json();child=after['reading_round']['chunks'][0]
    assert (child['page_start'],child['page_end'])==(2,3)
    assert_page(2)
    assert page.locator('.content input[aria-label="页码"]').input_value()=='2'
    assert page.locator('.content .page-annotation').get_attribute('aria-label')=='第 2 页理解笔记'
    page.locator('.content').get_by_role('button',name='图片挖空',exact=True).click()
    raster_ready()
    key=f'ir4anki.authoring.page.{split_rev["document_id"]}.{split_rev["revision_id"]}.2'
    aid=page.evaluate('(k)=>localStorage.getItem(k)',key)
    actual=page.request.get(base+'/api/lectures/authoring/'+aid).json()['source']
    assert actual['page']==2 and actual['segment_id']==child['segment_id']
    page.locator('md-text-button').filter(has_text='保存草稿并返回').click() and child['parent_segment_id']==parent['segment_id']
    assert len(after['reading_round']['chunks'])==1 and after['flow']['reading_slots']==before['flow']['reading_slots'] and after['flow']['reading_consumed']==0
    # An actual second client writes a newer annotation. The first editor must
    # report conflict and keep its unacknowledged draft; no routed fake response.
    page.locator('.content').get_by_text('添加理解笔记',exact=False).click();page.locator('.content .page-annotation .cm-content').wait_for()
    endpoint=base+f'/api/lectures/{split_rev["document_id"]}/revisions/{split_rev["revision_id"]}/pages/2/annotation'
    current=page.request.get(endpoint).json();response=page.request.put(endpoint,data={'body':'second client text','expected_version':current['version'],'request_id':'ui-real-second-client'});assert response.ok,response.text()
    page.locator('.content .page-annotation .cm-content').fill('unacknowledged first client text')
    page.locator('.content .page-annotation [role=alert]').wait_for();assert '冲突' in page.locator('.content .page-annotation [role=alert]').inner_text()
    draft_key=f'ir4anki.annotation.{split_rev["document_id"]}.{split_rev["revision_id"]}.2'
    assert json.loads(page.evaluate('(k)=>localStorage.getItem(k)',draft_key))['body']=='unacknowledged first client text'
    assert page.request.get(endpoint).json()['body']=='second client text'
    capture('annotation-conflict',1400,'light',['actual second-client conflict; server bytes and local draft checked'])
    # Sequential same-revision reading segment, after the split current
    # segment is completed. No card grading is needed to make this transition.
    response=page.request.post(base+'/api/reading/act',params={'path':child['path'],'chunk_key':child['chunk_key'],'action':'complete'});assert response.ok,response.text()
    assert page.request.post(base+'/api/flow/exit').ok
    assert page.request.post(base+'/api/flow/start').ok
    page.reload();page.locator('.content .pdf-page canvas').wait_for()
    next_state=page.request.get(base+'/api/flow/state').json();tail=next_state['reading_round']['chunks'][0]
    assert tail['revision_id']==child['revision_id'] and tail['segment_id']!=child['segment_id'] and tail['page_start']==4
    assert_page(4)
    assert page.locator('.content input[aria-label="页码"]').input_value()=='4'
    assert page.locator('.content .page-annotation').get_attribute('aria-label')=='第 4 页理解笔记'
    page.locator('.content').get_by_role('button',name='添加问答',exact=True).click()
    # QA locator is checked from an actual explicit card addition, not inferred
    # from a storage-key name or a page label.
    page.locator('.lecture-inline-qa .cm-content').nth(0).fill('Tail page four question')
    page.locator('.lecture-inline-qa .cm-content').nth(1).fill('Tail page four answer')
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as added:
     page.locator('md-filled-button').filter(has_text='保存问答卡').click()
    assert added.value.ok,added.value.text()
    tail_source=page.request.get(base+'/api/reading/source?note_id='+str(added.value.json()['noteId'])).json()
    assert tail_source['page']==4 and tail_source['segment_id']==tail['segment_id']
    def file_menu_action(path,label):
     target=page.get_by_role('treeitem',name=path,exact=True)
     assert target.count()==1 and target.get_attribute('data-path')==path
     assert target.locator('.files-tree-file__name').inner_text()==Path(path).name
     target.get_by_role('button',name='更多操作：'+path,exact=True).click()
     menu=target.locator('md-menu[open]')
     menu.locator('.menu[popover]').wait_for(state='visible')
     opened=page.locator('.files-tree md-menu[open]')
     assert opened.count()==1 and opened.evaluate_all("menus=>menus.map(m=>m.closest('[role=treeitem]').dataset.path)")==[path]
     action=menu.locator('md-menu-item').get_by_text(label,exact=True)
     assert action.count()==1
     action.click()
    # Real FilesScreen operations, without reload/remount of the files screen.
    page.locator('.nav-rail__item[title="文件"]').click()
    page.locator('.lecture-document-row').filter(has_text='split-fixture.pdf').click()
    go_page(3,'.lecture-files')
    version=page.get_by_label('课件版本',exact=True).input_value()
    row=page.locator('.files-tree-file[data-path="split-fixture.pdf"]');row.focus();row.press('F2')
    page.locator('.files-rename-input input').fill('renamed-ui.pdf');page.locator('md-dialog md-filled-button').filter(has_text='重命名').click()
    page.locator('.lecture-files strong').filter(has_text='renamed-ui.pdf').wait_for()
    assert page.locator('.lecture-files input[aria-label="页码"]').input_value()=='3'
    assert page.get_by_label('课件版本',exact=True).input_value()==version
    page.get_by_text('撤销上次文件迁移',exact=True).click()
    page.locator('.lecture-files strong').filter(has_text='split-fixture.pdf').wait_for()
    assert page.locator('.lecture-files input[aria-label="页码"]').input_value()=='3'
    file_menu_action('split-fixture.pdf','移动到…')
    page.locator('.files-move-dir').filter(has_text='relocated').click()
    with page.expect_response(lambda r:urlsplit(r.url).path=='/api/files/move' and r.request.method=='POST') as moved:
     page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
    assert moved.value.ok,moved.value.text()
    destination=page.get_by_role('treeitem',name='relocated',exact=True)
    assert destination.count()==1 and destination.get_attribute('data-path')=='relocated'
    if destination.get_attribute('aria-expanded')!='true':
     destination.focus();destination.press('ArrowRight')
    assert destination.get_attribute('aria-expanded')=='true'
    page.locator('.files-tree-file[data-path="relocated/split-fixture.pdf"]').wait_for()
    assert page.locator('.lecture-files input[aria-label="页码"]').input_value()=='3'
    current=page.request.get(base+'/api/lectures').json()['documents']
    assert next(d for d in current if d['document_id']==split_rev['document_id'])['path']=='relocated/split-fixture.pdf'
    file_menu_action('relocated/split-fixture.pdf','永久删除')
    page.locator('.files-delete-input input').fill('split-fixture.pdf');page.locator('md-dialog md-filled-button').filter(has_text='永久删除').click()
    page.wait_for_function("!document.querySelector('.files-tree-file[data-path=\"relocated/split-fixture.pdf\"]')")
    page.wait_for_function("![...document.querySelectorAll('.lecture-document-row')].some(e=>e.textContent.includes('split-fixture.pdf'))")
    assert not any(d['document_id']==split_rev['document_id'] for d in page.request.get(base+'/api/lectures').json()['documents'])
    capture('files-relocated-deleted',1400,'light',['real rename/undo/move/delete; preview page/version and server stable document identity asserted'])
    browser.close()
   except BaseException:
    diagnostics={}
    try:
     page.screenshot(path=str(out/'failure.png'),full_page=True)
     (out/'failure.html').write_text(page.content())
     diagnostics=page.evaluate("""()=>({authoring:[...document.querySelectorAll('.occlusion-editor')].map(e=>({id:e.dataset.authoringId,page:e.dataset.page,operation:e.dataset.operation,writer:e.dataset.writerState,rasterReady:e.dataset.rasterReady,errors:[...e.querySelectorAll('[role=alert]')].map(n=>n.textContent)})),canvases:[...document.querySelectorAll('.pdf-page canvas')].map(e=>({label:e.getAttribute('aria-label'),width:e.width,height:e.height})),toolbars:[...document.querySelectorAll('[role=toolbar]')].map(e=>({label:e.getAttribute('aria-label'),html:e.outerHTML})),controls:[...document.querySelectorAll('md-icon-button,md-text-button,input,summary')].map(e=>({tag:e.tagName,label:e.getAttribute('aria-label'),dataLabel:e.getAttribute('data-aria-label'),text:e.textContent,value:e.value,disabled:e.disabled,shadow:e.shadowRoot?.innerHTML})),selection:getSelection()?.toString(),anchor:getSelection()?.anchorNode?.parentElement?.outerHTML,annotations:[...document.querySelectorAll('.page-annotation')].map(e=>({label:e.getAttribute('aria-label'),text:e.innerText,visible:!!e.getClientRects().length})),pages:[...document.querySelectorAll('input[aria-label=页码]')].map(e=>({value:e.value,min:e.min,max:e.max})),text:document.body.innerText})""")
     diagnostics['network']=network
     diagnostics['page_error_details']=error_details
     diagnostics['fixture_source']={'document_id':rev['document_id'],'revision_id':rev['revision_id']}
     diagnostics['flow']=page.request.get(base+'/api/flow/state').json()
     (out/'failure-dom.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2))
    except Exception as diagnostic_error:diagnostics={'capture_error':repr(diagnostic_error)}
    raise
 except BaseException as exc:
  diagnostics=json.loads((out/'failure-dom.json').read_text()) if (out/'failure-dom.json').exists() else {'capture_error':'browser launch or diagnostic capture failed; see traceback'}
  ledger.append(dict(stage='failure',error=repr(exc),traceback=traceback.format_exc(),diagnostics=diagnostics));write();raise
 finally:
  if proc:proc.terminate();proc.wait(timeout=15)
  log.close();cleanup()
if __name__=='__main__':main()
