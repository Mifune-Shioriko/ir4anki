"""Synthetic production-component acceptance; no server or live requests."""
import argparse,json,mimetypes,traceback
from datetime import datetime
from itertools import product
from pathlib import Path
from urllib.parse import urlparse

THEMES=['light','dark']; WIDTHS=[1440,1366,1280,1181,1180,1179,390]
MODES=[('reading',False,False),('review',False,False),('review',True,False),('trace',False,False),('review',False,True),('review',True,True)]
DEFAULT=dict(mode='reading',revealed=False,legacy=False,metadata='normal',busy=False,undo=True,source=True,cloze=False,ord=1,cardId=1,isNew=True,status='active',long=False,sourceVersion=0)
plan=[]
def add(kind,theme,width,**config): plan.append(dict(kind=kind,theme=theme,width=width,config={**DEFAULT,**config}))
for theme,width,(mode,revealed,legacy) in product(THEMES,WIDTHS,MODES): add('core',theme,width,mode=mode,revealed=revealed,legacy=legacy)
# Edge states cross both themes, desktop/phone, and all actual study/reveal modes.
for theme,width,(mode,revealed,legacy),edge in product(THEMES,[1440,390],MODES,['long-meta','empty-meta','busy','no-undo','no-source','container']):
 config=dict(mode=mode,revealed=revealed,legacy=legacy)
 config.update({'long-meta':{'metadata':'long'},'empty-meta':{'metadata':'empty'},'busy':{'busy':True},'no-undo':{'undo':False},'no-source':{'source':False},'container':{'status':'container'}}[edge])
 add('edge-'+edge,theme,width,**config)
for theme,width in product(THEMES,[1440,390]):
 for ord in [0,1]: add('cloze',theme,width,mode='review',cloze=True,ord=ord)
 for mode in ['reading','review']: add('transitions',theme,width,mode=mode)
 add('scroll',theme,width,mode='review',revealed=True,long=True)
for theme,width in product(THEMES,[1440,390]): add('math',theme,width,mode='pair',revealed=True)
for theme in THEMES: add('pair',theme,1440,mode='pair',revealed=True)
for width in [1440,390]: add('authoring','light',width,mode='authoring')
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--plan-only',action='store_true');p.add_argument('--case',type=int,action='append',help='Run original matrix case number; repeatable');p.add_argument('--kinds',help='Comma-separated exact kinds, e.g. math,pair');p.add_argument('--kind',action='append',help='Exact kind; repeatable');p.add_argument('--filter',help='Substring of kind:theme:width:mode:revealed');a=p.parse_args();run=a.run.resolve()
full_count=len(plan)
for index,spec in enumerate(plan,1): spec['matrix_case']=index
if a.case:
 assert all(1<=i<=full_count for i in a.case),'Invalid matrix case number'
 plan=[spec for spec in plan if spec['matrix_case'] in a.case]
selected_kinds=set(a.kind or [])|set(a.kinds.split(',') if a.kinds else [])
if selected_kinds:
 assert selected_kinds<={spec['kind'] for spec in plan},'Unknown or excluded kind'
 plan=[spec for spec in plan if spec['kind'] in selected_kinds]
if a.filter: plan=[spec for spec in plan if a.filter in f"{spec['kind']}:{spec['theme']}:{spec['width']}:{spec['config']['mode']}:{spec['config']['revealed']}"]
assert plan,'Filter selected no cases'
counts={kind:sum(c['kind']==kind for c in plan) for kind in sorted({c['kind'] for c in plan})}
summary={'full_matrix_cases':full_count,'expected_cases':len(plan),'by_kind':counts,'cases':plan}
if a.plan_only:
 (run/'matrix-plan.json').write_text(json.dumps(summary,indent=2));print(json.dumps({'expected_cases':len(plan),'by_kind':counts}));raise SystemExit()
from playwright.sync_api import sync_playwright
out=run/('browser-acceptance-'+datetime.now().strftime('%Y%m%dT%H%M%S.%f'));out.mkdir();dist=run/'fixture-dist';records=[]
(out/'plan.json').write_text(json.dumps(summary,indent=2));print('Browser evidence: '+str(out),flush=True)
def save():
 (out/'ledger.json').write_text(json.dumps({'expected_cases':len(plan),'completed':sum(c['status']=='PASS' for c in records),'cases':records},indent=2))
 if records: (out/f'case-{len(records):04d}.json').write_text(json.dumps(records[-1],indent=2))
MEASURE='''() => [...document.querySelectorAll('.study-shell')].map(shell=>{
 const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
 const style=e=>{const s=getComputedStyle(e);return Object.fromEntries(['fontSize','lineHeight','paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginBottom','display','color','backgroundColor'].map(k=>[k,s[k]]))};
 const content=e=>({class:e.className,box:box(e),style:style(e),text:e.textContent,typography:Object.fromEntries(['h1','p','li','blockquote','table','code','img','.katex'].map(k=>[k,e.querySelector(k)?style(e.querySelector(k)):null])),hr:[...e.querySelectorAll('hr')].map(style),clozes:[...e.querySelectorAll('.cloze')].map(el=>({ordinal:el.getAttribute('data-ordinal'),text:el.textContent,...style(el)}))});
 const header=shell.querySelector('.study-header'),toolbar=shell.querySelector('.study-toolbar'),source=shell.querySelector('.study-source-row'),label=source.querySelector('.study-source'),dock=shell.querySelector('.study-action-dock');
 return {stage:shell.className,header:box(header),toolbar:box(toolbar),inner:box(shell.querySelector('.card-inner')),source:box(source),sourceLabel:label?.textContent,sourceTitle:label?.title,sourceDetail:label?{box:box(label),scrollWidth:label.scrollWidth,clientWidth:label.clientWidth,whiteSpace:getComputedStyle(label).whiteSpace,textOverflow:getComputedStyle(label).textOverflow,overflow:getComputedStyle(label).overflow}:null,badge:!!source.querySelector('.info-chip--new'),contents:[...shell.querySelectorAll('.study-content')].map(content),dock:dock?box(dock):null,dockParent:dock?.parentElement===shell,dockButtons:[...shell.querySelectorAll('.study-action-dock .ease-buttons > *')].map(e=>({box:box(e),computed:style(e),containerTarget:getComputedStyle(e).getPropertyValue('--_container-height').trim(),shadowButton:box(e.shadowRoot.querySelector('button')),shadowLabel:box(e.shadowRoot.querySelector('.label')),keycap:e.querySelector('.keycap')?{box:box(e.querySelector('.keycap')),computed:style(e.querySelector('.keycap'))}:null,disabled:e.disabled,label:e.textContent.trim()})),buttons:[...toolbar.querySelectorAll('md-icon-button')].map(e=>({box:box(e),label:e.ariaLabel,disabled:e.disabled,icon:box(e.querySelector('svg')),iconHost:box(e.querySelector('md-icon')),state:box(e.shadowRoot.querySelector('md-ripple')),rippleDisplay:getComputedStyle(e.shadowRoot.querySelector('md-ripple')).display,nativeButton:box(e.shadowRoot.querySelector('button')),stateTokens:{width:getComputedStyle(e).getPropertyValue('--md-icon-button-state-layer-width').trim(),height:getComputedStyle(e).getPropertyValue('--md-icon-button-state-layer-height').trim()},nativeDisabled:e.shadowRoot.querySelector('button').disabled}))}
})'''
MATH_READINESS = r"""() => {
 const fixture=window.fixture,{config,revealed}=fixture.state();
 const sourceMath=source=>[...source.matchAll(/\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g)].map(m=>(m[1]??m[2]).trim());
 const expected=[];
 if(['reading','trace','pair'].includes(config.mode)) expected.push({kind:'reading',tex:sourceMath(fixture.chunk().text)});
 if(['review','pair'].includes(config.mode)) {
  const card=fixture.card();expected.push({kind:'question',tex:config.legacy?[]:sourceMath(card.fields.Front)});
  if(revealed)expected.push({kind:'answer',tex:config.legacy?[]:sourceMath(card.fields.Back)});
 }
 const bodies=[...document.querySelectorAll('.study-content')];
 const actual=bodies.map(body=>({class:body.className,tex:[...body.querySelectorAll('.katex annotation[encoding="application/x-tex"]')].map(e=>e.textContent.trim()),unrendered:body.textContent.includes('\\[')||body.textContent.includes('\\(')}));
 return {ready:actual.length===expected.length&&expected.every((e,i)=>JSON.stringify(e.tex)===JSON.stringify(actual[i]?.tex)&&!actual[i]?.unrendered),expected,actual};
}"""
def wait_math(page,phase):
 # Observe complete formula sets in every intended body, including newly mounted answers.
 # A missing production rAF/render remains a timeout failure, with raw DOM evidence.
 before=page.evaluate(MATH_READINESS)
 try:
  page.wait_for_function('() => ('+MATH_READINESS+')().ready',timeout=10000)
  page.evaluate('async () => { await document.fonts.ready }')
 finally:
  case.setdefault('mathReadiness',[]).append({'phase':phase,'before':before,'after':page.evaluate(MATH_READINESS)})
  save()
MATH_MEASURE = r"""() => [...document.querySelectorAll('.study-content')].map(body=>{
 const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
 return {class:body.className,math:[...body.querySelectorAll('.katex')].map(math=>{
  const html=math.querySelector('.katex-html'),origin=rect(html),leaves=[];
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
  const font=e=>{const s=getComputedStyle(e);return {fontFamily:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight,fontWeight:s.fontWeight,fontStyle:s.fontStyle}};
  const walker=document.createTreeWalker(html,NodeFilter.SHOW_TEXT);let text;
  while(text=walker.nextNode()){
   if(!text.textContent.replace(/[\s\u200b]/g,'')||text.parentElement.closest('.strut,.katex-strut,.pstrut,.vlist-s'))continue;
   const range=document.createRange();range.selectNodeContents(text);const r=range.getBoundingClientRect();
   const f=font(text.parentElement);ctx.font=`${f.fontStyle} ${f.fontWeight} ${f.fontSize} ${f.fontFamily}`;const metrics=ctx.measureText(text.textContent);
   const marker=document.createElement('span');marker.style.cssText='display:inline-block;width:0;height:0;padding:0;margin:0;border:0;vertical-align:baseline;';text.parentElement.append(marker);const baseline=marker.getBoundingClientRect().y;marker.remove();
   const ink={y:baseline-metrics.actualBoundingBoxAscent,bottom:baseline+metrics.actualBoundingBoxDescent};
   leaves.push({font:f,ink,text:text.textContent,box:{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height},relativeY:r.y-origin.y});
  }
  const clips=[];for(let e=math.parentElement;e&&e!==body;e=e.parentElement){
   const style=getComputedStyle(e);if(e.matches('.katex-block,.katex-display')&&['auto','hidden','clip','scroll'].includes(style.overflowY)){
    const r=rect(e);clips.push({class:e.className,box:r,overflowX:style.overflowX,overflowY:style.overflowY,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight});
   }
  }
  return {tex:math.querySelector('annotation')?.textContent,display:!!math.closest('.katex-display'),box:rect(math),html:origin,leaves,clips,fonts:[...html.querySelectorAll('*')].map(e=>({class:e.className,...font(e)})),layout:[...html.querySelectorAll('.vlist,.vlist-t,.vlist-r,.vlist > span,.mord,.mfrac')].map(e=>{const style=getComputedStyle(e);return {class:e.className,inline:e.getAttribute('style')||'',declarations:Object.fromEntries(Array.from(e.style).map(k=>[k,{value:e.style.getPropertyValue(k).trim(),priority:e.style.getPropertyPriority(k)}])),position:style.position,top:style.top,height:style.height,verticalAlign:style.verticalAlign,relativeY:rect(e).y-origin.y}}),markup:math.outerHTML};
 })};
})"""
class SnapshotPageBase: pass
def math_check(page):
 if not isinstance(page,SnapshotPageBase): wait_math(page,'math-clipping-check')
 result=page.evaluate(MATH_MEASURE);case['mathComputed']=result;save()
 assert len(result)==3,result
 reading=result[0]['math'];assert reading,'Reading has no formulas'
 for body in result[1:]:
  assert len(body['math'])==len(reading),body
  for reference,actual in zip(reading,body['math']):
   assert reference['tex']==actual['tex'] and reference['display']==actual['display'],actual
   assert reference['fonts']==actual['fonts'],{'reading':reference['fonts'],'review':actual['fonts']}
   assert len(reference['layout'])==len(actual['layout']),actual
   for left,right in zip(reference['layout'],actual['layout']):
    assert all(left[k]==right[k] for k in ['class','declarations','position']),{'reading':left,'review':right}
    for prop in ['top','height','verticalAlign']:
     l,r=left[prop],right[prop]
     if l.endswith('px') and r.endswith('px'): assert abs(float(l[:-2])-float(r[:-2]))<=0.5,{'property':prop,'reading':left,'review':right}
     else: assert l==r,{'property':prop,'reading':left,'review':right}
    assert abs(left['relativeY']-right['relativeY'])<=0.5,{'reading':left,'review':right}
   assert abs(reference['html']['height']-actual['html']['height'])<=0.5,actual
 for body in result:
  for math in body['math']:
   assert math['tex'] and math['leaves'] and math['html']['height']>0,math
   assert any('height' in e['declarations'] and e['declarations']['height']['value'] for e in math['layout'] if 'vlist' in e['class'].split()),math
   assert any('top' in e['declarations'] and e['declarations']['top']['value'] for e in math['layout']),math
   for clip in math['clips']:
    for leaf in math['leaves']:
     assert leaf['ink']['y']>=clip['box']['y']-0.5 and leaf['ink']['bottom']<=clip['box']['bottom']+0.5,{'formula':math['tex'],'body':body['class'],'clip':clip,'leaf':leaf}
 return result
COLORS={'light':{'background':'rgb(254, 247, 255)','content':'rgb(29, 27, 32)','dock':'rgb(236, 230, 240)','cloze':['rgb(255, 241, 155)','rgb(51, 45, 0)']},'dark':{'background':'rgb(20, 18, 24)','content':'rgb(230, 224, 233)','dock':'rgb(43, 41, 48)','cloze':['rgb(102, 89, 0)','rgb(255, 244, 166)']}}
def theme_check(page,theme):
 actual=page.evaluate('''()=>({dark:document.documentElement.classList.contains('dark'),background:getComputedStyle(document.body).backgroundColor,content:document.querySelector('.study-content')?getComputedStyle(document.querySelector('.study-content')).color:null,dock:document.querySelector('.ease-buttons')?getComputedStyle(document.querySelector('.ease-buttons')).backgroundColor:null})''')
 assert actual['dark']==(theme=='dark'),actual
 for k in ['background','content','dock']:
  if actual[k] is not None: assert actual[k]==COLORS[theme][k],actual
 return actual
def geometry(page,c):
 result=page.evaluate(MEASURE)
 case.setdefault('geometrySamples',[]).append(result);save()
 for g in result:
  assert g['header']['height']==48 and abs(g['toolbar']['right']-(g['inner']['right']-20))<1,g
  assert g['toolbar']['x']>=g['inner']['x'] and g['toolbar']['right']<=g['inner']['right'],g
  assert g['source']['height']==20,g
  if c['metadata']=='empty': assert g['sourceLabel'] is None and g['sourceTitle'] is None,g
  else:
   d=g['sourceDetail'];assert g['sourceTitle']==g['sourceLabel'] and d['whiteSpace']=='nowrap' and d['textOverflow']=='ellipsis' and d['overflow']=='hidden',g
   assert d['box']['x']>=g['source']['x'] and d['box']['right']<=g['source']['right'],g
   if c['metadata']=='long': assert d['scrollWidth']>d['clientWidth'],g
  assert g['badge']==(c['mode'] in ['review','pair'] and c['isNew']),g
  assert len(g['contents'])==(2 if c['mode']=='review' and c['revealed'] else 1),g
  for body in g['contents']:
   s=body['style'];assert s['fontSize']=='18px' and s['lineHeight']=='30.6px',body
   assert all(s[k]=='0px' for k in ['paddingTop','paddingRight','paddingBottom']),body
   assert len(body['hr'])>=1 and all(hr['display']!='none' for hr in body['hr']),body
   if not c['legacy']: assert all(body['typography'][k] is not None for k in ['h1','p','li','blockquote','table','code','img','.katex']),body
   for cloze in body['clozes']: assert [cloze['backgroundColor'],cloze['color']]==COLORS[case['theme']]['cloze'],cloze
  if c['legacy']: assert g['contents'][0]['clozes'][0]['ordinal']=='2',g
  for b in g['buttons']:
   assert all(b[k]['width']==b[k]['height']==size for k,size in [('box',48),('nativeButton',48),('icon',20),('iconHost',20)]),b
   assert b['stateTokens']=={'width':'48px','height':'48px'},b
   if b['disabled']:
    assert (b['state']['width'],b['state']['height']) in [(0,0),(48,48)],b
    if b['state']['width']==0: assert b['rippleDisplay']=='none',b
   else: assert b['state']['width']==b['state']['height']==48,b
   assert b['disabled']==b['nativeDisabled'],b
  if c['mode']=='trace': assert g['dock'] is None,g
  else:
   assert g['dockParent'],g
   if sum(b['box']['width'] for b in g['dockButtons'])>g['dock']['width']:
    assert len({b['box']['y'] for b in g['dockButtons']})>1,'Narrow dock did not wrap'
   for b in g['dockButtons']:
    # Material's token is min-height; inline keycap line boxes can add 0.375px.
    # Retain exact target and bound only measured fractional layout error.
    assert b['containerTarget']=='44px' and abs(b['box']['height']-44)<=0.5 and b['box']['x']>=g['dock']['x'] and b['box']['right']<=g['dock']['right']+0.5,b
    if b['keycap']:
     natural=b['shadowLabel']['height']+float(b['computed']['paddingTop'].removesuffix('px'))+float(b['computed']['paddingBottom'].removesuffix('px'))
     assert abs(b['box']['height']-max(44,natural))<=0.5,b
     assert b['keycap']['box']['y']>=b['shadowLabel']['y']-0.5 and b['keycap']['box']['bottom']<=b['shadowLabel']['bottom']+0.5,b
 return result
def calls(page): return page.evaluate('window.fixture.calls')
def clear(page): page.evaluate('window.fixture.clear()')
def expected(name,args=None): return {'name':name,'args':[] if args is None else args}
def toolbar(page,c):
 names=['reading-undo','exit-round','reading-add','cloze','reading-edit'] if c['mode']=='reading' else ['reading-add','cloze','reading-edit','back'] if c['mode']=='trace' else ['review-undo','trace','review-add','delete-confirmation','review-edit']
 buttons=page.locator('.study-toolbar md-icon-button');assert buttons.count()==len(names)
 # Walk Tab once through the toolbar, rather than restarting navigation per icon.
 page.locator('#neutral').focus()
 for i,name in enumerate(names):
  host=buttons.nth(i);native=host.locator('button');clear(page)
  if host.evaluate('e=>e.disabled'):
   page.locator('#neutral').focus()
   native.evaluate('e=>e.click()');host.dispatch_event('keydown',{'key':'Enter'});host.dispatch_event('keydown',{'key':' '});assert calls(page)==[]
  else:
   for _ in range(15):
    page.keyboard.press('Tab')
    if native.evaluate('e=>e===e.getRootNode().activeElement'): break
   else: raise AssertionError('Toolbar unreachable by Tab: '+name)
   assert native.evaluate('e=>e.matches(":focus-visible")')
   native.press('Enter');native.press('Space');assert calls(page)==[expected(name,['',''] if name=='cloze' else None)]*2,calls(page)
  case.setdefault('toolbarChecks',[]).append({'name':name,'disabled':host.evaluate('e=>e.disabled'),'calls':calls(page)});save()
def dock_actions(page,c):
 if c['mode']=='trace': return
 buttons=page.locator('.study-action-dock .ease-buttons > *')
 if c['mode']=='reading': actions=[('exit-reading',[]),('skip',[]),('next',[]),('complete',[])]
 elif c['revealed']: actions=[('answer',[1]),('answer',[2]),('answer',[3]),('answer',[4])]
 else: actions=[('reveal',[])]
 assert buttons.count()==len(actions)
 for i,(name,args) in enumerate(actions):
  clear(page);host=buttons.nth(i);assert host.evaluate('e=>e.disabled')==c['busy']
  if c['busy']: host.locator('button').evaluate('e=>e.click()');assert calls(page)==[]
  else: host.click();assert calls(page)==[expected(name,args)],calls(page)
  if name=='reveal' and not c['busy']:
   assert page.evaluate('window.fixture.state().revealed') and page.locator('.card-answer').count()==1
   wait_math(page,'dock-reveal')
   case['revealedGeometry']=geometry(page,{**c,'revealed':True})
  case.setdefault('dockChecks',[]).append({'name':name,'busy':c['busy'],'calls':calls(page)});save()
 if c['mode']=='review':
  clear(page);exit=page.locator('.flow-actions md-text-button');assert exit.count()==1
  assert exit.evaluate('e=>e.disabled')==c['busy']
  if c['busy']: exit.locator('button').evaluate('e=>e.click()');assert calls(page)==[]
  else: exit.click();assert calls(page)==[expected('exit-round')]
def selection(page):
 clear(page);page.locator('.line-handle').first.click();page.locator('.split-confirm__ok').click()
 assert calls(page)==[expected('split',[{'start_line':1,'end_line':1}])],calls(page)
 clear(page);lines=page.locator('.reading-chunk-body p .line .line-handle');lines.nth(0).click();lines.nth(1).click()
 assert page.locator('.split-confirm__preview').count()==2
 assert 'Selectable source sentence.' in page.locator('.split-confirm__preview').nth(0).inner_text()
 assert 'Second source line.' in page.locator('.split-confirm__preview').nth(1).inner_text()
 page.locator('.split-confirm__ok').click();assert calls(page)==[expected('split',[{'start_line':3,'end_line':4}])],calls(page)
 clear(page);page.locator('.line-anchor:has(hr) .line-handle').click();page.locator('.split-confirm__ok').click()
 assert calls(page)==[expected('split',[{'start_line':6,'end_line':6}])],calls(page)
 page.evaluate('''()=>{const el=document.querySelector('.reading-chunk-body p .line');const node=[...el.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());const range=document.createRange();range.selectNodeContents(node);getSelection().removeAllRanges();getSelection().addRange(range);document.dispatchEvent(new Event('selectionchange'))}''')
 clear(page);page.locator('.study-toolbar md-icon-button').nth(3).click()
 assert calls(page)==[expected('cloze',['Selectable source sentence.','Selectable source sentence. Second source line.'])],calls(page)
 case['selectionCalls']=calls(page)
def transition(page,c):
 if c['mode']=='reading':
  selection(page)
  handles=page.locator('.line-handle').count();assert handles>0
  page.evaluate('window.fixture.set({metadata:"long",busy:true,status:"container"})');wait_math(page,'reading-metadata-transition')
  assert page.locator('.line-handle').count()==handles
  case['metadataTransition']=page.evaluate(MEASURE)
  assert page.locator('.study-toolbar md-icon-button').nth(4).evaluate('e=>e.disabled')
  page.evaluate('window.fixture.set({sourceVersion:1,source:false,busy:false,status:"todo",metadata:"empty"})');wait_math(page,'reading-source-transition')
  assert 'Changed source sentence.' in page.locator('.reading-chunk-body').inner_text()
  assert page.locator('.line-handle').count()>0 and page.locator('.study-source').count()==0
  clear(page);page.locator('.study-toolbar md-icon-button').nth(3).click();assert calls(page)==[expected('cloze',['',''])],'Old selection leaked to new source'
 else:
  for id,meta,new,reveal,source in [(2,'long',False,True,False),(3,'empty',True,False,True),(4,'normal',False,True,True)]:
   page.evaluate('c=>window.fixture.set(c)',dict(cardId=id,metadata=meta,isNew=new,revealed=reveal,source=source));wait_math(page,'review-card-transition-'+str(id))
   assert page.locator('.info-chip--new').count()==int(new)
   assert page.locator('.card-answer').count()==int(reveal)
   if reveal: assert f'Answer for card {id}' in page.locator('.card-answer').inner_text()
   assert page.locator('.study-toolbar md-icon-button').nth(1).evaluate('e=>e.disabled')==(not source)
   assert page.evaluate('window.fixture.card().cardId')==id
   case.setdefault('reactiveGeometry',[]).append(geometry(page,{**c,'metadata':meta,'isNew':new,'revealed':reveal}))
   save()
def scroll_check(page):
 assert page.locator('.card-answer').bounding_box()['height']>2000
 assert page.locator('.flow-actions').count()==1
 def snapshot(): return page.evaluate('''()=>{const box=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}};return {scroll:scrollY,viewport:innerHeight,shell:box(document.querySelector('.study-shell')),dock:box(document.querySelector('.study-action-dock')),exit:box(document.querySelector('.flow-actions'))}}''')
 shots=[]
 shell=page.locator('.study-shell').bounding_box();assert shell['height']>2000
 for y in [200,900]:
  page.evaluate('y=>scrollTo(0,y)',y);page.wait_for_timeout(40);s=snapshot();shots.append(s)
  assert s['dock']['top']>=0 and abs(s['dock']['bottom']-(s['viewport']-12))<=1,s
  assert s['shell']['top']<0 and s['shell']['bottom']>s['viewport'],s
 page.evaluate('()=>scrollTo(0,document.querySelector(".study-shell").getBoundingClientRect().bottom+scrollY+100)');page.wait_for_timeout(40);s=snapshot();shots.append(s)
 assert s['shell']['bottom']<0 and s['dock']['bottom']<0,s
 assert s['exit']['top']>=s['shell']['bottom'],s
 case['scrollChecks']=shots
 page.screenshot(path=str(out/f'scroll-release-{len(records)}.png'),full_page=True)
save()
with sync_playwright() as pw:
 try: browser=pw.chromium.launch(headless=True)
 except Exception as e: (out/'launch-error.json').write_text(json.dumps({'error':str(e)},indent=2));raise
 for spec in plan:
  case={**spec,'status':'RUNNING','errors':[],'requests':[]};records.append(case);save();c=spec['config']
  page=browser.new_page(viewport={'width':spec['width'],'height':900},color_scheme=spec['theme']);page.on('pageerror',lambda e:case['errors'].append(str(e)))
  def route(r):
   u=urlparse(r.request.url);case['requests'].append(r.request.url)
   if u.hostname=='study.fixture' and u.path=='/synthetic-image.svg': r.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"/>',content_type='image/svg+xml');return
   f=(dist/('study-fixture.html' if u.path=='/' else u.path.lstrip('/'))).resolve()
   if u.hostname!='study.fixture' or '/api/' in u.path or not f.is_relative_to(dist) or not f.is_file(): case['errors'].append('Rejected network: '+r.request.url);r.abort();return
   r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
  page.route('**/*',route)
  try:
   page.goto('http://study.fixture/');page.wait_for_function('!!window.fixture');page.evaluate('c=>window.fixture.set(c)',c);wait_math(page,'initial-state')
   case['themeComputed']=theme_check(page,spec['theme']);kind=spec['kind']
   if kind=='cloze':
    q=page.locator('.card-question');target='other' if c['ord']==0 else 'preserved';visible='preserved' if c['ord']==0 else 'other';placeholder='[…]' if c['ord']==0 else '[hint]'
    assert target not in q.inner_text() and visible in q.inner_text()
    assert q.locator('.cloze').inner_text()==placeholder
    for mode in ['q','a']:
     if mode=='a':
      page.locator('.study-action-dock md-filled-tonal-button').click();wait_math(page,'cloze-reveal')
     cloze=page.locator('.card-question .cloze' if mode=='q' else '.card-answer .cloze')
     assert cloze.evaluate('e=>[getComputedStyle(e).backgroundColor,getComputedStyle(e).color]')==COLORS[spec['theme']]['cloze']
     if mode=='a': assert cloze.inner_text()==target
    assert page.evaluate('window.fixture.card().kind')=='cloze' and page.evaluate('window.fixture.card().ord')==c['ord']
    case['question']=q.inner_text();case['answer']=page.locator('.card-answer').inner_text()
   elif kind=='authoring':
    page.locator('.cm-content').wait_for();preview=page.locator('.card-md-preview');before=preview.bounding_box()
    page.locator('.cm-content').fill('Synthetic edited source');page.wait_for_timeout(50);assert preview.bounding_box()['height']==before['height']==96
    page.locator('.nav-rail__item').nth(1).press('Enter');assert page.evaluate('window.fixture.state().section')=='files'
   elif kind=='math':
    # At phone widths test full-width modes sequentially, not a fake two-card phone layout.
    if spec['width']==390:
     snapshots=[]
     for mode in ['reading','review']:
      page.evaluate('mode=>window.fixture.set({mode,revealed:true})',mode);wait_math(page,'phone-math-'+mode);snapshots.extend(page.evaluate(MATH_MEASURE))
     case['mathComputed']=snapshots;save()
     # Reuse the same checker against captured real-mode DOM snapshots.
     class SnapshotPage(SnapshotPageBase):
      def evaluate(self,script): return snapshots
     math_check(SnapshotPage())
    else: math_check(page)
   elif kind=='pair':
    pair=page.evaluate(MEASURE);case['geometry']=pair
    assert pair[0]['contents'][0]['typography']==pair[1]['contents'][0]['typography']
    math_check(page)
    assert pair[0]['sourceLabel']==pair[1]['sourceLabel'] and pair[0]['source']['height']==pair[1]['source']['height']
   else:
    case['geometry']=geometry(page,c);save()
    if kind=='transitions': transition(page,c)
    elif kind=='scroll': scroll_check(page)
    else:
     toolbar(page,c);dock_actions(page,c)
     if c['mode'] in ['reading','trace']: assert page.locator('.line-handle').count()>0
     if kind=='core' and c['mode']=='reading': selection(page)
   assert not case['errors'],case['errors'];case['status']='PASS';case['finalCalls']=calls(page)
   if spec['kind'] in ['core','pair','math','scroll','transitions','cloze','authoring']: page.screenshot(path=str(out/f'case-{len(records):04d}-{kind}.png'),full_page=True)
  except Exception:
   case['status']='FAIL';case['failure']=traceback.format_exc();page.screenshot(path=str(out/f'failure-{len(records)}.png'),full_page=True);save();raise
  finally: save();page.close()
 browser.close()
 assert len(records)==len(plan) and all(c['status']=='PASS' for c in records)
 print(f'PASS {len(records)}/{len(plan)} cases',flush=True)
