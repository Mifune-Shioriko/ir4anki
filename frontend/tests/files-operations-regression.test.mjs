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
// AST extraction tolerates function declarations and formatting while executing
// the actual current handler bodies. This is a pure handler test, not DOM/native QA.
const sourceAst=ts.createSourceFile('FilesScreen.tsx',screen,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
function actual(name,ctx){let code
 function walk(n){if(ts.isVariableDeclaration(n)&&n.name.getText(sourceAst)===name&&n.initializer)code=`const ${name} = ${n.initializer.getText(sourceAst)}`
 else if(ts.isFunctionDeclaration(n)&&n.name?.text===name)code=n.getText(sourceAst)
 ts.forEachChild(n,walk)}walk(sourceAst);assert.ok(code,'Actual handler absent: '+name);return make(code,ctx,name)}
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
 let preview=selected
 const fc={alive:true,owner:{},remapPath,invalidateFileCache:p=>invalidated.push(p),setSelected:f=>selected=[...f(new Set([selected]))][0],setCurrentDir(){},setFocused(){},previewPath:()=>preview,setPreviewPath:f=>preview=f(preview),requestDraftLeave:()=>true,setMessage(){},setHistory(){},setMembership(){},setFiles(){},setDirs(){},setVersion(){},CustomEvent:class{constructor(n,o){this.detail=o.detail}},window:{dispatchEvent:e=>{relocateSources(e);parent=parentState.rdChunks[0].path;events.push(e.detail)}}}
 const applyRelocation=actual('applyRelocation',fc)
 const construct=()=>actual('operations',{alive:true,FileOperations,api,sessionStorage:storage,applyRelocation,undoId:()=>id,setUndoId:v=>id=v,remember(){},setOperationVersion(){}})
 const ops=construct()
 const ctx={alive:true,undoId:()=>id,busy:()=>false,requestDraftLeave:()=>true,setWorking:v=>busy=v,operations:ops,setMessage(){},reload:async()=>{try{await ops.reconcile()}catch{}}}
 const undo=actual('undo',ctx)
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
 const operations={isBusy:()=>true}
 const context={busy:()=>operations.isBusy(),requestDraftLeave:()=>true,api:{filesUpload:()=>{mutations++;throw Error('upload must not run')},filesDelete:()=>{mutations++;throw Error('delete must not run')}}}
 const upload=actual('importFiles',context)
 await upload({files:[{name:'a.md'}],value:'a.md'})
 const runItems=actual('runItems',context)
 await actual('remove',{...context,runItems})([{path:'a.md',isDir:false}])
 assert.equal(mutations,0)

})
await check('disposed relocation acknowledges original cache/source without view writes',async()=>{
 const invalidated=[],events=[],owner={}
 const ack=actual('applyRelocation',{alive:false,owner,invalidateFileCache:p=>invalidated.push(p),CustomEvent:class{constructor(type,value){this.type=type;this.detail=value.detail}},window:{dispatchEvent:event=>events.push(event)}})
 ack('original/a.md','confirmed/a.md')
 assert.deepEqual(invalidated,['original/a.md','confirmed/a.md']);assert.equal(events.length,1);assert.equal(events[0].detail.oldPath,'original/a.md');assert.equal(events[0].detail.owner,owner)
 // No report/setter/dispatch-next capability was supplied; any access fails.
})
await check('generic upload conflict keeps captured folder and explicit retry policy',async()=>{
 const {ApiError,UploadConflictError,UploadPartialError}=await import('../src/api.ts')
 const {SerialBatch}=await import('../src/lib/files-browser.ts');globalThis.crypto??=(await import('node:crypto')).webcrypto
 const requests=[],conflicts=[];let reject=true
 const context={alive:true,busy:()=>false,requestDraftLeave:()=>true,setWorking(){},setMessage(){},setImportConflict:x=>conflicts.push(x),UploadConflictError,UploadPartialError,ApiError,
  batch:new SerialBatch(()=>{}),
  api:{async filesUpload(entries,dir,policy){requests.push({entries,dir,policy});if(reject)throw new UploadConflictError(JSON.stringify({conflicts:['note.md']}));return {saved:[{path:dir+'/note.md',action:'skipped'}],images_renamed:{}}}},
  invalidateFileCache(){},window:{dispatchEvent(){}},Event:class{},setVersion(){},reload:async()=>{}}
 const importing=actual('importEntries',context),entries=[{file:{name:'note.md'},rel:'note.md'}]
 await importing(entries,'original-folder','error')
 assert.equal(requests.length,1);assert.equal(conflicts.length,1);assert.equal(conflicts[0].dir,'original-folder');assert.deepEqual(conflicts[0].names,['note.md'])
 reject=false
 for(const policy of ['skip','rename','overwrite'])await importing(conflicts[0].entries,conflicts[0].dir,policy)
 assert.deepEqual(requests.map(x=>x.policy),['error','skip','rename','overwrite']);assert.ok(requests.every(x=>x.dir==='original-folder'))
})
console.log(JSON.stringify(results.slice(2),null,2))
process.exitCode=results.some(r=>r.status==='FAIL')?1:0
