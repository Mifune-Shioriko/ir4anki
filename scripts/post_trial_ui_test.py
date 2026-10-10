"""Rendered templates, CM6 geometry and independent rings on disposable native data.
Requires REGRESSION_DIST and TMPDIR/NATIVE_FIXTURE_ROOT below a scratch directory.
Fails (never skips) if sockets/browser are unavailable.
"""
import hashlib, json, os, subprocess, sys
from pathlib import Path
from anki.collection import Collection
from playwright.sync_api import sync_playwright
from native_fixture import reserve_loopback_port, verify_server, cleanup
os.environ.setdefault('INTERLEAVE_RUN_DIR', os.environ['POST_TRIAL_RUN'])
from interleave_ui_test import prepare, ROOT
RUN=Path(os.environ['POST_TRIAL_RUN']); RUN.mkdir(parents=True,exist_ok=True)

def build_editor_probe():
 # Keep the public CM6 reader in a test-only bundle, outside served dist.
 # The imported public findFromDOM locates the actual app EditorView.
 script = f"""
 import {{ build }} from 'vite';
 await build({{
   configFile: false, cacheDir: {json.dumps(str(RUN/'vite-cache'))},
   build: {{
     lib: {{entry: {json.dumps(str(ROOT/'frontend/tests/cm6-browser-probe.mjs'))}, formats: ['es'], fileName: () => 'cm6-browser-probe.js'}},
     outDir: {json.dumps(str(RUN/'probe'))}, emptyOutDir: true, minify: false,
   }},
 }});
 """
 subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT/'frontend',check=True)
 return (RUN/'probe/cm6-browser-probe.js').read_text()


def main():
 reserve_loopback_port() # fail before collection setup if sockets are prohibited
 probe_source = build_editor_probe()
 env=prepare(2,2,'normal')
 col=Collection(env['ANKI_COLLECTION_PATH'])
 try:
  model=col.models.by_name('问答题')
  widgets='<section class="fsrs-stats"><div>稳定性 12</div><div>难度 5</div><div>保持率 90%</div></section><div id="anki-explain">AI explanation residual</div><div id="explain-section">💡 AI 讲解（仅供参考！）<div id="explain-content">legacy explanation</div></div><div id="sc">📎 相关卡片<div id="sl">legacy matches</div></div>'
  for t in model['tmpls']:
   t['qfmt']+=''+widgets
   t['afmt']+=''+widgets
  col.models.save(model)
  n=col.get_note(5000);n['正面']='<b>旧 HTML</b><span class="cloze">高亮</span><div class="fsrs-stats">稳定性 12</div>';col.update_note(n)
  n=col.get_note(5001);n['正面']='**Markdown QA**';n.tags=['ir4anki::markdown'];col.update_note(n)
  ids=[]
  for tagged in [False,True]:
   n=col.new_note(col.models.by_name('填空题'));n['文字']='**{{c3::挖空答案::提示}}** {{c7::另一序号}}';n['背面额外']='额外';n.tags=['ir4anki::markdown'] if tagged else []
   col.add_note(n,col.decks.id('2026'));ids.extend(c.id for c in n.cards())
  # Both storage formats use the SAME native cloze note type and unchanged ordinals.
  assert {col.get_card(cid).ord for cid in ids}=={2,6}
  assert len({col.get_card(cid).note().mid for cid in ids})==1
 finally: col.close()
 port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
 with (RUN/'server.log').open('w') as log:
  proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=env,stdout=log,stderr=subprocess.STDOUT)
  try:
   verify_server(proc,base)
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    probe_url = base+'/__post_trial_cm6_probe.js'
    page.route('**/__post_trial_cm6_probe.js',lambda route: route.fulfill(body=probe_source,content_type='text/javascript'))
    document_checks=[]
    def assert_document(index,expected,stage):
     # Wait for async editor construction, not for equality with expected text.
     # A wrong document fails immediately once the actual view exists.
     page.wait_for_function("""async ({index,url}) => {
       const {snapshot}=await import(url);
       const editor=document.querySelectorAll('.edit-dialog .cm-editor')[index];
       return !!editor && snapshot(editor)!==null;
     }""",arg={'index':index,'url':probe_url})
     actual=page.locator('.edit-dialog .cm-editor').nth(index).evaluate(
       'async (el,url)=>(await import(url)).snapshot(el)',probe_url)
     record={**{k:v for k,v in actual.items() if k!='document'},'stage':stage,
             'exact':actual['document']==expected,
             'expected_length':len(expected),'actual_length':len(actual['document']),
             'expected_sha256':hashlib.sha256(expected.encode()).hexdigest(),
             'actual_sha256':hashlib.sha256(actual['document'].encode()).hexdigest()}
     document_checks.append(record)
     (RUN/'document-checks.json').write_text(json.dumps(document_checks,indent=2))
     assert actual['document']==expected, f'{stage}: authoritative CM6 source differs; see {RUN/"document-checks.json"}'
     return actual
    def assert_saved_note(cid,expected_fields,stage):
     response=page.request.get(base+'/api/note',params={'card_id':cid});assert response.ok,response.text()
     saved=response.json()
     assert saved['fields']==expected_fields, f'{stage}: API fields differ'
     assert 'ir4anki::markdown' in saved['tags'], f'{stage}: Markdown storage tag lost'
     return saved
    def state(): return page.request.get(base+'/api/flow/state').json()
    def post(path,params=None):
     r=page.request.post(base+path,params=params);assert r.ok,r.text();return r.json()
    post('/api/flow/start');page.goto(base);page.locator('.nav-rail').wait_for()
    def rings():
     d=state();f=d['flow']
     for label,done,total in [('阅读',f['reading_consumed'],f['reading_slots']),('复习',f['card_handled'],f['card_count'])]:
      ring=page.get_by_role('progressbar',name=label,exact=True)
      assert ring.count()==1
      assert ring.get_attribute('aria-valuenow')==str(done)
      assert ring.get_attribute('aria-valuemax')==str(total)
     colors=page.locator('.progress-ring__arc').evaluate_all('els=>els.map(el=>getComputedStyle(el).stroke)');assert len(set(colors))==2
    rings();d=state();ch=d['reading_round']['chunks'][0]
    post('/api/reading/act',dict(path=ch['path'],chunk_key=ch['chunk_key'],action='next'));page.reload();page.locator('.card-question').wait_for();rings()
    text=page.locator('.flashcard-wrapper').inner_text()
    for retired in ['稳定性','难度','保持率','AI explanation residual','AI 讲解','legacy explanation','legacy matches','📎 相关卡片']: assert retired not in text
    assert page.locator('.card-question b').inner_text()=='旧 HTML'
    page.locator('.action-area md-filled-tonal-button',has_text='显示答案').click()
    for retired in ['稳定性','难度','保持率','AI explanation residual','AI 讲解','legacy explanation','legacy matches','📎 相关卡片']: assert retired not in page.locator('.flashcard-wrapper').inner_text()
    assert page.locator('.rating-interval').count()==0
    page.screenshot(path=str(RUN/'review-legacy.png'))
    page.reload();page.locator('.card-question').wait_for()
    page.locator('md-icon-button[data-aria-label="编辑卡片"]').click();page.locator('.rich-field').first.wait_for()
    assert page.locator('.edit-dialog .rich-field').count()==2
    assert page.locator('.rich-field .fsrs-stats').count()==1, 'presentation sanitization must not rewrite editor source'
    assert page.locator('.edit-dialog .card-md-preview').count()==2
    page.locator('.edit-dialog md-text-button',has_text='取消').last.click()
    # Add QA uses Markdown; verify left/right input and below previews in actual dialog/shadow layout.
    page.locator('md-icon-button[data-aria-label="添加卡片"]').first.click();page.locator('.cm-editor').first.wait_for()
    page.wait_for_function("document.querySelector('.cm-scroller') && getComputedStyle(document.querySelector('.cm-scroller')).display==='flex'")
    assert page.locator('.markdown-field .edit-error').count()==0
    def geometry():
     return page.locator('.edit-dialog .field-group').evaluate_all('els=>els.map(el=>{const b=x=>{const r=x.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom}};return {group:b(el),input:b(el.querySelector(".card-md-editor")),preview:b(el.querySelector(".card-md-preview")),scroller:getComputedStyle(el.querySelector(".cm-scroller")).display}})')
    g=geometry();assert g[0]['input']['x']<g[1]['input']['x'];assert abs(g[0]['input']['y']-g[1]['input']['y'])<2
    for col in g: assert col['preview']['y']>=col['input']['b'] and 120<=col['input']['h']<=182 and col['scroller']=='flex'
    source='**mixed** <br> $x_{2}$\n'+ '\n'.join('line '+str(i) for i in range(40))+'\nLAST LINE'
    assert_document(0,'','add question initialized');assert_document(1,'','add answer initialized')
    editors=page.locator('.edit-dialog .cm-content');editors.nth(0).fill(source);editors.nth(1).fill('**answer**')
    assert_document(0,source,'add question before save');assert_document(1,'**answer**','add answer before save')
    editors.nth(0).press('Control+End')
    assert page.locator('.edit-dialog .cm-line').filter(has_text='LAST LINE').evaluate('el=>{const a=el.getBoundingClientRect(),b=el.closest(".cm-scroller").getBoundingClientRect();return a.bottom<=b.bottom+1 && a.top>=b.top}')
    page.screenshot(path=str(RUN/'editor-desktop.png'))
    page.set_viewport_size({'width':390,'height':844});g=geometry();assert g[1]['group']['y']>g[0]['group']['y'];assert all(c['input']['w']<=390 for c in g)
    page.screenshot(path=str(RUN/'editor-narrow.png'));page.set_viewport_size({'width':1440,'height':1000})
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as response: page.locator('.edit-dialog md-filled-button').last.click()
    assert response.value.ok,response.value.text()
    assert response.value.request.post_data_json['fields']=={'正面':source,'背面':'**answer**'}
    added=response.value.json();assert_saved_note(added['cardIds'][0],{'正面':source,'背面':'**answer**'},'add/save/API')
    page.locator('.edit-dialog').wait_for(state='detached')
    # Native flow, refresh, undo, queue exhaustion and both cloze storage formats.
    seen=set()
    while state()['flow']['phase']!='complete':
     d=state()
     if d['flow']['phase']=='reading':
      ch=d['reading_round']['chunks'][0];post('/api/reading/act',dict(path=ch['path'],chunk_key=ch['chunk_key'],action='next'));page.reload();rings();continue
     page.locator('.card-question').wait_for();cid=d['cards'][0]['cardId'];note=page.request.get(base+'/api/note',params={'card_id':cid}).json()
     if note['kind']=='qa' and 'ir4anki::markdown' in note['tags']:
      page.locator('md-icon-button[data-aria-label="编辑卡片"]').click()
      assert_document(0,note['fields']['正面'],'edit initial question');assert_document(1,note['fields']['背面'],'edit initial answer')
      page.locator('.edit-dialog .cm-content').first.fill(source)
      assert_document(0,source,'edit question before save')
      expected_fields={**note['fields'],'正面':source}
      with page.expect_response(lambda r:'/api/note/update' in r.url and r.request.method=='POST') as updated:
       page.locator('.edit-dialog md-filled-button').last.click()
      assert updated.value.ok,updated.value.text()
      assert updated.value.request.post_data_json['fields']==expected_fields
      page.locator('.edit-dialog').wait_for(state='detached')
      assert_saved_note(cid,expected_fields,'edit/save/API before reopen')
      page.locator('md-icon-button[data-aria-label="编辑卡片"]').click()
      assert_document(0,source,'reopen exact question');assert_document(1,expected_fields['背面'],'reopen exact answer')
      page.locator('.edit-dialog .cm-content').first.press('Control+End')
      # DOM is appropriate for visible geometry, never for source equality.
      page.wait_for_function("""() => {
       const editor=document.querySelector('.edit-dialog .cm-editor');
       const el=editor && Array.from(editor.querySelectorAll('.cm-line')).find(el=>el.textContent==='LAST LINE');
       if (!el) return false;
       const a=el.getBoundingClientRect(),b=el.closest('.cm-scroller').getBoundingClientRect();
       return a.bottom<=b.bottom+1 && a.top>=b.top;
      }""")
      assert_document(0,source,'reopen after scroll to last line')
      page.screenshot(path=str(RUN/'editor-reopened.png'))
      page.locator('.edit-dialog md-text-button',has_text='取消').last.click()
      assert_saved_note(cid,expected_fields,'reopen/API')
     if note['kind']=='cloze':
      seen.add('ir4anki::markdown' in note['tags']);assert page.locator('.card-question .cloze').count()>=1
      assert page.locator('.card-question .cloze').first.evaluate('el=>getComputedStyle(el).backgroundColor')!='rgba(0, 0, 0, 0)'
     page.locator('.action-area md-filled-tonal-button',has_text='显示答案').click()
     assert page.locator('.rating-interval').count()==0
     if note['kind']=='cloze': assert page.locator('.card-answer .cloze').count()>=1
     page.locator('.ease-good').click();page.wait_for_function('!document.querySelector(".ease-good")');rings()
     post('/api/undo');page.reload();page.locator('.card-question').wait_for();rings();assert state()['cards'][0]['cardId']==cid
     post('/api/answer',dict(card_id=cid,ease=3));page.reload();rings()
    assert seen=={False,True};rings();page.reload();rings()
    page.emulate_media(color_scheme='dark');page.screenshot(path=str(RUN/'complete-dark.png'))
    assert not errors,errors
    (RUN/'geometry.json').write_text(json.dumps(g,indent=2));browser.close()
    print('PASS rendered template sanitization, native/Markdown cloze, mixed raw save, CM6 geometry/last line, two rings/undo/refresh/exhaustion')
  finally: proc.terminate();proc.wait(timeout=15)
if __name__=='__main__':
 try: main()
 finally: cleanup()
