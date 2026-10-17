"""Mounted Solid acceptance regression. No production API/network/collection.
Build with source-scroll-build.mjs --outDir RUN/dist --data DATA.json. Use --run RUN --plan-only first.
Exit 0 all selected assertions pass; 1 assertion/test failure; 2 browser blocked.
Artifacts NEW RUN only; ephemeral SHORT scratch browser runtime is auto-cleaned.
"""
import argparse,asyncio,json,os,pathlib,re,sys,tempfile,time,traceback
RUN=pathlib.Path(__file__).resolve().parent
os.environ['PYTHONDONTWRITEBYTECODE']='1'
PLAN=[]
for mode in ['review','reading']:
 for target in [33,75,84]:
  for timing in ['preloaded','delayed70']:
   PLAN.append({'name':f'{mode}:{timing}-{target}','mode':mode,'kind':timing,'target':target,'acceptance':['A','D','E']})
 for kind in ['delayed900','forced-late-correction','failed-image','never-image','hidden-new','hidden-expired','reshow-unchanged','intent-wheel','intent-touch','intent-keyboard','intent-pointer','source-swap','unmount','reduced-motion']:
  PLAN.append({'name':f'{mode}:{kind}-75','mode':mode,'kind':kind,'target':75,'acceptance':['A','B','C','D','E']})
P=argparse.ArgumentParser(description=__doc__);P.add_argument('--run',type=pathlib.Path,required=True);P.add_argument('--plan-only',action='store_true');P.add_argument('--filter',default='');P.add_argument('--label',default='RED');args=P.parse_args()
RUN=args.run.resolve()
REPO=pathlib.Path(__file__).resolve().parents[2]
if RUN.is_relative_to(REPO):raise SystemExit('--run must be outside repository')
RUN.mkdir(parents=True,exist_ok=True)
selected=[x for x in PLAN if not args.filter or re.search(args.filter,x['name'])]
if not selected:raise SystemExit('No selected cases')
if args.plan_only:
 (RUN/'acceptance-plan.json').write_text(json.dumps({'criteria_frozen':'A-E from user; explicit reshow semantics: no new target => preserve intentional user scroll','all_cases':PLAN,'selected':selected},indent=2));print('plan-only: '+str(len(selected))+' cases');raise SystemExit(0)
OUT=RUN/(args.label+'-'+str(time.time_ns()));OUT.mkdir();summary={'status':'starting','selected':selected,'cases':[],'basis':'actual mounted Solid SidePanelTabs, NotePanel/ReadingPanel, FileViewer/cache/renderer; synthetic in-memory API + intercepted images'}
def save(): (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
save()
try:from playwright.async_api import async_playwright
except ImportError:
 cache=pathlib.Path('/home/shioriko/.cache/uv/archive-v0')
 for name in ['playwright','greenlet','pyee']:
  paths=list(cache.glob('*/'+name+'/__init__.py'))
  if paths:sys.path.insert(0,str(paths[0].parent.parent))
 paths=list(cache.glob('*/typing_extensions.py'))
 if paths:sys.path.insert(0,str(paths[0].parent))
 from playwright.async_api import async_playwright
INSTRUMENT=r'''(() => {
 window.audit={calls:[],flashes:[],observers:new Map(),next:0};
 new MutationObserver(records=>{for(const m of records){const el=m.target;if(el.closest?.('.note-body')&&el.classList.contains('note-hl')&&!(m.oldValue||'').split(/\s+/).includes('note-hl'))window.audit.flashes.push({at:performance.now(),line:el.dataset.srcLine})}}).observe(document,{subtree:true,attributes:true,attributeFilter:['class'],attributeOldValue:true});
 const rootFor=e=>e?.matches?.('.note-body')?e:e?.closest?.('.note-body');
 for(const name of ['scrollTo','scrollIntoView','scrollBy']) {const original=Element.prototype[name];Element.prototype[name]=function(...args){const root=rootFor(this);if(root)window.audit.calls.push({method:name,at:performance.now(),connected:root.isConnected,path:root.dataset.filePath,args});return original.apply(this,args)}}
 const d=Object.getOwnPropertyDescriptor(Element.prototype,'scrollTop');if(d?.set)Object.defineProperty(Element.prototype,'scrollTop',{...d,set(value){const root=rootFor(this);if(root)window.audit.calls.push({method:'scrollTop=',at:performance.now(),connected:root.isConnected,path:root.dataset.filePath,args:[value]});d.set.call(this,value)}});
 const Native=window.ResizeObserver;window.ResizeObserver=class extends Native {constructor(cb){super(cb);this.id=++window.audit.next;window.audit.observers.set(this.id,new Map())}observe(el,...opts){window.audit.observers.get(this.id).set(el,rootFor(el));return super.observe(el,...opts)}unobserve(el){window.audit.observers.get(this.id).delete(el);return super.unobserve(el)}disconnect(){window.audit.observers.get(this.id).clear();return super.disconnect()}};
})()'''
MEASURE=r'''() => {const root=document.querySelector('.note-body');if(!root)return {mounted:false,flashes:window.audit.flashes,calls:window.audit.calls,detachedObserved:[...window.audit.observers.values()].flatMap(m=>[...m.values()].filter(r=>r&&!r.isConnected)).length};const state=window.fixture.state(),line=state.segment.start_line,c=root.getBoundingClientRect();const target=root.querySelector(`[data-src-line="${line}"]`)||[...root.querySelectorAll('[data-src-line]')].filter(e=>+e.dataset.srcLine<=line).sort((a,b)=>+b.dataset.srcLine-+a.dataset.srcLine)[0];const rect=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,relativeTop:r.top-c.top,visible:r.height>0&&c.height>0&&r.bottom>c.top&&r.top<c.bottom}};return {mounted:true,state,at:performance.now(),windowY:scrollY,scrollTop:root.scrollTop,scrollHeight:root.scrollHeight,container:{top:c.top,bottom:c.bottom,height:c.height},target:rect(target),highlights:[...root.querySelectorAll('.note-hl')].map(rect),pendingImages:[...root.querySelectorAll('img')].filter(i=>!i.complete).length,loadedImages:[...root.querySelectorAll('img')].filter(i=>i.complete&&i.naturalWidth>0).length,flashes:window.audit.flashes,calls:window.audit.calls,detachedObserved:[...window.audit.observers.values()].flatMap(m=>[...m.values()].filter(r=>r&&!r.isConnected)).length};}'''
SVG='<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900"><rect width="1200" height="900" fill="#abc"/></svg>'
async def run_case(browser,spec,index):
 record={'spec':spec,'status':'running','samples':{},'checks':[],'network':[],'imageDimensions':'synthetic 1200x900, not actual article pixels'};summary['cases'].append(record)
 def persist():save();(OUT/f'case-{index:03d}.json').write_text(json.dumps(record,indent=2))
 persist();kind=spec['kind'];mode=spec['mode'];gate=asyncio.Event();never=asyncio.Event();image_count=0
 if kind in ['preloaded','hidden-new','hidden-expired','reshow-unchanged','reduced-motion']:gate.set()
 page=await browser.new_page(viewport={'width':1280,'height':800},reduced_motion='reduce' if kind=='reduced-motion' else 'no-preference',has_touch=kind=='intent-touch');errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
 await page.add_init_script(INSTRUMENT)
 async def route(r):
  nonlocal image_count
  url=r.request.url;record['network'].append({'url':url,'type':r.request.resource_type})
  if r.request.resource_type=='image':
   idx=image_count;image_count+=1
   await gate.wait()
   if kind=='never-image' and idx==0:await never.wait()
   if kind=='failed-image' and idx==0:await r.abort();return
   try:await r.fulfill(status=200,content_type='image/svg+xml',body=SVG)
   except Exception:pass
   return
  from urllib.parse import urlparse,unquote
  u=urlparse(url)
  if u.hostname!='source-scroll.test':await r.abort();return
  relative=unquote(u.path).lstrip('/') or 'source-scroll-fixture.html';p=(RUN/'dist'/relative).resolve()
  if not p.is_relative_to((RUN/'dist').resolve()) or not p.is_file():await r.abort();return
  import mimetypes
  await r.fulfill(status=200,body=p.read_bytes(),content_type=mimetypes.guess_type(str(p))[0] or 'application/octet-stream')
 await page.route('**/*',route)
 async def sample(label):
  value=await page.evaluate(MEASURE);record['samples'][label]=value;persist();return value
 def check(name,ok,detail=None):record['checks'].append({'name':name,'pass':bool(ok),'detail':detail});persist()
 async def setup(target=None):
  await page.goto('http://source-scroll.test/',wait_until='domcontentloaded')
  await page.wait_for_function('!!window.fixture');await page.evaluate('(s)=>window.fixture.set(s)',{'mode':mode,'target':target or spec['target'],'mounted':False})
  if kind in ['preloaded','hidden-new','hidden-expired','reshow-unchanged','reduced-motion']:
   await page.evaluate('async()=>{window.preloadedImages=await Promise.all(window.fixture.imageURLs.map(src=>new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(Error(src));img.src=src})));await Promise.all(window.preloadedImages.map(img=>img.decode()))}')
  await page.evaluate('window.fixture.set({mounted:true})');await page.wait_for_selector('.note-body [data-src-line]')
 async def visible(label,timeout=1400):
  deadline=time.monotonic()+timeout/1000;v=None
  while time.monotonic()<deadline:
   v=await page.evaluate(MEASURE)
   if v.get('target',{}).get('visible') and any(h['visible'] for h in v.get('highlights',[])):break
   await page.wait_for_timeout(40)
  v=await sample(label);check(label+': target+highlight visible',v.get('target',{}).get('visible') and any(h['visible'] for h in v.get('highlights',[])),v.get('target'));return v
 async def intent(which):
  start=await page.evaluate(MEASURE);record['samples']['before_input_'+which]=start;persist()
  box=await page.locator('.note-body').bounding_box();x=box['x']+box['width']/2;y=box['y']+box['height']/2
  if which=='wheel':await page.mouse.move(x,y);await page.mouse.wheel(0,550)
  elif which=='keyboard':await page.locator('.note-body').evaluate('(e)=>{e.tabIndex=0;e.focus({preventScroll:true})}');await page.keyboard.press('PageDown')
  elif which=='touch':
   cdp=await page.context.new_cdp_session(page);await cdp.send('Emulation.setTouchEmulationEnabled',{'enabled':True});await cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y+150}]});
   for step in range(1,7):
    await cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+150-step*50}]});await page.wait_for_timeout(50)
   await cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]});await cdp.detach()
  elif which=='pointer':await page.mouse.click(box['x']+box['width']-5,box['y']+box['height']-65)
  await page.wait_for_timeout(450)
  moved=await sample('after_input_'+which)
  check('genuine '+which+' scroll movement',abs(moved['scrollTop']-start['scrollTop'])>20,{'before':start['scrollTop'],'after':moved['scrollTop'],'note':'If this fails, investigate hit target/gesture as harness realism; no-yank assertion is not sufficient.'})
 try:
  await setup(8 if kind.startswith('hidden') else None)
  if kind.startswith('hidden'):
   await page.wait_for_function('Array.from(document.images).every(i=>i.complete)');await page.evaluate('window.fixture.tab(1)');await page.evaluate('(s)=>window.fixture.set(s)',{'target':spec['target'],'key':2});await page.wait_for_timeout(150 if kind=='hidden-new' else 3100);await sample('hidden_new_target');await page.evaluate('window.fixture.tab(0)');await visible('visible_after_show');await page.wait_for_timeout(2000);v=await sample('exposure_plus2000ms');check('highlight has meaningful post-show lifetime',any(h['visible'] for h in v.get('highlights',[])))
  elif kind=='reshow-unchanged':
   await page.wait_for_function('Array.from(document.images).every(i=>i.complete)');await visible('initial');await intent('wheel');before=await sample('intentional_user_position');await page.evaluate('window.fixture.tab(1)');await page.wait_for_timeout(80);await page.evaluate('window.fixture.tab(0)');await page.wait_for_timeout(400);after=await sample('unchanged_reshow');check('unchanged reshow preserves scroll',abs(after['scrollTop']-before['scrollTop'])<=2,{'before':before['scrollTop'],'after':after['scrollTop']})
  elif kind in ['source-swap','unmount']:
   await visible('initial_pending_images');await page.evaluate("window.previousRoot=document.querySelector('.note-body')")
   if kind=='unmount':await page.evaluate('window.fixture.unmount()')
   else:await page.evaluate("window.fixture.set({target:33,file:'second',key:2})");await page.wait_for_timeout(100)
   if kind=='source-swap':await visible('fresh_source_before_old_events')
   await page.wait_for_timeout(200);before_old=await sample('before_old_image_events');gate.set();await page.wait_for_timeout(500);after_old=await sample('old_images_released');check('stale image events do not scroll next source',len(after_old['calls'])==len(before_old['calls']),{'before':len(before_old['calls']),'after':len(after_old['calls'])});await page.set_viewport_size({'width':1240,'height':760});await page.wait_for_timeout(300);v=await sample('after_stale_images_and_resize');
   if kind=='unmount':check('old root detached',await page.evaluate('!window.previousRoot.isConnected'))
   check('no detached roots remain observed',v['detachedObserved']==0,v['detachedObserved']);check('no async scroll of detached root',not any(not c['connected'] for c in v['calls']),v['calls']);
   if kind=='source-swap':await visible('fresh_source_visible')
  elif kind.startswith('intent-'):
   await visible('initial_pending_images');await page.wait_for_timeout(250);await intent(kind.removeprefix('intent-'));before=await sample('after_user_intent');check('input produced an intentional scroll',before['target']['relativeTop'] < -10,before['target']);start_calls=len(before['calls']);gate.set();await page.wait_for_timeout(400);await page.set_viewport_size({'width':1240,'height':760});await page.wait_for_timeout(500);v=await sample('late_layout_after_user_intent');check('no automatic scroll calls after intent',len(v['calls'])==start_calls,{'before':start_calls,'after':len(v['calls'])});check('no yank back to target',v['target']['relativeTop'] < -10,v['target']);await page.evaluate("window.fixture.set({target:33,key:2})");await visible('new_target_authorizes_alignment')
  elif kind in ['preloaded','reduced-motion']:
   await page.wait_for_function('Array.from(document.images).every(i=>i.complete)');await visible('preloaded_settled');await page.wait_for_timeout(150);v=await sample('settled');check('no outer page jump',abs(v['windowY'])<=2,v['windowY'])
   if kind=='reduced-motion':check('reduced motion no smooth request',not any(c['args'] and isinstance(c['args'][0],dict) and c['args'][0].get('behavior')=='smooth' for c in v['calls']),v['calls'])
  else:
   if kind=='delayed70':await page.wait_for_timeout(70)
   else:
    await visible('prompt_before_images',1200)
    if kind=='delayed900':await page.wait_for_timeout(900)
    if kind=='forced-late-correction':
     # Adversarial fixture only: force a real correction instead of relying on
     # the browser's optional scroll-anchoring compensation. Product CSS unchanged.
     await page.locator('.note-body').evaluate("e=>e.style.overflowAnchor='none'")
     record['adversarialFixture']='overflow-anchor:none on panel; real article/range unchanged'
     await page.wait_for_timeout(1800)
   before=await sample('before_release');gate.set();await page.wait_for_function('Array.from(document.images).filter(i=>!i.complete).length <= '+('1' if kind=='never-image' else '0'));await page.wait_for_timeout(450);await visible('after_image_growth');v=await sample('growth_settled');check('anchor remains near panel top',-2<=v['target']['relativeTop']<=128,v['target']);check('no outer page jump',abs(v['windowY'])<=2,v['windowY']);
   if kind=='never-image':check('never-finishing request still pending',v['pendingImages']==1,v['pendingImages'])
   if kind in ['delayed900','forced-late-correction']:
    corrected=len(v['calls'])>len(before['calls'])
    if kind=='forced-late-correction':check('adversarial late layout caused actual realignment',corrected,{'before':before['calls'],'after':v['calls']})
    exposure=v['flashes'][-1]['at'] if v['flashes'] else None
    check('meaningful exposure recorded',exposure is not None)
    renewed=exposure is not None and bool(before['flashes']) and exposure>before['flashes'][-1]['at']+1
    if corrected:check('actual correction renewed exposure',renewed)
    elif abs(sum(h['height'] for h in v['highlights'])-sum(h['height'] for h in before['highlights']))<=1:
     check('uninterrupted unchanged exposure is not extended on image load',not renewed)
    record['exposureInterpretation']={'actualCorrection':corrected,'renewedExposure':renewed,'lastExposure':exposure,'basis':'normal 2800ms fade from actual flash; no unconditional extension on image load'};persist()
    if exposure is not None:
     now=await page.evaluate('performance.now()');await page.wait_for_timeout(max(0,exposure+2200-now));v=await sample('exposure_plus2200ms');check('highlight present within actual exposure duration',any(h['visible'] for h in v['highlights']))
     now=await page.evaluate('performance.now()');await page.wait_for_timeout(max(0,exposure+3050-now));v=await sample('exposure_plus3050ms');check('normal fade expires without further correction',not v['highlights'])
   else:
    n=len(v['calls']);await page.wait_for_timeout(400);steady=await sample('steady');check('no continuous scroll chasing on steady layout',len(steady['calls'])==n,{'before':n,'after':len(steady['calls'])})
  check('no application/network API calls',not any('/api/' in x['url'] for x in record['network']),record['network']);check('no page exceptions',not errors,errors)
  record['status']='PASS' if all(c['pass'] for c in record['checks']) else 'FAIL'
 except Exception:
  record['status']='ERROR';record['error']=traceback.format_exc()
  try:await sample('error_state')
  except Exception:pass
 finally:
  gate.set();never.set();persist();await page.close()
 print(record['status']+' '+spec['name'],flush=True)
async def main():
 # Chromium singleton sockets require a short active scratch runtime. This
 # temporary directory has no durable evidence and is removed on exit.
 scratch=pathlib.Path('/home/shioriko/.hermes/cache/scratch')
 with tempfile.TemporaryDirectory(prefix='sf',dir=scratch) as runtime:
  os.environ['TMPDIR']=runtime;tempfile.tempdir=runtime;summary['ephemeralRuntime']=runtime;save()
  async with async_playwright() as pw:
   exe=pathlib.Path('/home/shioriko/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome')
   try:browser=await pw.chromium.launch(executable_path=str(exe) if exe.exists() else None,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'],ignore_default_args=['--hide-scrollbars'],env={**os.environ,'TMPDIR':runtime})
   except Exception:
    summary['status']='BLOCKED_BROWSER_LAUNCH';summary['error']=traceback.format_exc();save();print('BLOCKED browser launch; this is NOT RED. Evidence '+str(OUT),flush=True);return 2
   try:
    summary['browserVersion']=browser.version
    for i,spec in enumerate(selected,1):await run_case(browser,spec,i)
   finally:await browser.close()
 summary['status']='PASS' if all(c['status']=='PASS' for c in summary['cases']) else 'FAIL';save();print('Evidence '+str(OUT));return 0 if summary['status']=='PASS' else 1
if __name__=='__main__':sys.exit(asyncio.run(main()))
