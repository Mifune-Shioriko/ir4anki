import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { createRoot, createSignal, createEffect } from 'solid-js'
const which=process.argv[2]??new URL(import.meta.url).searchParams.get('case')
// Default runs the three named cases in isolated module scopes; no empty PASS.
if(!which){const runs=[];for(const name of ['F1','F3','F4'])try{await import(new URL('?case='+name,import.meta.url));runs.push({case:name,status:'PASS'})}catch(e){runs.push({case:name,status:'FAIL',stack:e.stack})}await new Promise(resolve=>process.stdout.write(JSON.stringify(runs,null,2)+'\n',resolve));process.exit(runs.some(r=>r.status==='FAIL')?1:0)}
assert.ok(['F1','F3','F4'].includes(which),'Unknown findings case')

const compile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const actual=(s,ctx,ret)=>new Function(...Object.keys(ctx),compile(s)+`;return ${ret}`)(...Object.values(ctx))
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve}}
function handler(source,name){const ast=ts.createSourceFile('FilesScreen.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let code;function walk(n){if(ts.isFunctionDeclaration(n)&&n.name?.text===name)code=n.getText(ast);if(ts.isVariableDeclaration(n)&&n.name.getText(ast)===name&&n.initializer)code='const '+name+' = '+n.initializer.getText(ast);ts.forEachChild(n,walk)}walk(ast);assert.ok(code,'Actual handler absent: '+name);return code}
function attribute(source,name){const ast=ts.createSourceFile('FilesScreen.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let code;function walk(n){if(ts.isJsxAttribute(n)&&n.name.getText(ast)===name)code=n.initializer.expression.getText(ast);ts.forEachChild(n,walk)}walk(ast);assert.ok(code,name);return code}
const tick=()=>new Promise(r=>setTimeout(r,0))
if(which==='F1') {
 const {FileOperations}=await import('../src/lib/fileOperations.ts')
 const {ApiError}=await import('../src/api.ts')
 const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
 const files=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
 const remapPath=(p,o,n)=>p===null?null:p===o||p.startsWith(o+'/')?n+p.slice(o.length):p
 for(const mode of ['lost-response','FS-recovery','DB-recovery','flow-recovery']) {
  const state={selected:new Set(['a/n.md']),currentDir:'a',focused:'a/n.md',history:['a','a/empty'],preview:'a/n.md',files:[{path:'a/n.md'}],dirs:['a','a/empty'],membership:new Set(['a/n.md']),listedPaths:new Set(['a/n.md']),rdChunks:[{path:'a/n.md',identity:9}],traceChunk:{path:'a/n.md'},traceSource:{path:'a/n.md'},addSource:{path:'a/n.md'},segEditChunk:{path:'a/n.md'},undo:null,viewer:0}
  const setter=k=>v=>state[k]=typeof v==='function'?v(state[k]):v
  const ctx={createSignal:v=>{let s=v;return [()=>s,v=>s=typeof v==='function'?v(s):v]},remapPath,srcCache:new Map([[42,{path:'a/n.md'}]]),traceSource:()=>state.traceSource,setTraceOpen(){}}
  for(const k of ['rdChunks','traceChunk','traceSource','addSource','segEditChunk','listedPaths'])ctx['set'+k[0].toUpperCase()+k.slice(1)]=setter(k)
  const start=app.indexOf('  const [sourceGeneration, setSourceGeneration]'),end=app.indexOf('  onMount(() => { window.addEventListener',start)
  const parent=actual(app.slice(start,end),ctx,'({relocateSources})')
  let events=0,invalidated=[]
  const fctx={remapPath,window:{dispatchEvent:e=>{events++;parent.relocateSources(e)}},CustomEvent:class{constructor(name,options){this.detail=options.detail}},scrollPositions:new Map([['a/n.md',120]]),invalidateFileCache:p=>invalidated.push(p),alive:true,owner:{},requestDraftLeave:()=>true,previewPath:()=>state.preview,setPreviewPath:setter('preview'),setHistory:setter('history'),setFiles:setter('files'),setDirs:setter('dirs'),setVersion:setter('viewer')}
  for(const k of ['selected','currentDir','focused','membership'])fctx['set'+k[0].toUpperCase()+k.slice(1)]=setter(k)
  const apply=actual(handler(files,'applyRelocation'),fctx,'applyRelocation')
  const store=new Map(),storage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
  let statusDown=true,ids=[],calls=0
  const result={status:'complete',old_path:'a',path:'dest/a',operationId:'fixture-'+mode}
  const api={filesMove:async(p,v,id)=>{calls++;ids.push(id);throw new Error(mode)},filesRename:async()=>{throw new Error('unexpected')},filesOperation:async id=>{ids.push(id);if(statusDown)throw new Error('connection unavailable');return result}}
  const ops=new FileOperations(api,storage,(r,old)=>{apply(old,r.path);state.undo=r.operationId})
  await assert.rejects(ops.run('move','a','dest'),/待确认/)
  assert.deepEqual([...state.selected],['a/n.md']);assert.equal(events,0);assert(ops.hasPending())
  statusDown=false
  await Promise.all([ops.reconcile(),ops.reconcile()])
  assert.deepEqual([...state.selected],['dest/a/n.md']);assert.equal(state.focused,'dest/a/n.md');assert.equal(state.preview,'dest/a/n.md')
  assert.equal(state.currentDir,'dest/a');assert.deepEqual(state.history,['dest/a','dest/a/empty']);assert.equal(state.files[0].path,'dest/a/n.md');assert.deepEqual(state.dirs,['dest/a','dest/a/empty'])
  for(const k of ['traceChunk','traceSource','addSource','segEditChunk'])assert.equal(state[k].path,'dest/a/n.md')
  assert.equal(state.rdChunks[0].path,'dest/a/n.md');assert.equal(state.rdChunks[0].identity,9)
  assert.equal(ctx.srcCache.get(42).path,'dest/a/n.md');assert(state.listedPaths.has('dest/a/n.md'));assert(state.membership.has('dest/a/n.md'))
  assert.equal(state.viewer,1,'Current preview/cache reload generation advances once');assert.deepEqual(invalidated,['a','dest/a'])
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
 // Current design uses native HTML drag events; browser pointer capture and
 // outside/blur delivery are UA-owned. Exercise actual application handlers,
 // not the removed custom pointerDown/pointerMove/pointerUp implementation.
 const {normalizeTargets,moveError,editingEvent}=await import('../src/lib/files-browser.ts')
 const source=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
 class Element {constructor(selector=''){this.selector=selector}matches(s){return s.split(',').map(x=>x.trim()).includes(this.selector)}closest(s){return this.selector==='.files-menu'&&s.includes('.files-menu')?this:null}}
 globalThis.Element=Element
 let drag=[],drop=null,selected=new Set(['a/n.md']),focused='a/n.md',blocked=false,allow=true,external=false,previewed=0
 const moves=[],messages=[],entries=[{path:'a/n.md',isDir:false}],row={}
 const ctx={normalizeTargets,moveError,editingEvent,all:()=>entries,rows:()=>entries,selected:()=>selected,targets:()=>entries.filter(t=>selected.has(t.path)),setSelected:v=>selected=typeof v==='function'?v(selected):v,focused:()=>focused,setFocused:v=>focused=v,busy:()=>blocked,requestDraftLeave:()=>allow,setDrag:v=>drag=v,drag:()=>drag,setDrop:v=>drop=v,setExternalDrop:v=>external=v,setMessage:v=>messages.push(v),relocation:async(kind,items,dest)=>moves.push([items.map(t=>t.path),dest]),dialog:()=>null,drawer:()=>false,previewPath:()=>null,preview:()=>previewed++}
 const code= ['startDrag','over','dropped','key','choose'].map(n=>handler(source,n)).join('\n')+`;const endDrag=${attribute(source,'onDragEnd')};const freshPointer=${attribute(source,'onPointerDown')}`
 const h=actual('let suppressClick=false,anchor="";'+code,ctx,'({startDrag,over,dropped,key,choose,endDrag,freshPointer,suppressed:()=>suppressClick})')
 const event=(types=[])=>({types,dataTransfer:{types,effectAllowed:'',dropEffect:'',setData(k,v){this[k]=v}},preventDefault(){this.prevented=true},stopPropagation(){},composedPath:()=>[],key:'Escape'})
 // Keyboard/menu controls must not become Files keyboard operations, including
 // Material shadow paths. The old tree chevron is now a directory button.
 for(const tag of ['md-menu','md-menu-item','md-icon-button','button','a','input','textarea','select','[contenteditable]:not([contenteditable="false"])','.files-menu']){
  const e={...event(),key:'Enter',composedPath:()=>[new Element(),new Element(tag),row]};h.key(e);assert.equal(previewed,0,tag+' escaped editing scope');assert.equal(moves.length,0)
 }
 for(const guard of ['busy','unsafe-draft']){blocked=guard==='busy';allow=guard!=='unsafe-draft';const e=event();h.startDrag(e,entries[0]);assert.equal(e.prevented,true);assert.equal(drag.length,0)}blocked=false;allow=true
 // No drop callback means no mutation. Native dragend and application Escape
 // both clear the candidate so a later stale drop cannot move anything.
 for(const cancel of ['blur','visibilitychange','lostpointercapture','buttons','outside']){
  h.startDrag(event(),entries[0]);assert.equal(drag.length,1);assert.equal(moves.length,0);h.endDrag();assert.equal(drag.length,0);assert.equal(drop,null);h.dropped(event(),'');assert.equal(moves.length,0,cancel+' stale drop dispatched')
 }
 h.startDrag(event(),entries[0]);h.key(event());h.dropped(event(),'');assert.equal(moves.length,0,'Escape did not revoke drag')
 selected=new Set(['a/n.md']);h.startDrag(event(),entries[0]);const invalid=event();h.over(invalid,'a');assert.equal(invalid.dataTransfer.dropEffect,'none');h.dropped(invalid,'a');assert.equal(moves.length,0);assert(messages.length)
 h.startDrag(event(),entries[0]);const valid=event();h.over(valid,'');assert.equal(valid.dataTransfer.dropEffect,'move');h.dropped(valid,'');assert.deepEqual(moves,[[['a/n.md'],'']]);assert.equal(h.suppressed(),true)
 const before=new Set(selected);h.choose(entries[0]);assert.deepEqual(selected,before);assert.equal(h.suppressed(),false,'Post-drag click suppression must clear after consuming ghost click')
 h.startDrag(event(),entries[0]);h.endDrag();h.key({...event(),key:'Enter'});assert.equal(previewed,1,'Keyboard activation swallowed by drag click suppression');h.freshPointer();assert.equal(h.suppressed(),false)
 const ext=event(['Files']);h.over(ext,'');assert.equal(ext.dataTransfer.dropEffect,'none');assert.equal(external,true);h.dropped(ext,'');assert.equal(moves.length,1,'External file drop moved internal entry')
 console.log('PASS F3 actual native-handler guards, composed keyboard controls, cancel/end stale-drop rejection, invalid/root/external drop and ghost/fresh-pointer/keyboard activation. UA drag delivery remains browser-gated.')
}
