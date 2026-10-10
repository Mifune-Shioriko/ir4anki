"""Offline editor geometry: route every request, never start/contact a backend.
Usage: python scripts/editor_stable_preview_ui_test.py --output SCRATCH_DIR
"""
import argparse, json, mimetypes, subprocess
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
def assert_legacy_format(evidence, *, bold, underline, command):
 # Assert every rendered character, source and selection, independent of the
 # browser's equivalent b/strong/u tags or styleWithCSS span representation.
 runs=[run for run in evidence['runs'] if run['text']]
 assert runs and ''.join(run['text'] for run in runs)=='selection', evidence
 assert evidence['text']=='selection' and evidence['selected']=='selection', evidence
 assert evidence['range'] and evidence['range']['inside'], evidence
 for run in runs:
  weight=float(run['fontWeight'])
  assert weight>=700 if bold else weight<600, evidence
  assert ('underline' in run['textDecoration'].split()) == underline, evidence
  if not bold and not underline:
   assert run['fontStyle']=='normal', evidence
 actual=evidence['commands'][-1]
 assert actual['name']==command and actual['result'] is True, evidence
 assert actual['before']=='selection' and actual['after']=='selection', evidence
 # Source must still be the exact HTML produced by the actual editing command,
 # rather than normalized/replaced by a reactive rerender or conversion.
 assert evidence['html']==actual['html'], evidence

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
p.add_argument('--legacy-diagnostic', action='store_true', help='Capture legacy command/selection evidence without replacing normal formatting assertions')
a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=True)
dist = a.output / 'fixture-dist'
script = """
import {build} from 'vite'; import solid from 'vite-plugin-solid';
await build({configFile:false,root:'tests',plugins:[solid()],build:{outDir:process.argv[1],emptyOutDir:true,rollupOptions:{input:'tests/editor-stable-preview.html'}}});
"""
subprocess.run(['node','--input-type=module','-e',script,str(dist)],cwd=ROOT/'frontend',check=True)
records=[]
def record_event(width, mode, status, **details):
 record={'width':width,'theme':theme,'mode':mode,'status':status,**details}
 print(json.dumps(record,ensure_ascii=False),flush=True)
 with (a.output/'cases.jsonl').open('a') as log:
  log.write(json.dumps(record,ensure_ascii=False)+'\n')
with sync_playwright() as pw:
 try:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
 except Exception as error:
  (a.output/'launch-failure.json').write_text(json.dumps({'stage':'chromium-launch','status':'BLOCKED','error':str(error)},indent=2))
  raise
 for width,theme in [(w,t) for w in [1440,390] for t in ['light','dark']]:
  for mode in (['legacy'] if a.legacy_diagnostic else ['add','markdown','legacy','cloze']):
   record_event(width,mode,'START')
   page=browser.new_page(viewport={'width':width,'height':1000},color_scheme=theme)
   def route(r):
    u=urlparse(r.request.url)
    if u.path.startswith('/api/'):
     assert r.request.method=='GET', 'Unexpected mutation'
     if u.path=='/api/tags': data={'tags':[]}
     elif u.path=='/api/note': data={'fields':{'正面':'','背面':''},'tags':['ir4anki::markdown'] if mode=='markdown' else []}
     elif u.path=='/api/card/add/info': data={'fields':['文字','背面额外'] if mode=='cloze' else ['正面','背面']}
     else: raise AssertionError(u.path)
     r.fulfill(json=data); return
    f=dist/u.path.lstrip('/')
    assert f.is_file(), f'Unmocked request: {u.path}'
    r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
   page.route('**/*',route)
   page.goto(f'http://editor.test/editor-stable-preview.html?mode={mode}')
   editor=page.locator('.rich-field' if mode=='legacy' else '.cm-content').first
   editor.wait_for(); page.wait_for_timeout(600)
   def geometry():
    return page.evaluate('''() => {
     const d=document.querySelector('md-dialog');
     const els=[d.shadowRoot.querySelector('dialog'),...document.querySelectorAll('.card-md-editor,.rich-field,.card-md-preview,.cloze-ordinals,.cloze-preview,.cloze-preview-box,.tags-section')];
     return els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})
    }''')
   def assert_geometry(stage):
    now=geometry()
    assert len(now)==len(baseline)
    # Use the same subpixel tolerance for short and multiline documents;
    # exact float equality is not a stronger layout guarantee.
    assert all(abs(r[k]-s[k])<1 for r,s in zip(baseline,now) for k in r), f'{width}/{mode}/{stage}: geometry moved: {baseline} -> {now}'
   def scroll_failure(locator, stage, error):
    diagnostic=page.evaluate('''() => {
     const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
     const inspect=el=>{const s=getComputedStyle(el);return {
      className:el.className,rect:rect(el),scrollHeight:el.scrollHeight,
      clientHeight:el.clientHeight,scrollTop:el.scrollTop,
      styles:Object.fromEntries(['height','minHeight','maxHeight','flex','flexBasis','flexShrink','overflowY','whiteSpace','scrollBehavior'].map(k=>[k,s[k]]))};};
     const dialog=document.querySelector('md-dialog');
     return {dialog:inspect(dialog.shadowRoot.querySelector('dialog')),
      hostClass:dialog.className,dialogScroller:inspect(dialog.shadowRoot.querySelector('.scroller')),
      elements:Array.from(document.querySelectorAll('.rich-field,.cm-scroller,.card-md-editor,.card-md-preview,.cloze-ordinals,.cloze-preview-box')).map(inspect),
      inputs:Array.from(document.querySelectorAll('.rich-field,.cm-content')).map(el=>({textContent:el.textContent,innerText:el.innerText,innerHTML:el.innerHTML})),
      selection:window.getSelection()?.toString()};
    }''')
    diagnostic.update(width=width,mode=mode,stage=stage,error=str(error),geometry=geometry(),baseline=baseline)
    path=a.output/f'{width}-{theme}-{mode}-{stage}-failure.json'
    path.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2))
    page.screenshot(path=str(path.with_suffix('.png')))
    record_event(width,mode,'FAIL',stage=stage,diagnostic=str(path))
   def wait_for_scroll(locator, stage, predicate):
    try:
     page.wait_for_function(predicate,arg=locator.element_handle(),timeout=5000)
    except Exception as error:
     scroll_failure(locator,stage,error)
     raise AssertionError(f'{width}/{mode}/{stage}: scroll assertion failed; see {a.output}') from error
   def wait_at_bottom(locator, stage):
    # Smooth scrolling is asynchronous. Poll actual overflow and bottom.
    wait_for_scroll(locator,stage,'el=>el.scrollHeight>el.clientHeight && el.scrollTop+el.clientHeight>=el.scrollHeight-1')
   last_line_visible='''el=>{
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
    let n; while(n=walker.nextNode()) {const i=n.textContent.indexOf('LAST LINE');if(i<0)continue;
     const range=document.createRange();range.setStart(n,i);range.setEnd(n,i+9);
     const line=range.getBoundingClientRect(), box=el.getBoundingClientRect();
     return line.height>0 && line.top>=box.top-1 && line.bottom<=box.bottom+1;
    } return false;
   }'''
   page.screenshot(path=str(a.output/f'{width}-{theme}-{mode}-empty.png'))
   regions=page.locator('.authoring-preview')
   assert regions.count() >= 2
   for region in regions.all():
    assert region.get_attribute('aria-label')
    assert region.evaluate("el=>getComputedStyle(el).borderTopStyle==='solid' && parseFloat(getComputedStyle(el).borderTopWidth)>=1")
   toolbar=page.locator('.edit-toolbar').first
   (a.output/f'{width}-{theme}-{mode}-initial.json').write_text(json.dumps({
    'width':width,'theme':theme,'mode':mode,'geometry':geometry(),
    'actions':toolbar.locator('md-icon-button').evaluate_all("els=>els.map(el=>({title:el.title,name:el.ariaLabel,rect:el.getBoundingClientRect().toJSON(),visible:getComputedStyle(el).display!=='none'}))")
   },ensure_ascii=False,indent=2))
   for action in toolbar.locator('md-icon-button').all():
    # Material upgrades delegate aria-label into the shadow button and retain
    # it as an ariaLabel property, not a reflected host aria-label attribute.
    label=action.get_attribute('title')
    assert label and action.evaluate('el=>el.ariaLabel') == label
    assert action.locator('svg').count()==1
    # Hidden responsive alternatives are absent from the accessible tree.
    # Their label/title/property are checked above; actual visible buttons
    # must still expose the intended semantic name and full target area.
    if action.is_visible():
     semantic=action.get_by_role('button',name=label,exact=True)
     expect(semantic).to_have_count(1)
     expect(semantic).to_have_accessible_name(label)
     box=semantic.bounding_box()
     assert box['width']>=48 and box['height']>=48, (label,box)
   toolbar_box=toolbar.bounding_box()
   pane_box=page.locator('.authoring-toolbar-shell').first.bounding_box()
   assert toolbar_box['x']>=pane_box['x']-0.1
   assert toolbar_box['x']+toolbar_box['width']<=pane_box['x']+pane_box['width']+0.1
   assert toolbar.evaluate('el=>el.scrollWidth<=el.clientWidth'), 'toolbar must fit without horizontal scrolling'
   more=toolbar.get_by_role('button',name='更多格式',exact=True)
   menu=toolbar.locator('md-menu')
   def secondary_action(label, keyboard=False, key='Enter'):
    if more.is_visible():
     if keyboard:
      more.focus(); more.press('Enter')
     else: more.click()
     item=menu.get_by_role('menuitem',name=label,exact=True)
     expect(item).to_be_visible()
     if keyboard:
      item.focus(); item.press(key)
     else: item.click()
     expect(item).not_to_be_visible()
    else:
     action=toolbar.get_by_role('button',name=label,exact=True)
     if keyboard:
      action.focus(); action.press(key)
     else: action.click()
   if width==390:
    expect(more).to_be_visible()
    assert len(toolbar.get_by_role('button').all())==3
    more.focus(); more.press('Enter')
    expect(menu.get_by_role('menuitem').first).to_be_visible()
    for item in menu.get_by_role('menuitem').all():
     box=item.bounding_box()
     assert box['height']>=48 and box['width']>=48, box
    menu_box=menu.locator('.menu').bounding_box()
    assert menu_box['x']>=0 and menu_box['x']+menu_box['width']<=width
    menu.get_by_role('menuitem').first.focus()
    menu.get_by_role('menuitem').first.press('ArrowDown')
    expect(menu.get_by_role('menuitem').nth(1)).to_be_focused()
    menu.get_by_role('menuitem').nth(1).press('ArrowUp')
    expect(menu.get_by_role('menuitem').first).to_be_focused()
    page.screenshot(path=str(a.output/f'{width}-{theme}-{mode}-overflow-open.png'))
    menu.get_by_role('menuitem').first.press('Escape')
    expect(menu.get_by_role('menuitem').first).not_to_be_visible()
    expect(more).to_be_focused()
    page.screenshot(path=str(a.output/f'{width}-{theme}-{mode}-overflow-dismissed.png'))
   assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme=='dark')
   def legacy_snapshot(stage):
    evidence=editor.evaluate("""el=>{
     const sel=window.getSelection(), range=sel?.rangeCount ? sel.getRangeAt(0) : null;
     const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT), runs=[];
     while(walker.nextNode()) { const node=walker.currentNode,s=getComputedStyle(node.parentElement);
      runs.push({text:node.textContent,fontWeight:s.fontWeight,fontStyle:s.fontStyle,textDecoration:s.textDecorationLine,parent:node.parentElement.outerHTML}); }
     const active=document.activeElement;
     return {html:el.innerHTML,text:el.textContent,runs,selected:sel?.toString(),
      range:range?{startOffset:range.startOffset,endOffset:range.endOffset,inside:el.contains(range.commonAncestorContainer)}:null,
      active:active?.outerHTML,styleWithCSS:document.queryCommandState('styleWithCSS'),
      bold:document.queryCommandState('bold'),underline:document.queryCommandState('underline'),
      preview:el.closest('.field-group').querySelector('.card-md-preview').innerHTML,
      commands:window.__legacyCommandEvidence};
    }""")
    evidence.update(width=width,theme=theme,mode=mode,stage=stage)
    (a.output/f'{width}-{theme}-{mode}-{stage}.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    page.screenshot(path=str(a.output/f'{width}-{theme}-{mode}-{stage}.png'))
    record_event(width,mode,'DIAGNOSTIC',stage=stage,html=evidence['html'],runs=evidence['runs'])
    return evidence
   if mode=='legacy':
    # Test-only instrumentation: call the real command unchanged, capture its
    # return value and selection immediately before/after, never app globals.
    page.evaluate("""() => {
     const command=document.execCommand.bind(document); window.__legacyCommandEvidence=[];
     document.execCommand=(name,...args)=>{
      const before=window.getSelection()?.toString(),result=command(name,...args);
      window.__legacyCommandEvidence.push({name,args,result,before,after:window.getSelection()?.toString(),html:document.querySelector('.rich-field')?.innerHTML});
      return result;
     };
    }""")
   editor.fill('selection'); editor.press('Control+a')
   if mode=='legacy': legacy_snapshot('bold-before')
   toolbar.get_by_role('button',name='加粗',exact=True).click()
   page.wait_for_timeout(350)
   if mode=='legacy':
    bold_evidence=legacy_snapshot('bold-after')
    if a.legacy_diagnostic:
     editor.press('Control+a'); secondary_action('下划线',keyboard=True)
     page.wait_for_timeout(350); legacy_snapshot('underline-after')
     editor.press('Control+a'); secondary_action('清除格式')
     page.wait_for_timeout(350); legacy_snapshot('clear-after')
     # Only this explicit diagnostic mode stops before normal strict asserts.
     page.close(); continue
    assert_legacy_format(bold_evidence,bold=True,underline=False,command='bold')
    # Independently check every text run in the rendered preview, not just an
    # element whose tag happens to imply bold. Source/selection above stay exact.
    preview_runs=page.locator('.card-md-preview').first.evaluate("""el=>{
     const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),runs=[];
     while(walker.nextNode()) { const n=walker.currentNode;
      if(n.textContent)runs.push({text:n.textContent,weight:getComputedStyle(n.parentElement).fontWeight}); }
     return runs;
    }""")
    assert preview_runs and ''.join(run['text'] for run in preview_runs)=='selection'
    assert all(float(run['weight'])>=700 for run in preview_runs), preview_runs
    editor.press('Control+a')
    secondary_action('下划线',keyboard=True)
    page.wait_for_timeout(350)
    assert_legacy_format(legacy_snapshot('underline-after'),bold=True,underline=True,command='underline')
    editor.press('Control+a')
    secondary_action('清除格式',keyboard=True,key='Space')
    page.wait_for_timeout(350)
    assert_legacy_format(legacy_snapshot('clear-after'),bold=False,underline=False,command='removeFormat')
    # The displayed preview must retain the exact text after clearing.
    assert page.locator('.card-md-preview').first.text_content()=='selection'
   else:
    assert editor.inner_text()=='**selection**'
    italic=toolbar.get_by_role('button',name='斜体',exact=True)
    italic.focus(); italic.press('Enter')
    page.wait_for_timeout(350)
    assert editor.inner_text()=='***selection***'
    editor.fill('')
    secondary_action('插入公式',keyboard=True)
    page.wait_for_timeout(350)
    assert editor.inner_text()=='$公式$'
    assert page.locator('.card-md-preview .katex').count()>0
    secondary_action('插入表格')
    page.wait_for_timeout(350)
    assert '|---|---|' in editor.inner_text()
    # Opening/cancelling the picker must not contact the upload API.
    with page.expect_file_chooser() as picked:
     secondary_action('插入图片')
    assert picked.value.is_multiple() is False
   if mode=='cloze':
    editor.fill('selection'); editor.press('Control+a'); editor.press('Control+Shift+c')
    page.wait_for_timeout(350)
    assert editor.inner_text()=='{{c1::selection}}'
    assert page.locator('.cloze-preview-box .cloze').count()>=1
   editor.fill(''); page.wait_for_timeout(350)
   baseline=geometry()
   if mode=='cloze':
    ordinal=page.locator('.cloze-ordinals')
    assert ordinal.count()==1 and abs(ordinal.bounding_box()['height']-40)<1, f'{width}/{mode}: ordinal reservation missing'
    record_event(width,mode,'ORDINAL',computed=ordinal.evaluate('el=>({hostClass:el.closest("md-dialog").className,height:getComputedStyle(el).height,flex:getComputedStyle(el).flex,wrap:getComputedStyle(el).flexWrap})'))
   previews=page.locator('.card-md-preview'); count=previews.count()
   previews.evaluate_all('els=>els.forEach(el=>el.dataset.mounted="yes")')
   transitions=['x','','{{c1::first}}','']
   if mode=='cloze':
    transitions += [' '.join(f'{{{{c{i}::answer}}}}' for i in range(1,25)), '']
   for step,text in enumerate(transitions):
    editor.fill(text); page.wait_for_timeout(350)
    assert previews.count()==count and previews.evaluate_all('els=>els.every(el=>el.dataset.mounted==="yes")')
    assert_geometry(repr(text))
    case=f'{width}-{theme}-{mode}-transition-{step}'
    (a.output/f'{case}.json').write_text(json.dumps({'case':case,'text':text,'geometry':geometry()},ensure_ascii=False,indent=2))
    page.screenshot(path=str(a.output/f'{case}.png'))
   inputs=page.locator('.rich-field' if mode=='legacy' else '.card-md-editor')
   assert inputs.first.bounding_box()['height']<=130, 'input should be compact'
   if mode!='cloze':
    b=[inputs.nth(i).bounding_box() for i in range(2)]
    assert (b[1]['x']>b[0]['x'] and abs(b[1]['y']-b[0]['y'])<1) if width>700 else (b[1]['y']>b[0]['y'] and abs(b[1]['x']-b[0]['x'])<1)
    # Each QA preview belongs below its own input, including stacked mobile.
    for i in range(2):
     pr=previews.nth(i).bounding_box()
     panel=page.locator('.authoring-preview').nth(i).bounding_box()
     assert abs(panel['x']-b[i]['x'])<1 and abs(panel['width']-b[i]['width'])<1
     # Inner scroll content is inset by the panel's actual border, not aligned
     # with its outer edge. Keep the original <1px subpixel tolerance.
     borders=previews.nth(i).evaluate("el=>{const s=getComputedStyle(el.closest('.authoring-preview'));return {left:parseFloat(s.borderLeftWidth),right:parseFloat(s.borderRightWidth)}}")
     assert abs(pr['x']-(panel['x']+borders['left']))<1
     assert abs(pr['width']-(panel['width']-borders['left']-borders['right']))<1
     assert pr['y']>=b[i]['y']+b[i]['height']-1
   else:
    b=[page.locator('.cloze-preview-col').nth(i).bounding_box() for i in range(2)]
    assert (b[1]['x']>b[0]['x'] and abs(b[1]['y']-b[0]['y'])<1) if width>620 else (b[1]['y']>b[0]['y'] and abs(b[1]['x']-b[0]['x'])<1)
   long='\n'.join(f'line {i}' for i in range(80))+'\nLAST LINE'
   if mode=='legacy':
    # Enter creates Chromium's native paragraph/div boundaries and input
    # events. fill() inserts raw text newlines, which is a different rich DOM.
    editor.fill('')
    for i,line in enumerate(long.split('\n')):
     if i: editor.press('Enter')
     page.keyboard.insert_text(line)
   else:
    editor.fill(long)
   editor.press('Control+End')
   scroller=page.locator('.rich-field' if mode=='legacy' else '.cm-scroller').first
   # End must expose the actual last line. Caret scrolling need not consume
   # bottom padding; reaching the absolute scroll limit is tested separately.
   wait_for_scroll(scroller,'source-last-line',last_line_visible)
   scroller.evaluate('el=>el.scrollTop=el.scrollHeight')
   wait_at_bottom(scroller,'source-bottom')
   wait_for_scroll(scroller,'source-bottom-last-line',last_line_visible)
   assert_geometry('long source')
   # Cloze's question/answer boxes must scroll too, not only the shared field.
   # Only the first field is filled so far. The empty extra-field preview
   # must remain mounted, but is not expected to overflow.
   long_previews=[previews.first]
   if mode=='cloze':
    # Use explicit field ownership rather than CSS sibling order: the first
    # shared preview plus the two front/back boxes are populated.
    boxes=page.locator('.cloze-preview-box')
    long_previews += [boxes.nth(i) for i in range(boxes.count())]
   for i,preview in enumerate(long_previews):
    preview.evaluate('el=>el.scrollTop=el.scrollHeight')
    wait_at_bottom(preview,f'preview-{i}-bottom')
   assert_geometry('scrolled previews')
   if mode=='cloze':
    secondary=page.locator('.cm-content').nth(1)
    secondary_scroller=page.locator('.cm-scroller').nth(1)
    secondary_preview=previews.nth(1)
    outer=page.locator('md-dialog .scroller')
    outer_top=outer.evaluate('el=>el.scrollTop')
    def secondary_geometry(stage):
     # Focusing an offscreen field legitimately scrolls the dialog. Restore
     # the same viewport offset before applying unchanged strict rectangles.
     outer.evaluate('(el,top)=>el.scrollTop=top',outer_top)
     assert_geometry(stage)
    for text in ['x','']:
     secondary.fill(text); page.wait_for_timeout(350)
     secondary_geometry(f'secondary {text!r}')
    secondary.fill(long); secondary.press('Control+End')
    wait_for_scroll(secondary_scroller,'secondary-last-line',last_line_visible)
    secondary_scroller.evaluate('el=>el.scrollTop=el.scrollHeight')
    wait_at_bottom(secondary_scroller,'secondary-source-bottom')
    wait_for_scroll(secondary_scroller,'secondary-bottom-last-line',last_line_visible)
    secondary_geometry('secondary long source')
    secondary_preview.evaluate('el=>el.scrollTop=el.scrollHeight')
    wait_at_bottom(secondary_preview,'secondary-preview-bottom')
    secondary_geometry('secondary scrolled preview')
    secondary.fill(''); page.wait_for_timeout(350)
    secondary_geometry('secondary cleared long content')
    assert previews.count()==count and previews.evaluate_all('els=>els.every(el=>el.dataset.mounted==="yes")')
   # Tall/mobile dialogs intentionally scroll. Verify the shadow scroller
   # exposes all of the tag controls, after the stationary geometry checks.
   tags=page.locator('.tags-section')
   tags.scroll_into_view_if_needed(timeout=5000)
   wait_for_scroll(tags,'dialog-tags-access','''el=>{
    const scroller=el.closest('md-dialog').shadowRoot.querySelector('.scroller');
    const box=scroller.getBoundingClientRect(), tag=el.getBoundingClientRect();
    return tag.height>0 && tag.top>=box.top-1 && tag.bottom<=box.bottom+1;
   }''')
   record_event(width,mode,'TAGS_ACCESS',scroll=page.locator('md-dialog .scroller').evaluate('el=>({scrollTop:el.scrollTop,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight})'))
   page.screenshot(path=str(a.output/f'{width}-{theme}-{mode}.png'))
   records.append({'width':width,'theme':theme,'mode':mode,'status':'PASS','geometry':baseline})
   record_event(width,mode,'PASS')
   (a.output/'geometry.json').write_text(json.dumps(records,indent=2))
   page.close()
 browser.close()
(a.output/'geometry.json').write_text(json.dumps(records,indent=2))
print('DIAGNOSTIC: legacy evidence captured; normal formatting assertions remain pending' if a.legacy_diagnostic else f'PASS: {len(records)} responsive editor cases')
