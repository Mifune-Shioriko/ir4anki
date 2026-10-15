import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { createRoot, createSignal, createEffect } from 'solid-js'
const which=process.argv[2]
const compile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const actual=(s,ctx,ret)=>new Function(...Object.keys(ctx),compile(s)+`;return ${ret}`)(...Object.values(ctx))
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve}}
const tick=()=>new Promise(r=>setTimeout(r,0))
if(which==='F1') {
 const {FileOperations}=await import('../src/lib/fileOperations.ts')
 const {ApiError}=await import('../src/api.ts')
 const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
 const files=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
 const remapPath=(p,o,n)=>p===null?null:p===o||p.startsWith(o+'/')?n+p.slice(o.length):p
 for(const mode of ['lost-response','FS-recovery','DB-recovery','flow-recovery']) {
  const state={selected:'a/n.md',currentDir:'a',focused:'a/n.md',expanded:new Set(['a','a/empty']),membership:new Set(['a/n.md']),listedPaths:new Set(['a/n.md']),rdChunks:[{path:'a/n.md',identity:9}],traceChunk:{path:'a/n.md'},traceSource:{path:'a/n.md'},addSource:{path:'a/n.md'},segEditChunk:{path:'a/n.md'},undo:null,viewer:0}
  const setter=k=>v=>state[k]=typeof v==='function'?v(state[k]):v
  const ctx={createSignal:v=>{let s=v;return [()=>s,v=>s=typeof v==='function'?v(s):v]},remapPath,srcCache:new Map([[42,{path:'a/n.md'}]]),traceSource:()=>state.traceSource,setTraceOpen(){}}
  for(const k of ['rdChunks','traceChunk','traceSource','addSource','segEditChunk','listedPaths'])ctx['set'+k[0].toUpperCase()+k.slice(1)]=setter(k)
  const start=app.indexOf('  const [sourceGeneration, setSourceGeneration]'),end=app.indexOf('  onMount(() => { window.addEventListener',start)
  const parent=actual(app.slice(start,end),ctx,'({relocateSources})')
  let events=0,invalidated=[]
  const fctx={remapPath,window:{dispatchEvent:e=>{events++;parent.relocateSources(e)}},CustomEvent:class{constructor(name,options){this.detail=options.detail}},scrollPositions:new Map([['a/n.md',120]]),invalidateFileCache:p=>invalidated.push(p),setViewerVersion:setter('viewer')}
  for(const k of ['selected','currentDir','focused','expanded','membership'])fctx['set'+k[0].toUpperCase()+k.slice(1)]=setter(k)
  const fs=files.indexOf('  const applyRelocation ='),fe=files.indexOf('  const operations =',fs)
  const apply=actual('let previewReadyPath=null;'+files.slice(fs,fe),fctx,'applyRelocation')
  const store=new Map(),storage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
  let statusDown=true,ids=[],calls=0
  const result={status:'complete',old_path:'a',path:'dest/a',operationId:'fixture-'+mode}
  const api={filesMove:async(p,v,id)=>{calls++;ids.push(id);throw new Error(mode)},filesRename:async()=>{throw new Error('unexpected')},filesOperation:async id=>{ids.push(id);if(statusDown)throw new Error('connection unavailable');return result}}
  const ops=new FileOperations(api,storage,(r,old)=>{apply(old,r.path);state.undo=r.operationId})
  await assert.rejects(ops.run('move','a','dest'),/待确认/)
  assert.equal(state.selected,'a/n.md');assert.equal(events,0);assert(ops.hasPending())
  statusDown=false
  await Promise.all([ops.reconcile(),ops.reconcile()])
  for(const k of ['selected','focused'])assert.equal(state[k],'dest/a/n.md')
  assert.equal(state.currentDir,'dest/a');assert(state.expanded.has('dest/a/empty'))
  for(const k of ['traceChunk','traceSource','addSource','segEditChunk'])assert.equal(state[k].path,'dest/a/n.md')
  assert.equal(state.rdChunks[0].path,'dest/a/n.md');assert.equal(state.rdChunks[0].identity,9)
  assert.equal(ctx.srcCache.get(42).path,'dest/a/n.md');assert(state.listedPaths.has('dest/a/n.md'));assert(state.membership.has('dest/a/n.md'))
  assert.equal(fctx.scrollPositions.get('dest/a/n.md'),120);assert.deepEqual(invalidated,['a','dest/a'])
  assert.equal(state.undo,result.operationId);assert.equal(events,1);assert.equal(calls,1);assert.equal(new Set(ids).size,1)
  await ops.reconcile();assert.equal(events,1);assert.equal(store.size,0)
  console.log('PASS F1 '+mode+' uncertain→refresh→canonical actual tree/parent/cache/undo mapping once')
 }
 // A committed response lost in transit is confirmed immediately, not reported failed.
 const storage={getItem:()=>null,setItem(){},removeItem(){}}
 let commits=0
 const ops=new FileOperations({filesMove:async()=>{throw new Error('lost')},filesRename:async()=>{throw new Error('unused')},filesOperation:async()=>({status:'complete',path:'b',old_path:'a',operationId:'confirmed'})},storage,()=>commits++)
 assert.equal((await ops.run('move','a','b')).path,'b');assert.equal(commits,1)
 console.log('PASS F1 immediate lost-response confirmation is success')
 // A remounted screen reconciles its own saved intent. A delayed old response
 // cannot replay the parent mapping or erase the newer request/undo state.
 const store=new Map(),shared={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
 const delayed=deferred(),next=deferred();let n=0,ids=[],applied=[]
 const transport={filesMove:async(p,v,id)=>{ids.push(id);return ++n===1?delayed.promise:next.promise},filesRename:async()=>{},filesOperation:async()=>({status:'complete',old_path:'a',path:'b/a',operationId:'first'})}
 const firstClient=new FileOperations(transport,shared,r=>applied.push(r.operationId))
 const inFlight=firstClient.run('move','a','b')
 const secondClient=new FileOperations(transport,shared,r=>applied.push(r.operationId))
 await secondClient.reconcile()
 const secondFlight=secondClient.run('move','b/a','c')
 delayed.resolve({old_path:'a',path:'b/a',operationId:'first'});await inFlight
 assert.deepEqual(applied,['first']);assert.equal(JSON.parse([...store.values()][0]).id,ids[1])
 next.resolve({old_path:'b/a',path:'c/a',operationId:'second'});await secondFlight
 assert.deepEqual(applied,['first','second']);assert.equal(store.size,0)
 console.log('PASS F1 remount recovery + late duplicate cannot replay/erase/reorder newer intent')
 const retryStore=new Map(),retryStorage={getItem:k=>retryStore.get(k)??null,setItem:(k,v)=>retryStore.set(k,v),removeItem:k=>retryStore.delete(k)}
 let retryIds=[],arrived=false
 const retryApi={filesMove:async(p,v,id)=>{retryIds.push(id);if(!arrived)throw new Error('never reached server');return {old_path:p,path:'dest/a',operationId:'retried'}},filesRename:async()=>{},filesOperation:async()=>({status:'not_found'})}
 const retryOps=new FileOperations(retryApi,retryStorage,()=>{})
 await assert.rejects(retryOps.run('move','a','dest'),/待确认/);arrived=true;await assert.rejects(retryOps.reconcile(),/重新操作/)
 assert.equal(retryIds.length,1);await retryOps.run('move','a','dest')
 assert.equal(retryIds.length,2);assert.equal(new Set(retryIds).size,2)
 console.log('PASS F1 missing request requires renewed user action; refresh never moves a replacement')
 const conflictOps=new FileOperations({...retryApi,filesMove:async()=>{throw new ApiError('目标已存在',409)}},retryStorage,()=>{throw new Error('must not map')})
 await assert.rejects(conflictOps.run('move','a','dest'),/目标已存在/);assert.equal(conflictOps.hasPending(),false)
 console.log('PASS F1 definitive conflict clears intent without remapping')
} else if(which==='F4') {
 const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
 const start=source.indexOf('  let sourceGeneration = 0')>=0?source.indexOf('  let sourceGeneration = 0'):source.indexOf('  const [sourceGeneration, setSourceGeneration]')
 const end=source.indexOf('  onMount(() => { window.addEventListener',start)
 const effectStart=source.lastIndexOf('  createEffect(() => {',source.indexOf('    const generation = sourceGeneration'))
 const effectEnd=source.indexOf('  const traceAvailable',effectStart)
 for(const matching of [false,true]) await new Promise((resolve,reject)=>createRoot(dispose=>{
  const requests=[];const card={noteId:42};const [trace,setTraceSource]=createSignal(null)
  const chunks=[{path:'a/note.md',identity:99}];const caches=new Map();let rd=chunks
  const ctx={createSignal,createEffect,screenCard:()=>card,readingMode:()=>true,srcCache:caches,api:{readingSource:id=>{const d=deferred();requests.push({id,...d});return d.promise}},setTraceSource,traceSource:trace,setRdChunks:fn=>rd=fn(rd),setTraceChunk(){},setAddSource(){},setSegEditChunk(){},setListedPaths(){},setTraceOpen(){},remapPath:(p,o,n)=>p===o||p.startsWith(o+'/')?n+p.slice(o.length):p}
  const handlers=actual(source.slice(start,end)+source.slice(effectStart,effectEnd),ctx,'({relocateSources})')
  ;(async()=>{
   await tick();assert.equal(requests.length,1)
   handlers.relocateSources({detail:{oldPath:matching?'a':'unrelated',newPath:'dest'}})
   await tick();assert.equal(requests.length,2,'same card must issue replacement lookup')
   requests[0].resolve({path:'a/note.md'});await tick();assert.equal(trace(),null)
   requests[1].resolve({path:matching?'dest/note.md':'a/note.md'});await tick()
   assert.equal(trace().path,matching?'dest/note.md':'a/note.md');assert.equal(rd[0].identity,99)
   assert.equal(rd[0].path,matching?'dest/note.md':'a/note.md');assert.equal(card.noteId,42)
   dispose();resolve()
  })().catch(e=>{dispose();reject(e)})
 }))
 console.log('PASS F4 actual reactive source effect: delayed lookup + unrelated/matching relocation, same card, stale rejection')
} else if(which==='F3') {
 const source=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
 const start=source.indexOf('  // Pointer threshold'),end=source.indexOf('  const contextMenu',start)
 let drag=null,drop=null,moves=[],folder='',captured=false,hit=true
 const listeners=new Map(),mounts=[]
 const window={addEventListener:(n,fn)=>listeners.set(n,fn),removeEventListener(){}}
 const document={hidden:false,addEventListener:(n,fn)=>listeners.set(n,fn),removeEventListener(){},elementFromPoint:()=>hit?{closest:()=>({dataset:{folder}})}:null}
 let captures=0
 const row={setPointerCapture(){captured=true;captures++},hasPointerCapture:()=>captured,releasePointerCapture(){captured=false}}
 class Element { constructor(selector=null){this.selector=selector} matches(selectors){return selectors.split(',').map(s=>s.trim()).includes(this.selector)} }
 const ctx={Element,createSignal:v=>{let value=v;return [()=>value,x=>value=x]},window,document,onMount:fn=>mounts.push(fn),onCleanup(){},busy:()=>false,validMove:(p,d,dest)=>dest!==p&&dest!=='a',executeMove:async(t,d)=>moves.push([t.path,d]),setDirOpen(){},showSnack(){},requestAnimationFrame:()=>1,cancelAnimationFrame(){},setTimeout:fn=>{fn();return 1},clearTimeout(){}}
 const h=actual(source.slice(start,end),ctx,'({pointerDown,pointerMove,pointerUp,getClickSuppressed:()=>suppressClick})');mounts.forEach(f=>f())
 const event=(extra={})=>({button:0,buttons:1,pointerType:'mouse',pointerId:1,clientX:10,clientY:10,currentTarget:row,target:{closest:()=>null},composedPath:()=>[new Element(),row],preventDefault(){},...extra})
 // Actual pointerDown: menus/controls in real shadow/slotted paths never
 // create a candidate or capture. Pointermove after their click cannot move.
 for(const tag of ['md-menu','md-menu-item','md-icon-button','button','a','input','textarea','select','chevron','contenteditable']) {
  const before=captures
  h.pointerDown(event({composedPath:()=>[new Element(),new Element(tag==='chevron'?'.files-tree-chevron':tag==='contenteditable'?'[contenteditable]:not([contenteditable="false"])':tag),row]}),{path:'a/n.md',isDir:false})
  assert.equal(captured,false,tag+' captured row');assert.equal(captures,before,tag+' requested capture')
  h.pointerMove(event({clientX:30}));h.pointerUp(event({buttons:0}));assert.equal(moves.length,0,tag+' created drag candidate')
 }
 h.pointerDown(event(),{path:'a/n.md',isDir:false});assert.equal(captured,false,'ordinary pointerdown captures before threshold')
 h.pointerMove(event({clientX:12}));assert.equal(captured,false,'sub-threshold movement captures')
 h.pointerUp(event({buttons:0}));assert.equal(moves.length,0,'ordinary row click moves')
 for(const cancel of ['blur','visibilitychange','lostpointercapture','buttons','outside']) {
  h.pointerDown(event(),{path:'a/n.md',isDir:false});h.pointerMove(event({clientX:30}));assert.equal(captured,true,'drag threshold acquires capture')
  if(cancel==='buttons') h.pointerMove(event({buttons:0}))
  else if(cancel==='outside'){hit=false;h.pointerUp(event({buttons:0}));hit=true}
  else {document.hidden=cancel==='visibilitychange';listeners.get(cancel)?.(event());document.hidden=false}
  h.pointerMove(event({buttons:0}));h.pointerUp(event({buttons:0}));assert.equal(moves.length,0,cancel+' leaves candidate')
 }
 h.pointerDown(event(),{path:'a/n.md',isDir:false});h.pointerMove(event({clientX:30}));h.pointerUp(event({buttons:0}));assert.deepEqual(moves,[['a/n.md','']])
 // Run all queued timeout callbacks (fixture setTimeout is immediate) before
 // the actual post-pointerup click; suppression must survive that boundary.
 assert.equal(h.getClickSuppressed(),true,'post-drag suppression expired before native click')
 let prevented=false,stopped=false
 const ghost={detail:1,preventDefault(){prevented=true},stopImmediatePropagation(){stopped=true}}
 listeners.get('click')(ghost)
 assert(prevented&&stopped);assert.equal(h.getClickSuppressed(),false)
 h.pointerDown(event(),{path:'a/n.md',isDir:false});h.pointerMove(event({clientX:30}));h.pointerUp(event({buttons:0}))
 prevented=false;stopped=false;listeners.get('click')({...ghost,detail:0})
 assert.equal(prevented,false,'keyboard activation swallowed');assert.equal(stopped,false)
 listeners.get('pointerdown')(event())
 assert.equal(h.getClickSuppressed(),false,'fresh pointerdown did not release suppression')
 listeners.get('click')(ghost);assert.equal(stopped,false,'ordinary next pointer click swallowed')
 console.log('PASS F3 interactive composed paths excluded, capture only beyond threshold; actual drag handlers: blur/hidden/lostcapture/buttons=0/outside release cancel; next click safe; root drop works')
}
