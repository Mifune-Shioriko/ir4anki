import assert from 'node:assert/strict'
import {webcrypto} from 'node:crypto'
import {createSignal,Show} from 'solid-js'
import {root,render,insert,createElement,createComponent,find} from './solid-lifecycle-renderer.mjs'
import {PageAnnotation} from '../src/components/PageAnnotation.tsx'
import {OcclusionEditor} from '../src/components/OcclusionEditor.tsx'
import {createStateGeneration} from '../src/lib/state-generation.ts'
globalThis.crypto??=webcrypto
const store=new Map();globalThis.localStorage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
globalThis.window={addEventListener(){},removeEventListener(){}}
// No API responses synthesized: pending I/O never participates in lifecycle assertions.
globalThis.fetch=()=>new Promise(()=>{})
const source={kind:'pdf',document_id:'doc',revision_id:'rev',segment_id:'seg',page:4,page_start:1,page_end:4}
const draft={authoring_id:'author',source,version:2,published_version:2,raster:{raster_hash:'hash',width:100,height:100},crop:[0,0,1,1],masks:[{mask_id:'mask',rect:[.1,.1,.2,.2]}],question:'',answer:'',mode:'all',note_links:[]}
const checks=[]
function test(name,fn){checks.push([name,fn])}
test('source clearing unmounts actual PageAnnotation without re-reading invalid spread props',()=>{
 const host=root(),[current,setCurrent]=createSignal(source),notifications=[]
 // Mirrors PdfSourceViewer -> dynamic spread -> ReadingPanel.source getter:
 // callback lookup re-evaluates a source-dependent prop even though absent.
 const notify=value=>notifications.push(value)
 const dispose=render(()=>createComponent(Show,{get when(){return current()},children:s=>createComponent(PageAnnotation,{source:s(),get onEditingChange(){return current().kind==='pdf'?notify:undefined}})}),host)
 assert.ok(find(host,n=>n.props['aria-label']==='第 4 页理解笔记'))
 assert.doesNotThrow(()=>setCurrent(null));assert.equal(host.children.length,0);assert.deepEqual(notifications,[false,false]);dispose()
})
test('authoritative refresh keeps actual mounted editor; close callback removes exactly current instance',async()=>{
 const host=root(),authority=createStateGeneration(),[phase,setPhase]=createSignal('reading'),[current,setCurrent]=createSignal(source),[editing,setEditing]=createSignal(true)
 let closed=0
 const dispose=render(()=>createComponent(Show,{get when(){return phase()==='reading'},get children(){
  const parent=createElement('main')
  insert(parent,createComponent(Show,{get when(){return current()},children:s=>createComponent(PageAnnotation,{source:s()})}))
  insert(parent,createComponent(Show,{get when(){return editing()},get children(){return createComponent(OcclusionEditor,{initial:draft,onPublished(){},onClose(){closed++;setEditing(false)}})}}))
  return parent
 }}),host)
 const editor=find(host,n=>n.props['aria-label']==='图片遮罩编辑器');assert.ok(editor)
 const before=JSON.parse(editor.props['data-writer-state']);assert.equal(before.version,2)
 // App adoptState currently passes through loadBatch(review) then flow(reading).
 // It must be one authority transaction, not two observable UI stages.
 for(const mutation of [false,true]){
  const ticket=mutation?authority.mutate():authority.begin()
  authority[mutation?'applyMutation':'apply'](ticket,()=>{setPhase('review');setCurrent(null);setCurrent({...source});setPhase('reading')})
  assert.equal(find(host,n=>n.props['aria-label']==='图片遮罩编辑器'),editor,'same-source resync must preserve component and writer identity')
  assert.equal(JSON.parse(editor.props['data-writer-state']).disposed,false)
  assert.equal(editor.props['data-page'],4)
 }
 const close=find(editor,n=>n.tag==='md-text-button'&&find(n,x=>x.text==='保存草稿并返回'));assert.ok(close)
 close.props.onClick();await new Promise(setImmediate)
 assert.equal(closed,1);assert.equal(find(host,n=>n.props['aria-label']==='图片遮罩编辑器'),undefined)
 dispose()
})
let failures=0
for(const [name,fn] of checks)try{await fn();console.log('PASS',name)}catch(e){failures++;console.error('FAIL',name,e)}
process.exitCode=failures?1:0
