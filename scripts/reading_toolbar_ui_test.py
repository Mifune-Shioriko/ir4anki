"""Offline ReadingCard regression. All browser requests are fulfilled locally.
Builds only under --output; no backend, credentials, or study data used.
"""
import argparse, json, mimetypes, subprocess
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--source-only', action='store_true')
a = p.parse_args(); out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
card=(ROOT/'frontend/src/components/ReadingCard.tsx').read_text()
app=(ROOT/'frontend/src/App.tsx').read_text()
css=(ROOT/'frontend/src/index.css').read_text()
start=card.index('class="card-header-actions"')
end=card.index('class="reading-crumb',start)
header=card[start:end]
labels=['撤销上次评分','退出本轮','添加卡片','添加挖空','编辑片段']
assert [header.index('aria-label="'+label+'"') for label in labels]==sorted(header.index('aria-label="'+label+'"') for label in labels)
for label in labels[:2]: assert 'title="'+label+'"' in header
assert 'disabled={!props.undoEnabled || props.undoBusy || props.busy}' in header
assert 'disabled={props.exitDisabled}' in header
assert 'onClick={() => props.onUndo?.()}' in header
assert 'onClick={() => props.onExitRound?.()}' in header
assert header.count('onKeyDown={roundControlKeyDown}')==2
assert "if (event.key === ' ' || event.key === 'Enter') event.stopPropagation()" in card
assert 'onUndo={undo}' in app and 'onExitRound={exitFlow}' in app
assert 'exitDisabled={answering() || rdBusy() || undoBusy()}' in app
assert "<Show when={!traceOpen() && (phase() === 'review' || (phase() === 'start' && canUndo()))}>" in app
assert 'width: 48px; height: 48px;' in css[css.index('.reading-round-toolbar md-icon-button'):]
metadata=card[card.index('<div class="card-header"'):start]
assert '<Show when={trace()}>' in metadata, 'Ordinary reading must not render a metadata row'
assert '本文件进度' not in card, 'Ordinary reading file progress chip must be removed'
assert '.stage-reading .card-header-actions { margin-left: auto; }' in css, 'Reading toolbar must align at the card inner right edge'
print('PASS: source toolbar order, labels, states, exact handler wiring, bottom gating and 48px targets',flush=True)
if a.source_only: raise SystemExit(0)
from playwright.sync_api import sync_playwright, expect

fixture = out / 'fixture.tsx'
fixture.write_text('''import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import '__SOURCE__/md-components';
import '__SOURCE__/index.css';
import {ReadingCard} from '__SOURCE__/components/ReadingCard';
const [canUndo,setCanUndo]=createSignal(true),[busy,setBusy]=createSignal(false),[undoBusy,setUndoBusy]=createSignal(false),[answering,setAnswering]=createSignal(false);
window.calls=[];
window.fixtureState=()=>({canUndo:canUndo(),busy:busy(),undoBusy:undoBusy(),answering:answering()});
window.addEventListener('keydown',e=>{if(e.key===' ' && ['撤销上次评分','退出本轮'].includes(e.target.ariaLabel)){e.preventDefault();window.calls.push('UNEXPECTED completion')}});
const run=name=>()=>window.calls.push(name);
render(()=><><ReadingCard chunk={{chunk_key:'mock',path:'fixture.md',text:'Disposable reading text',heading_path:[],status:'todo',file_chunks:1,file_done:0,file_skipped:0,line_start:1,line_end:1,seg_id:1}}
 busy={busy()} onComplete={run('complete')} onNext={run('next')} onSkip={run('skip')} onAdd={run('add')} onCloze={run('cloze')} onSplit={run('split')} onEdit={run('edit')} onExit={run('finish')}
 onUndo={run('undo')} undoEnabled={canUndo()} undoBusy={undoBusy()} exitDisabled={answering() || busy() || undoBusy()} onExitRound={run('exit')}/>
<button onClick={()=>setCanUndo(!canUndo())}>toggle undo</button><button onClick={()=>setBusy(!busy())}>toggle busy</button><button onClick={()=>setUndoBusy(!undoBusy())}>toggle undo busy</button><button onClick={()=>setAnswering(!answering())}>toggle answering</button></>,document.getElementById('app'));
'''.replace('__SOURCE__', (ROOT/'frontend/src').as_posix()))
html = out/'index.html'
html.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><button id="keyboard-start" type="button">keyboard start</button><div id="app"></div><script type="module" src="./fixture.tsx"></script></body></html>')
dist = out/'fixture-dist'
# Exact file aliases avoid directory package.json's server `module` entry.
# The fixture lives outside frontend, so bare-import deduplication alone is insufficient.
script = r"""import {build} from 'vite';import solid from 'vite-plugin-solid';
import {writeFileSync} from 'node:fs';
const pkg=process.cwd()+'/node_modules/solid-js';
const verify={name:'verify-solid-browser',generateBundle(_,bundle){
 const ids=[...new Set(Object.values(bundle).flatMap(b=>Object.keys(b.modules||{})))];
 const solidIds=ids.filter(id=>id.includes('/solid-js/'));
 writeFileSync(process.argv[1]+'/build-resolution.json',JSON.stringify(solidIds,null,2));
 if(solidIds.some(id=>/\/dist\/server\.[cm]?js$/.test(id))) throw new Error('Solid server runtime in browser fixture');
 if(!solidIds.some(id=>id.endsWith('/web/dist/web.js'))) throw new Error('Solid browser render runtime missing');
}};
await build({configFile:false,root:process.argv[1],plugins:[solid(),verify],resolve:{
 conditions:['browser','module','production'],dedupe:['solid-js'],alias:[
 {find:/^solid-js$/,replacement:pkg+'/dist/solid.js'},
 {find:/^solid-js\/web$/,replacement:pkg+'/web/dist/web.js'},
 {find:/^solid-js\/store$/,replacement:pkg+'/store/dist/store.js'}
 ]},build:{outDir:process.argv[2],emptyOutDir:true}});"""
subprocess.run(['node','--input-type=module','-e',script,str(out),str(dist)],cwd=ROOT/'frontend',check=True)
records=[]
def save():
 (out/'cases.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
def stage(case, name):
 case['stage']=name;case['stages'].append(name);save()
save()
with sync_playwright() as pw:
 try:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
 except Exception as error:
  (out/'launch-error.json').write_text(json.dumps({'stage':'browser-launch','error':str(error)},indent=2))
  raise
 for width in [1440,390]:
  case={'width':width,'status':'RUNNING','stages':[],'console':[],'pageerrors':[],'requests':[]}
  records.append(case);stage(case,'create-page')
  page=browser.new_page(viewport={'width':width,'height':900})
  def console(message):
   case['console'].append({'type':message.type,'text':message.text,'location':message.location});save()
  def pageerror(error):
   case['pageerrors'].append(str(error));save()
  page.on('console',console);page.on('pageerror',pageerror)
  def route(r):
   path=urlparse(r.request.url).path
   case['requests'].append({'path':path,'method':r.request.method});save()
   f=dist/path.lstrip('/')
   if path=='/': f=dist/'index.html'
   assert f.is_file(), f'Unmocked request: {path}'
   r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
  try:
   page.route('**/*',route);stage(case,'load-fixture');page.goto('http://reading.fixture/')
   assert page.evaluate('document.compatMode')=='CSS1Compat'
   neutral=page.get_by_role('button',name='keyboard start',exact=True)
   def state(name):
    snapshot=page.evaluate('''() => {
     const active=[];let el=document.activeElement;
     while(el){active.push({tag:el.tagName,id:el.id,label:el.getAttribute('aria-label')||el.ariaLabel||el.textContent,disabled:el.disabled});el=el.shadowRoot?.activeElement;}
     return {active,fixture:window.fixtureState(),calls:[...window.calls],controls:Array.from(document.querySelectorAll('.reading-round-toolbar md-icon-button')).map(el=>({label:el.ariaLabel,disabled:el.disabled,nativeDisabled:el.shadowRoot.querySelector('button').disabled}))};
    }''')
    snapshot['name']=name;case.setdefault('states',[]).append(snapshot);save();return snapshot
   def disabled_inert(button, key, name):
    # A disabled native button cannot take focus. Park it on a non-mutating
    # control, otherwise Playwright's press() hits the last clicked toggle.
    neutral.focus();expect(neutral).to_be_focused();expect(button).to_be_disabled()
    before=state(name+' before')
    button.press(key)
    expect(neutral).to_be_focused();expect(button).to_be_disabled()
    button.evaluate('el=>el.click()')  # Native disabled button click suppression.
    after=state(name+' after')
    assert after['fixture']==before['fixture'],after
    assert after['calls']==before['calls'],after
    assert after['controls']==before['controls'],after
   toolbar=page.locator('.reading-round-toolbar')
   stage(case,'toolbar-visible');expect(toolbar).to_be_visible()
   labels=['撤销上次评分','退出本轮','添加卡片','添加挖空','编辑片段']
   stage(case,'ordinary-metadata-absent')
   expect(page.locator('.stage-reading .card-chips')).to_have_count(0)
   expect(page.locator('.stage-reading .reading-status-chip')).to_have_count(0)
   expect(page.get_by_text('本文件进度',exact=False)).to_have_count(0)
   stage(case,'card-inner-right-alignment')
   geometry=toolbar.evaluate('''el => {
    const header=el.closest('.card-header'),inner=el.closest('.card-inner');
    const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}};
    const crumb=inner.querySelector('.reading-crumb'),innerStyle=getComputedStyle(inner);
    const expectedGap=parseFloat(getComputedStyle(header).marginBottom)+parseFloat(getComputedStyle(crumb).marginTop)+(parseFloat(innerStyle.rowGap)||0);
    return {toolbar:rect(el),header:rect(header),inner:rect(inner),paddingRight:parseFloat(innerStyle.paddingRight),crumb:rect(crumb),expectedGap,crumbFollowsHeader:header.nextElementSibling===crumb};
   }''')
   case['geometry']=geometry;save()
   assert abs(geometry['toolbar']['right']-geometry['header']['right'])<=1,geometry
   assert abs(geometry['toolbar']['right']-(geometry['inner']['right']-geometry['paddingRight']))<=1,geometry
   assert abs(geometry['header']['height']-geometry['toolbar']['height'])<=1,geometry
   assert geometry['crumbFollowsHeader'],geometry
   assert abs(geometry['crumb']['top']-geometry['header']['bottom']-geometry['expectedGap'])<=1,geometry
   stage(case,'order-and-labels')
   expect(toolbar.get_by_role('button')).to_have_count(5)
   assert toolbar.locator('md-icon-button').evaluate_all('els=>els.map(e=>e.ariaLabel)')==labels
   boxes=[];case['targets']=boxes
   for label in labels:
    stage(case,'target-and-focus: '+label)
    host=toolbar.locator('md-icon-button').filter(has=page.get_by_role('button',name=label,exact=True))
    button=toolbar.get_by_role('button',name=label,exact=True)
    expect(button).to_have_accessible_name(label)
    if label in labels[:2]: assert host.get_attribute('title')==label
    box=button.bounding_box();boxes.append(box);save()
    assert box['width']>=48 and box['height']>=48, box
    assert box['x']>=0 and box['x']+box['width']<=width,box
    button.focus();expect(button).to_be_focused()
   assert all(boxes[i]['x']<boxes[i+1]['x'] for i in range(4))
   assert page.locator('.flow-actions').count()==0
   stage(case,'tab-traversal')
   neutral.focus();expect(neutral).to_be_focused()
   for label in labels:
    page.keyboard.press('Tab')
    expect(toolbar.get_by_role('button',name=label,exact=True)).to_be_focused()
    state('Tab: '+label)
   for label,call,key in [('撤销上次评分','undo','Enter'),('退出本轮','exit','Space'),('添加卡片','add','Enter'),('添加挖空','cloze','Space'),('编辑片段','edit','Enter')]:
    stage(case,'keyboard: '+label)
    button=toolbar.get_by_role('button',name=label,exact=True);button.focus();button.press(key)
    page.wait_for_function('n=>window.calls.length===n',arg=labels.index(label)+1)
   assert page.evaluate('window.calls')==['undo','exit','add','cloze','edit']
   stage(case,'undo-unavailable')
   undo=toolbar.get_by_role('button',name=labels[0],exact=True);exit=toolbar.get_by_role('button',name=labels[1],exact=True)
   page.get_by_role('button',name='toggle undo',exact=True).click();expect(undo).to_be_disabled();expect(exit).to_be_enabled()
   disabled_inert(undo,'Enter','undo unavailable')
   neutral.focus();page.keyboard.press('Tab');expect(exit).to_be_focused()
   state('Tab skips unavailable undo')
   page.get_by_role('button',name='toggle undo',exact=True).click()
   for toggle in ['toggle busy','toggle undo busy']:
    stage(case,toggle)
    page.get_by_role('button',name=toggle,exact=True).click();expect(undo).to_be_disabled();expect(exit).to_be_disabled()
    disabled_inert(undo,'Enter',toggle+' undo')
    disabled_inert(exit,'Space',toggle+' exit')
    expect(undo).to_be_disabled();expect(exit).to_be_disabled()
    assert page.evaluate('window.calls.length')==5
    page.get_by_role('button',name=toggle,exact=True).click();expect(undo).to_be_enabled();expect(exit).to_be_enabled()
   stage(case,'answering')
   page.get_by_role('button',name='toggle answering',exact=True).click();expect(exit).to_be_disabled()
   disabled_inert(exit,'Space','answering exit')
   assert not case['pageerrors'],case['pageerrors']
   page.screenshot(path=str(out/f'{width}.png'))
   case.update(labels=labels,status='PASS',calls=page.evaluate('window.calls'));stage(case,'complete')
  except Exception as error:
   case.update(status='FAIL',error=str(error));save()
   try:
    (out/f'{width}-failure.html').write_text(page.content())
    case['dom']=page.evaluate("""() => ({readyState:document.readyState,app:document.querySelector('#app')?.innerHTML,calls:window.calls,toolbarCount:document.querySelectorAll('.reading-round-toolbar').length})""")
    page.screenshot(path=str(out/f'{width}-failure.png'))
   except Exception as diagnostic_error:
    case['diagnostic_error']=str(diagnostic_error)
   save();raise
  finally:
   page.close()
 browser.close()
print('PASS: desktop/narrow toolbar order, labels, keyboard, targets, busy states and handler wiring')
