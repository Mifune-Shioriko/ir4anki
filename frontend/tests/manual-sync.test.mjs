import test from 'node:test'
import assert from 'node:assert/strict'
import {api} from '../src/api.ts'
import {readFile} from 'node:fs/promises'
test('learner sync uses restricted local route, confirmation header and explicit undo commit', async () => {
 const old=globalThis.fetch; const calls=[]
 globalThis.fetch=async (...args)=>{calls.push(args);return new Response('{"synced":true}')}
 try { await api.engineSync(); assert.equal(calls.length,1);const [url,init]=calls[0];
 assert.equal(url,'/api/local/sync'); assert.equal(init.headers['X-IR4Anki-Local-Sync'],'confirm');
 assert.equal(init.headers.Authorization,undefined);assert.deepEqual(JSON.parse(init.body),{commit_undo:true})
 } finally {globalThis.fetch=old}
})
test('dialog has no administrator credential input and rail has a labelled icon indicator', async ()=>{
 const dialog=await readFile(new URL('../src/components/ManualSyncDialog.tsx',import.meta.url),'utf8')
 assert.doesNotMatch(dialog,/setEngineToken|type="password"|管理令牌/)
 const rail=await readFile(new URL('../src/components/NavRail.tsx',import.meta.url),'utf8')
 assert.match(rail,/aria-haspopup="dialog"/);assert.match(rail,/<IconSync size=\{24\}/)
})

// Execute the real TSX component with mocked Solid signals/JSX and browser API.
// Browser layout remains covered separately by the Playwright fixture.
import ts from 'typescript'
import vm from 'node:vm'
async function dialogFixture(result, statusOverride = {}) {
 const path=process.env.ORIGINAL_SYNC_DIALOG || new URL('../src/components/ManualSyncDialog.tsx',import.meta.url)
 const source=await readFile(path,'utf8');const signals=[];const mounts=[];const calls=[];let closed=0;let synced=0
 const api={engineStatus:async()=>({backend:'pylib',...statusOverride}),engineSync:async()=>{calls.push('sync');return await result()}}
 const solid={createSignal(value){const state={value};signals.push(state);return [()=>state.value,v=>{state.value=v}]},
 onMount(fn){mounts.push(fn)},onCleanup(){},Show:(p)=>p.when?p.children:null}
 const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,jsxFactory:'h'}}).outputText
 const exports={};vm.runInNewContext(output,{exports,require:name=>name==='solid-js'?solid:{api,setEngineToken:()=>{}},h:(tag,props,...children)=>typeof tag==='function'?tag({...(props||{}),children}):({tag,props:props||{},children})})
 const tree=exports.ManualSyncDialog({onClose:()=>closed++,onSynced:()=>synced++})
 mounts.forEach(fn=>fn());await new Promise(r=>setImmediate(r))
 const nodes=[];function walk(node){if(node&&typeof node==='object'&&node.tag){nodes.push(node);node.children.flat(Infinity).forEach(walk)}}walk(tree)
 return {calls,signals,closed:()=>closed,synced:()=>synced,button:nodes.find(n=>n.tag==='md-filled-button'),cancel:nodes.find(n=>n.tag==='md-text-button')}
}
test('opening and cancelling never sync; direct submit syncs once while busy', async()=>{
 let resolve;const f=await dialogFixture(()=>new Promise(r=>resolve=r))
 assert.equal(f.calls.length,0);f.cancel.props.onClick();assert.equal(f.closed(),1)
 const first=f.button.props.onClick();const duplicate=f.button.props.onClick()
 assert.equal(f.calls.length,1);f.cancel.props.onClick();assert.equal(f.closed(),1)
 assert.ok(f.signals.some(s=>s.value===true));assert.ok(f.signals.some(s=>String(s.value).includes('正在同步集合')))
 resolve({synced:true});await Promise.all([first,duplicate])
 assert.ok(f.signals.some(s=>s.value==='集合与媒体同步完成。'))
 assert.equal(f.synced(),1)
 f.cancel.props.onClick();assert.equal(f.closed(),2)
})
test('failure reports error, clears busy, allows explicit retry without full-sync direction',async()=>{
 const f=await dialogFixture(()=>{throw new Error('full sync needs explicit direction')})
 await f.button.props.onClick()
 assert.equal(f.calls.length,1)
 assert.ok(f.signals.some(s=>String(s.value).includes('同步失败')&&String(s.value).includes('full sync')))
 f.cancel.props.onClick();assert.equal(f.closed(),1)
 await f.button.props.onClick();assert.equal(f.calls.length,2)
})
