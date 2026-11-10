import assert from 'node:assert/strict'
import {sourceKey, sourcePage, currentText, selectionText} from '../src/lib/lecture-session.ts'
assert.equal(sourcePage({page_start:10,page_end:20,page:17}),17)
assert.equal(sourcePage({page_start:10,page_end:20,page:2}),10)
assert.equal(sourcePage({page_start:21,page_end:30}),21)
assert.equal(sourcePage({page_start:21,page_end:30},29),29)
assert.equal(selectionText('saved','',true),'saved')
assert.equal(selectionText('saved','new',false),'new')
assert.equal(selectionText('saved','',false),'')
let release; const old=new Promise(resolve=>release=resolve);let generation=1;const attached=[]
const job=currentText(()=>old,()=>generation===1,value=>attached.push(value))
generation=2;release('old page');await job;assert.deepEqual(attached,[])
await currentText(async()=>'new page',()=>true,value=>attached.push(value));assert.deepEqual(attached,['new page'])
console.log('source page clamping, toolbar selection and delayed text generation: PASS')

const {createRoot,createSignal,createMemo,createComputed,on}=await import('solid-js/dist/solid.js')
createRoot(dispose=>{
 const [source,setSource]=createSignal({document_id:'d',revision_id:'r',segment_id:'s',page_start:1,page_end:50})
 const identity=createMemo(()=>sourceKey(source()));let page=0
 createComputed(on(identity,()=>{page=sourcePage(source())}))
 page=20;setSource({...source(),status:'active'});assert.equal(page,20,'refresh must preserve current page')
 setSource({...source(),segment_id:'next',page_start:21,page_end:30});assert.equal(page,21)
 setSource({...source(),segment_id:'citation',page_start:1,page_end:50,page:37});assert.equal(page,37)
 dispose()
})

const {referenceViewer,readingDockVisible,commitPage}=await import('../src/lib/lecture-session.ts')
const reference=referenceViewer({page:20,page_start:1,page_end:50,page_count:50})
assert.equal(reference.initialPage,20)
assert.equal(reference.source.page_start,1)
assert.equal(referenceViewer({page_start:10,page_end:20}).initialPage,10)
assert.equal(readingDockVisible({annotation:true}),false)
assert.equal(readingDockVisible({annotation:false}),true)
for(const flag of ['qa','occlusion','back'])assert.equal(readingDockVisible({[flag]:true}),false)
assert.deepEqual(commitPage('4',1,1,4),{page:4,error:''})
for(const text of ['', '0', '5', '2.5', 'bad']){
 const result=commitPage(text,3,1,4);assert.equal(result.page,3);assert.ok(result.error)
}
assert.deepEqual(commitPage('2',3,1,4),{page:2,error:''})
console.log('reference consumer config, annotation dock and explicit page commit: PASS')

createRoot(dispose=>{
 const [source,setSource]=createSignal({document_id:'d',revision_id:'r',segment_id:'s',page_start:1,page_end:50,page:20})
 const reference=createMemo(()=>referenceViewer(source()))
 const identity=createMemo(()=>sourceKey(reference().source)+'/'+reference().initialPage)
 let current=0
 createComputed(on(identity,()=>{current=sourcePage(reference().source,reference().initialPage)}))
 assert.equal(current,20);current=21
 setSource({...source(),status:'active'});assert.equal(current,21,'reference refresh must preserve user page choice')
 setSource({...source(),page:37});assert.equal(current,37,'new authoring citation must change reference page')
 dispose()
})
