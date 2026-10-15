import assert from 'node:assert/strict'
const { FileOperations } = await import(process.env.OPERATIONS_SOURCE ?? '../src/lib/fileOperations.ts')
const results=[]
async function check(name, fn) { try { await fn(); results.push({name,status:'PASS'}) } catch(e) { results.push({name,status:'FAIL',error:e.message}) } }
await check('storage write failure leaves no dispatchable intent', async()=> {
 const calls=[]
 const api={filesMove:async()=>{calls.push('move');return{}},filesRename:async()=>{},filesOperation:async()=>({status:'not_found'})}
 const storage={getItem:()=>null,setItem:()=>{throw Error('synthetic quota')},removeItem:()=>{}}
 const ops=new FileOperations(api,storage,()=>{})
 await assert.rejects(()=>ops.run('move','a.md','dest'),/synthetic quota/)
 assert.equal(ops.hasPending(),false)
 await ops.reconcile();assert.deepEqual(calls,[])
})
await check('different command cannot receive reconciliation result', async()=> {
 let saved=JSON.stringify({id:'a'.repeat(32),action:'move',path:'a.md',value:'dest'})
 let resolve
 const calls=[]
 const api={filesMove:async(path)=>{calls.push(path);return{ok:true,path:'dest2/b.md',old_path:path,operationId:'b'.repeat(32)}},filesRename:async()=>{},filesOperation:()=>new Promise(r=>{resolve=r})}
 const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v},removeItem:()=>{saved=null}}
 const ops=new FileOperations(api,storage,()=>{})
 const reconciliation=ops.reconcile()
 const next=ops.run('move','b.md','dest2')
 resolve({ok:true,status:'complete',path:'dest/a.md',old_path:'a.md',operationId:'a'.repeat(32)})
 await reconciliation
 let result
 try { result=await next } catch { return }
 assert.equal(result?.old_path,'b.md','unrelated old result returned for b.md')
 assert.deepEqual(calls,['b.md'])
})
console.log(JSON.stringify(results,null,2))
process.exitCode=results.some(r=>r.status==='FAIL')?1:0
// The actual undo handler uses persisted intent and the same commit callback on
// refresh/remount; no study engine, network or collection is involved.
const {readFileSync}=await import('node:fs')
const ts=(await import('typescript')).default
const screen=readFileSync(process.env.FILES_SOURCE ?? new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const make=(code,ctx,name)=>new Function(...Object.keys(ctx),ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+`;return ${name}`)(...Object.values(ctx))
await check('undo lost response then remount maps descendants and clears original undo',async()=>{
 const store=new Map(),storage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
 let online=false,busy=false,id='a'.repeat(32),calls=0,events=[],selected='dest/a/n.md',parent='dest/a/n.md',invalidated=[]
 const result={status:'complete',old_path:'dest/a',path:'a',operationId:'c'.repeat(32)}
 const api={filesUndo:async(original,rid)=>{assert.equal(original,id);assert(storage.getItem('files-pending-relocation-v1'));calls++;throw Error('actual-client synthetic lost')},filesOperation:async()=>{if(!online)throw Error('offline');return result}}
 const commit=(r,old,request)=>{invalidated.push(old,r.path);selected=selected.replace(old,r.path);parent=parent.replace(old,r.path);events.push(r.operationId);if(request.action==='undo'&&id===request.value)id=null}
 const remapPath=(p,o,n)=>p==null?p:p===o||p.startsWith(o+'/')?n+p.slice(o.length):p
 const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
 const parentState={rdChunks:[{path:parent,identity:42}],traceChunk:{path:parent},traceSource:{path:parent},addSource:{path:parent},segEditChunk:{path:parent},listedPaths:new Set([parent])}
 const pc={remapPath,createSignal:v=>{let s=v;return[()=>s,v=>s=typeof v==='function'?v(s):v]},srcCache:new Map([[42,{path:parent}]]),traceSource:()=>parentState.traceSource,setTraceOpen(){}}
 for(const k of Object.keys(parentState))pc['set'+k[0].toUpperCase()+k.slice(1)]=v=>parentState[k]=typeof v==='function'?v(parentState[k]):v
 const pa=app.indexOf('  const [sourceGeneration, setSourceGeneration]'),pb=app.indexOf('  onMount(() => { window.addEventListener',pa)
 const relocateSources=make(app.slice(pa,pb),pc,'relocateSources')
 const fc={remapPath,invalidateFileCache:p=>invalidated.push(p),setSelected:f=>selected=f(selected),setCurrentDir(){},setFocused(){},setExpanded(){},setMembership(){},scrollPositions:new Map(),setViewerVersion(){},CustomEvent:class{constructor(n,o){this.detail=o.detail}},window:{dispatchEvent:e=>{relocateSources(e);parent=parentState.rdChunks[0].path;events.push(e.detail)}}}
 const fa=screen.indexOf('  const applyRelocation ='),fb=screen.indexOf('  const operations =',fa)
 const applyRelocation=make('let previewReadyPath=null;'+screen.slice(fa,fb),fc,'applyRelocation')
 const construction=screen.slice(screen.indexOf('  const operations ='),screen.indexOf('  const undoMove ='))
 const construct=()=>make(construction,{FileOperations,api,sessionStorage:storage,applyRelocation,undoId:()=>id,setUndoId:v=>id=v,setOperationVersion(){}},'operations')
 const ops=construct()
 const ctx={undoId:()=>id,busy:()=>false,setUndoBusy:v=>busy=v,operations:ops,selected:()=>selected,showSnack(){},reload:async()=>ops.reconcile()}
 const start=screen.indexOf('  const undoMove ='),end=screen.indexOf('  const selectFile =',start)
 const undo=make(screen.slice(start,end),ctx,'undoMove')
 await undo();assert.equal(busy,false);assert(ops.hasPending());assert.equal(parent,'dest/a/n.md');assert.equal(calls,1)
 online=true
 const remount=construct();await remount.reconcile()
 assert.equal(selected,'a/n.md');assert.equal(parent,'a/n.md');assert.equal(parentState.rdChunks[0].identity,42);for(const k of ['traceChunk','traceSource','addSource','segEditChunk'])assert.equal(parentState[k].path,'a/n.md');assert.equal(pc.srcCache.get(42).path,'a/n.md');assert.equal(id,null);assert.deepEqual(invalidated,['dest/a','a']);assert.equal(events.length,1);assert.equal(store.size,0)
 await remount.reconcile();assert.equal(events.length,1)
})
await check('undelivered pending intent never moves overwritten replacement on refresh',async()=>{
 const store=new Map(),storage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
 let content='original',calls=0
 const ops=new FileOperations({filesMove:async()=>{calls++;throw Error('never reached server')},filesOperation:async()=>({status:'not_found'})},storage,()=>{throw Error('unexpected commit')})
 await assert.rejects(ops.run('move','a.md','dest'),/待确认/);assert.equal(calls,1)
 content='replacement' // independent external overwrite, never permission to rebase
 await assert.rejects(ops.reconcile(),/重新操作/);assert.equal(calls,1);assert.equal(content,'replacement');assert.equal(ops.hasPending(),false)
})
await check('upload and delete refuse mutation while unresolved intent exists',async()=>{
 let mutations=0
 const context={operations:{isBusy:()=>true},showSnack(){},uploading:()=>false,renameBusy:()=>false,deleteTarget:()=>({path:'a.md'}),deleteCanSubmit:()=>true,UploadConflictError:class{},setUploading(){},setDeleteBusy(){},api:{filesUpload:()=>{mutations++;throw Error('upload must not run')},filesDelete:()=>{mutations++;throw Error('delete must not run')}}}
 const a=screen.indexOf('  const doUpload ='),b=screen.indexOf('\n  const ',a+22)
 // Include exactly the function, up to the next declaration.
 const upload=make(screen.slice(a,b),context,'doUpload');await upload([{file:{},rel:'a.md'}],'','overwrite')
 const c=screen.indexOf('  const submitDelete ='),d=screen.indexOf('\n  type Target',c)
 await make(screen.slice(c,d),context,'submitDelete')();assert.equal(mutations,0)
})
console.log(JSON.stringify(results.slice(2),null,2))
process.exitCode=results.some(r=>r.status==='FAIL')?1:0
