import {test} from 'node:test'
import assert from 'node:assert/strict'
import {samePage,relatedDetails} from '../src/lib/lecture-session.ts'
test('cached/trace authoring is accepted only for exact document revision page',()=>{
 const a={document_id:'d',revision_id:'r',page:3}
 assert.equal(samePage(a,{...a,segment_id:'historical'}),true)
 for(const b of [{...a,page:4},{...a,document_id:'other'},{...a,revision_id:'other'}])assert.equal(samePage(a,b),false)
})
test('details are deduplicated and batched after exact page selection with no 100 cap',async()=>{
 const calls=[];const ids=Array.from({length:241},(_,i)=>i+1)
 const result=await relatedDetails([...ids,1],async batch=>{calls.push(batch);return {cards:batch.map(noteId=>({noteId}))}})
 assert.equal(result.length,241);assert.deepEqual(calls.map(a=>a.length),[100,100,41])
})
