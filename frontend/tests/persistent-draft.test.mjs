import test from 'node:test'
import assert from 'node:assert/strict'
import {writeDraft,clearDraft,ownsDraft} from '../src/lib/persistent-draft.ts'
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}}
test('late saved response cannot erase newer page draft',()=>{
 const s=storage();writeDraft(s,'page',{requestId:'old',body:'old'},'first')
 writeDraft(s,'page',{requestId:'new',body:'new text'},'second')
 assert.equal(clearDraft(s,'page','old','old'),false)
 assert.equal(JSON.parse(s.getItem('page')).body,'new text')
 assert.equal(clearDraft(s,'page','new','new text'),true)
})
test('disposed writer cannot reclaim a remounted or second-tab draft',()=>{
 const s=storage();writeDraft(s,'page',{body:'first'},'first')
 writeDraft(s,'page',{body:'second'},'second')
 assert.equal(ownsDraft(s,'page','first'),false)
 assert.equal(writeDraft(s,'page',{body:'stale'},'first',true),false)
 assert.equal(JSON.parse(s.getItem('page')).body,'second')
})

test('disposed writer cannot resurrect an already acknowledged newer draft',()=>{
 const s=storage();writeDraft(s,'page',{requestId:'new',body:'new'},'second');clearDraft(s,'page','new','new')
 assert.equal(writeDraft(s,'page',{body:'stale'},'first',true),false)
 assert.equal(s.getItem('page'),null)
})
