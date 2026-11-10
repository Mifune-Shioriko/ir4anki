// Actual PageAnnotation, Solid lifecycle and writer; universal host is not DOM QA.
import assert from 'node:assert/strict'
import {webcrypto} from 'node:crypto'
import {root,render,createComponent,find} from './solid-lifecycle-renderer.mjs'
import {PageAnnotation} from '../src/components/PageAnnotation.tsx'
import {requestDraftLeave} from '../src/lib/draft-leave.ts'
globalThis.crypto??=webcrypto
const source={kind:'pdf',document_id:'safe-doc',revision_id:'safe-rev',segment_id:'safe-seg',page:1,page_start:1,page_end:2}
const key='ir4anki.annotation.safe-doc.safe-rev.1',exact='# 未保存的精确 Markdown\n\n**中文** $x^2$\n\n末行 🟣'
let stored=JSON.stringify({body:exact,version:'v1',requestId:'restored-request'}),failStore=true,acceptDiscard=false,release
let puts=[]
globalThis.localStorage={getItem:k=>k===key?stored:null,setItem(k,v){if(failStore)throw Error('synthetic quota');if(k===key)stored=v},removeItem(k){if(k===key)stored=null}}
globalThis.window={addEventListener(){},removeEventListener(){},confirm(){return acceptDiscard}}
globalThis.fetch=async(url,opt={})=>{
 if((opt.method??'GET')==='GET')return new Response(JSON.stringify({annotation_id:'safe-annotation',body:'server old',version:'v1'}))
 puts.push(JSON.parse(opt.body));return new Promise(r=>release=r)
}
const host=root();const dispose=render(()=>createComponent(PageAnnotation,{source}),host)
await new Promise(setImmediate)
assert.equal(puts.length,1);assert.equal(puts[0].body,exact)
// Exact source/editor ownership retained across failed navigation requests.
stored=null
assert.equal(requestDraftLeave(),false)
const editor=find(host,n=>n.props['aria-label']==='理解笔记源码编辑器');assert.ok(editor)
assert.equal(find(host,n=>n.props['aria-label']==='第 1 页理解笔记')!==undefined,true)
assert.equal(requestDraftLeave(),false);assert.equal(find(host,n=>n.props['aria-label']==='理解笔记源码编辑器'),editor)
release(new Response(JSON.stringify({detail:'synthetic remote write error'}),{status:503}));await new Promise(setImmediate)
assert.equal(requestDraftLeave(),false);assert.equal(find(host,n=>n.props['aria-label']==='理解笔记源码编辑器'),editor)
// Conflict also retains exact local text and current mounted editor.
const retry=find(host,n=>n.tag==='md-text-button'&&find(n,x=>x.text==='重试保存'));assert.ok(retry);retry.props.onClick();await new Promise(setImmediate)
release(new Response(JSON.stringify({detail:'版本冲突'}),{status:409}));await new Promise(setImmediate)
assert.equal(requestDraftLeave(),false);assert.equal(puts.at(-1).body,exact)
acceptDiscard=true;assert.equal(requestDraftLeave(),true);dispose()
assert.equal(requestDraftLeave(),true,'Unmount unregisters only its own guard')
stored=JSON.stringify({body:exact,version:'v1',requestId:'success-request'});acceptDiscard=false;puts=[]
const next=root(),disposeNext=render(()=>createComponent(PageAnnotation,{source}),next)
await new Promise(setImmediate);stored=null;assert.equal(requestDraftLeave(),false)
release(new Response(JSON.stringify({annotation_id:'safe-annotation',body:exact,version:'v2'})));await new Promise(setImmediate)
assert.equal(puts[0].body,exact);assert.equal(requestDraftLeave(),true);disposeNext()
console.log('PASS actual annotation exact-text guard under delayed/error/conflict/local-storage failure and explicit discard')
