"""Temporary real Collection/browser regression. No live API or sync credentials."""
import json, os, re, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from anki.collection import Collection
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]

def test():
 with tempfile.TemporaryDirectory(prefix='ir4anki-ui-') as tmp:
  tmp=Path(tmp); corpus=tmp/'notes'; corpus.mkdir(); (corpus/'topic.md').write_text('# Topic\n\nMarkdown source passage.\n')
  col=Collection(str(tmp/'collection.anki2')); deck=col.decks.id('2026')
  seed=col.new_note(col.models.by_name('Basic')); seed['Front']='**Seed question**'; seed['Back']='Seed answer'; seed.tags=['ir4anki::markdown']; col.add_note(seed,deck); seed_cid=seed.cards()[0].id; col.close()
  env=os.environ.copy()
  for k in list(env):
   if k.startswith('ANKI_SYNC_'): env.pop(k)
  env.update(ANKI_BACKEND='pylib', ANKI_COLLECTION_PATH=str(tmp/'collection.anki2'), ANKI_STATE_DIR=str(tmp/'state'), ANKI_NOTES_DIR=str(corpus), ANKI_BACKUP_DIR=str(tmp/'backups'), ANKI_ADD_MODEL='Basic', ANKI_ADD_CLOZE_MODEL='Cloze', ANKI_PREVIEW_MODE='1', ANKI_READING_MODE='1', REVIEW_DIST_DIR=os.environ.get('REGRESSION_DIST',str(ROOT/'frontend/dist')))
  with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
  base=f'http://127.0.0.1:{port}'
  log=open(tmp/'server.log','w')
  proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=env,stdout=log,stderr=log)
  def api(path,body=None):
   req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(req) as res: return json.load(res)
  try:
   for _ in range(100):
    try: api('/api/status'); break
    except Exception: time.sleep(.1)
   api('/api/session/start',{})
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000}); page.goto(base)
    page.locator('md-icon-button[data-aria-label="添加卡片"]').first.click()
    page.locator('.edit-dialog .cm-editor').first.wait_for(timeout=10000)
    source='  **Question** <br> ![](image.png) $x_{2}$\n\n- list\n'
    editors=page.locator('.edit-dialog .cm-content')
    editors.nth(0).fill(source); editors.nth(1).fill('**Answer**\n\n| A | B |\n|---|---|\n| 1 | 2 |')
    page.locator('.edit-dialog .tag-input').evaluate("el => { el.value='user-tag'; el.dispatchEvent(new InputEvent('input',{bubbles:true})); el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); }")
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as response:
     page.locator('.edit-dialog md-filled-button').last.click()
    result=response.value.json(); cid=result['cardIds'][0]
    note=api(f'/api/note?card_id={cid}')
    assert note['fields']['Front']==source, repr(note['fields']['Front'])
    assert set(['ir4anki::markdown','user-tag']).issubset(note['tags'])
    print('PASS CM6 QA raw Markdown exact save + user tags + marker')
    # Reopen a tagged note in the active review card: raw Markdown is
    # retrieved through the real note API, including mixed HTML source.
    page.locator('md-icon-button[data-aria-label="编辑卡片"]').click()
    page.locator('.edit-dialog .cm-content').first.wait_for()
    page.locator('.edit-dialog .cm-content').first.fill(source)
    page.locator('.edit-dialog md-filled-button').last.click()
    page.locator('.edit-dialog').wait_for(state='detached')
    page.locator('md-icon-button[data-aria-label="编辑卡片"]').click()
    page.locator('.edit-dialog .cm-content').first.wait_for()
    assert page.locator('.edit-dialog .cm-content').first.evaluate("el => Array.from(el.querySelectorAll('.cm-line')).map(line => line.textContent).join('\\n')")==source
    assert api('/api/note?card_id='+str(seed_cid))['fields']['Front']==source
    page.locator('.edit-dialog md-text-button',has_text='取消').last.click()
    print('PASS tagged edit/reopen uses CM6 and preserves raw Markdown')
    # Use the app sanitizer itself inside the browser, without importing a
    # test dependency or adding production globals.
    clean=(ROOT/'frontend/src/lib/clean.ts').read_text().replace('export function','function')
    clean=re.sub(r': string(?=[), {])','',clean)
    cleaned=page.evaluate("() => { "+clean+"; return cleanCardHtml('<b>legacy</b><img src=x onerror=alert(1)><a href=javascript:alert(1)>link</a><svg onload=alert(1)></svg><script>alert(1)</script>'); }")
    assert '<b>legacy</b>' in cleaned and 'onerror' not in cleaned and 'javascript:' not in cleaned and '<svg' not in cleaned and '<script' not in cleaned
    print('PASS legacy HTML sanitizer excludes active XSS')
    # Switch to a real reading round and create clozes from its editor.
    api('/api/session/finish',{}); api('/api/reading/list/add',{'path':'topic.md'}); api('/api/reading/start',{})
    page.reload(); page.locator('md-icon-button[data-aria-label="添加挖空"]').click()
    page.locator('.cloze-dialog .cm-content').first.wait_for()
    cloze='**{{c1::$x_{2}$::hint}}** [{{c2::other}}](https://example.org)\n\n- list\n'
    page.locator('.cloze-dialog .cm-content').first.fill(cloze)
    page.locator('.cloze-dialog .cm-content').nth(1).fill('**Extra** $y$')
    assert 'other' in page.locator('.cloze-preview-col').first.inner_text()
    assert 'hint' in page.locator('.cloze-preview-col').first.inner_text()
    with page.expect_response(lambda r:'/api/card/add' in r.url and r.request.method=='POST') as response:
     page.locator('.cloze-dialog md-filled-button',has_text='添加挖空卡').click()
    data=response.value.json(); note=api('/api/note?card_id='+str(data['cardIds'][0]))
    assert note['fields']['Text']==cloze and note['fields']['Back Extra']=='**Extra** $y$'
    assert len(data['cardIds'])==2 and 'ir4anki::markdown' in note['tags']
    print('PASS CM6 cloze preview ordinals, raw fields and native multi-card generation')
    # Contract-only fixtures for native endpoints being implemented by the
    # backend worker. The collection/state flows above remain real.
    sync_calls=[]
    def engine_fixture(route):
     path=route.request.url.split('/api/engine/')[1]
     data={'stats':{'cards':3,'reviews_today':0},'status':{'sync_deferred':True,'undo':{'available':True}},'backup':{'handle':'temporary-backup'},'export':{'download_url':'/api/engine/download/temporary'},'sync':{'synced':False,'deferred':False,'reason':'no credentials'}}.get(path,{})
     if path=='sync': sync_calls.append(route.request.post_data_json)
     route.fulfill(json=data)
    page.route('**/api/engine/**',engine_fixture)
    page.get_by_role('button',name='引擎管理',exact=True).click()
    page.locator('.engine-panel pre').first.wait_for()
    assert 'reviews_today' in page.locator('.engine-panel').inner_text()
    assert '同步已延后' in page.locator('.engine-panel').inner_text()
    page.locator('.engine-panel md-outlined-button',has_text='同步').click()
    assert sync_calls==[]
    page.locator('.sync-confirm md-text-button',has_text='取消').click()
    assert sync_calls==[]
    page.locator('.engine-panel md-outlined-button',has_text='同步').click()
    page.locator('.sync-confirm md-filled-button').click()
    page.locator('.sync-confirm').wait_for(state='detached')
    assert sync_calls==[{'commit_undo':True}]
    page.locator('.engine-panel md-filled-tonal-button',has_text='创建备份').click()
    page.locator('.engine-panel [role=status]').filter(has_text='temporary-backup').wait_for()
    page.locator('.engine-panel md-filled-tonal-button',has_text='导出集合').click()
    page.get_by_role('link',name='下载导出的集合').wait_for()
    assert page.get_by_role('link',name='下载导出的集合').get_attribute('href')=='/api/engine/download/temporary'
    print('PASS engine UI contract: actual fixture values, deferred state, confirm/cancel, backup/export download')
    page.route('**/api/status',lambda route: route.fulfill(json={'anki_backend':'connect'}))
    page.reload(); page.locator('.nav-rail').wait_for()
    assert page.get_by_role('button',name='引擎管理',exact=True).count()==0
    print('PASS connect hides native engine navigation')
    browser.close()
  finally:
   proc.terminate(); proc.wait(timeout=15); log.close()
if __name__=='__main__': test()
