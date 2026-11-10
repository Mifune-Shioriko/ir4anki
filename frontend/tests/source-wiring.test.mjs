import assert from 'node:assert/strict'
import {batch,createSignal} from 'solid-js'
import {render,root,createComponent,find} from './solid-lifecycle-renderer.mjs'
import {SidePanelTabs} from '../src/components/SidePanelTabs.tsx'
import {NotePanel} from '../src/components/NotePanel.tsx'
import {events} from './source-leaf-probe.mjs'
const chunk=(doc,extra={})=>({kind:'pdf',path:doc+'.pdf',document_id:doc,revision_id:'rev',segment_id:'seg',chunk_key:'seg',page_start:2,page_end:4,page_count:54,version:1,heading_path:[],cards_created:[],ancestor_cards:[],breadcrumb:[],status:'todo',...extra})
const a=chunk('A'),b=chunk('B'),requests=[]
// Math/layout RAF belongs to the replaced browser host, not these source lifetimes.
globalThis.requestAnimationFrame=()=>0;globalThis.cancelAnimationFrame=()=>{}
globalThis.fetch=async url=>{
 requests.push(String(url));if(String(url).startsWith('/api/reading/source?')){const id=new URL(String(url),'http://fixture').searchParams.get('note_id');return new Response(JSON.stringify(id==='1'?a:b),{status:200})}
 return new Response(JSON.stringify({cards:[]}),{status:200})
}
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate)}
let failures=0
async function test(name,fn){try{await fn();console.log('PASS',name)}catch(error){failures++;console.error('FAIL',name,error)}}
await test('actual reading panels -> SourceViewer survive atomic PDF-to-review clearing',()=>{
 const host=root(),[current,setCurrent]=createSignal(a),[blocked,setBlocked]=createSignal(false)
 const dispose=render(()=>createComponent(SidePanelTabs,{get chunk(){return current()},noteId:null,cardKey:null,get blocked(){return blocked()},refreshToken:0,noteReloadToken:0,active:true}),host)
 assert(find(host,n=>n.tag==='pdf-leaf'))
 assert.doesNotThrow(()=>batch(()=>{setCurrent(null);setBlocked(true)}))
 assert.equal(find(host,n=>n.tag==='pdf-leaf'),undefined)
 assert(find(host,n=>n.text?.includes('先想一想')));dispose()
})
await test('same-document authoritative refresh retains the mounted reference/manual position',()=>{
 const host=root(),[current,setCurrent]=createSignal(a)
 const dispose=render(()=>createComponent(SidePanelTabs,{get chunk(){return current()},noteId:null,cardKey:null,blocked:false,refreshToken:0,noteReloadToken:0,active:true}),host)
 const leaf=find(host,n=>n.tag==='pdf-leaf');leaf.props.manualPosition=4200
 setCurrent({...a,version:2});assert.equal(find(host,n=>n.tag==='pdf-leaf'),leaf);assert.equal(leaf.props.manualPosition,4200)
 assert.doesNotThrow(dispose)
})
await test('source A -> B retires A with valid required props, then mounts exact B',()=>{
 const host=root(),[current,setCurrent]=createSignal(a)
 const dispose=render(()=>createComponent(SidePanelTabs,{get chunk(){return current()},noteId:null,cardKey:null,blocked:false,refreshToken:0,noteReloadToken:0,active:true}),host)
 const old=find(host,n=>n.tag==='pdf-leaf'),start=events.length
 assert.doesNotThrow(()=>setCurrent(b));const next=find(host,n=>n.tag==='pdf-leaf')
 assert.notEqual(next,old);assert.equal(next.props.document,'B');assert(events.slice(start).some(e=>e.disposed===old&&e.document==='A'))
 assert.doesNotThrow(()=>setCurrent(null));dispose()
})
await test('real review NotePanel source clearing and A -> B preserve the reveal gate',async()=>{
 const host=root(),[id,setId]=createSignal(1),[blocked,setBlocked]=createSignal(false)
 const dispose=render(()=>createComponent(NotePanel,{get noteId(){return id()},get cardKey(){return id()},get blocked(){return blocked()},active:true}),host)
 await flush();assert.equal(find(host,n=>n.tag==='pdf-leaf').props.document,'A')
 const before=requests.length
 assert.doesNotThrow(()=>batch(()=>{setId(null);setBlocked(true)}));await flush()
 assert.equal(find(host,n=>n.tag==='pdf-leaf'),undefined);assert.equal(requests.length,before)
 assert(find(host,n=>n.text?.includes('先想一想')))
 batch(()=>{setId(2);setBlocked(false)});await flush()
 assert.equal(find(host,n=>n.tag==='pdf-leaf').props.document,'B')
 assert.doesNotThrow(dispose)
})
await test('PDF -> Markdown -> clear keeps valid adapter path/crumb/line props',()=>{
 const host=root(),[current,setCurrent]=createSignal(a)
 const dispose=render(()=>createComponent(SidePanelTabs,{get chunk(){return current()},noteId:null,cardKey:null,blocked:false,refreshToken:0,noteReloadToken:0,active:true}),host)
 assert.doesNotThrow(()=>setCurrent({kind:'md',path:'notes.md',heading_path:['Section'],line_start:2,line_end:8,chunk_key:'md-seg',cards_created:[],ancestor_cards:[]}))
 assert.equal(find(host,n=>n.tag==='pdf-leaf'),undefined);assert.equal(find(host,n=>n.tag==='md-leaf').props.path,'notes.md')
 assert.doesNotThrow(()=>setCurrent(null));assert.equal(find(host,n=>n.tag==='md-leaf'),undefined);dispose()
})
process.exitCode=failures?1:0
