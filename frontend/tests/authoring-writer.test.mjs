import assert from 'node:assert/strict'
import {createHash,webcrypto} from 'node:crypto'
import {AuthoringWriter} from '../src/lib/authoring-writer.ts'
import {pageRequestPath,pageResourceUrl,revisionPdfUrl,lectureResourceUrl,verifiedPng} from '../src/lib/lecture-api.ts'
import {writeDraft,clearOwnedDraft,persistWriterDraft} from '../src/lib/persistent-draft.ts'
if(!globalThis.crypto)globalThis.crypto=webcrypto
const initial={authoring_id:'a',version:2,published_version:2,crop:[0,0,1,1],masks:[],mode:'all',question:'saved',answer:'',note_links:[]}
let server=2,release,requests=[]
const writer=new AuthoringWriter(initial,async(value,version)=>{
 requests.push({method:'PUT',version,question:value.question,server_before:server});assert.equal(version,server)
 if(requests.length===1)await new Promise(resolve=>release=resolve)
 const next={...value,version:++server};requests.at(-1).response_version=server;return next
})
// Publishing already saved content, then close/dispose: absolutely no PUT.
await writer.flush();assert.deepEqual(requests,[])
writer.edit({...writer.value(),question:'first'})
const a=writer.flush(),b=writer.flush();assert.equal(a,b)
await Promise.resolve();writer.edit({...writer.value(),question:'newer during PUT'})
release();await Promise.all([a,b]);assert.equal(requests.length,2)
assert.deepEqual(requests.map(r=>r.version),[2,3]);assert.equal(writer.value().question,'newer during PUT');assert.equal(writer.state().dirty,false)
// Authoritative publication metadata is adopted before close/reopen/edit.
writer.setLocked(true);assert.throws(()=>writer.edit({...writer.value(),question:'frozen'}))
await writer.flush();assert.equal(requests.length,2)
writer.adopt({...writer.value(),version:4,published_version:4,note_links:[{mask_id:'mask',note_id:99}]});writer.setLocked(false)
await Promise.all([writer.flush(),writer.flush()]);writer.dispose();assert.equal(requests.length,2)
const reopened=new AuthoringWriter(writer.value(),async(v,version)=>{assert.equal(version,4);return {...v,version:5}})
assert.equal(reopened.value().note_links[0].note_id,99)
reopened.edit({...reopened.value(),answer:'edit after publication'});await reopened.flush();assert.equal(reopened.value().version,5)
// In-flight acknowledgement after disposal cannot start a second save or
// overwrite a newer browser writer. Unsaved later edits remain recoverable.
let ack;const memory=new Map(),storage={getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)}
const abandoned=new AuthoringWriter(initial,()=>new Promise(resolve=>ack=resolve),(v,state,reason)=>{
 if(state.dirty)writeDraft(storage,'draft',{...v,edit_sequence:state.sequence},'old',state.disposed||reason!=='edit')
 else clearOwnedDraft(storage,'draft','old',state.sequence)
})
abandoned.edit({...initial,question:'request'});const pending=abandoned.flush();await Promise.resolve()
abandoned.edit({...abandoned.value(),question:'unacknowledged newer local'});abandoned.dispose()
writeDraft(storage,'draft',{question:'new browser owner',edit_sequence:1},'new')
ack({...initial,version:3});await pending
assert.equal(abandoned.value().question,'unacknowledged newer local');assert.equal(abandoned.state().dirty,true)
assert.equal(JSON.parse(storage.getItem('draft')).question,'new browser owner')
assert.equal(clearOwnedDraft(storage,'draft','old',1),false)
// Failed close-save leaves dirty content; retry uses the same authoritative
// version, not a speculative bump or discarded draft.
let attempts=0;const failed=new AuthoringWriter(initial,async(v,n)=>{assert.equal(n,2);if(++attempts===1)throw Error('409 external version conflict');return {...v,version:3}})
failed.edit({...initial,question:'retain me'});await assert.rejects(failed.flush(),/409/);assert.equal(failed.state().dirty,true);await failed.flush();assert.equal(failed.state().dirty,false)
const ids={document_id:'Exact-DOC',revision_id:'Exact-REV'}
assert.equal(pageRequestPath(ids,4),'/Exact-DOC/revisions/Exact-REV/pages/4')
assert.equal(pageResourceUrl(ids,4,'image','AbC123'),'/api/lectures/Exact-DOC/revisions/Exact-REV/pages/4/image?hash=AbC123')
assert.equal(pageResourceUrl(ids,4,'thumbnail'),'/api/lectures/Exact-DOC/revisions/Exact-REV/pages/4/thumbnail')
assert.equal(revisionPdfUrl(ids),'/api/lectures/Exact-DOC/revisions/Exact-REV/pdf')
assert.throws(()=>lectureResourceUrl('/api/lectures/already-prefixed'))
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64'),hash=createHash('sha256').update(png).digest('hex')
await assert.rejects(verifiedPng(new Response('<html>SPA fallback</html>',{headers:{'Content-Type':'text/html'}}),hash),/PNG/)
await assert.rejects(verifiedPng(new Response('not png',{headers:{'Content-Type':'image/png'}}),hash),/PNG/)
await assert.rejects(verifiedPng(new Response(png,{headers:{'Content-Type':'image/png'}}),'wrong'),/基准图/)
const blob=await verifiedPng(new Response(png,{headers:{'Content-Type':'image/png'}}),hash);assert.deepEqual(Buffer.from(await blob.arrayBuffer()),png)
console.log('serialized publication/close/dispose versions:',JSON.stringify(requests))
console.log('resource URL identity / HTML200 rejection / PNG SHA256 / serialized writer / retained drafts: PASS')

// Undo back to the old server content while a different snapshot is in flight
// remains a recoverable intent until that request and its reversal finish.
let complete;const reverse=new AuthoringWriter(initial,()=>new Promise(resolve=>complete=resolve),(value,state,reason)=>persistWriterDraft(storage,'reverse',value,'reverse-owner',state,reason==='edit'))
reverse.edit({...initial,question:'in flight'});const reversal=reverse.flush();await Promise.resolve()
reverse.edit(initial);assert.equal(JSON.parse(storage.getItem('reverse')).question,'saved')
reverse.dispose();complete({...initial,question:'in flight',version:3});await reversal
assert.equal(reverse.state().dirty,true)
assert.equal(JSON.parse(storage.getItem('reverse')).version,3)
assert.equal(JSON.parse(storage.getItem('reverse')).question,'saved')
const noOp=new AuthoringWriter(initial,async()=>{throw Error('no-op wrote')},(value,state,reason)=>persistWriterDraft(storage,'noop',value,'owner',state,reason==='edit'))
noOp.edit({...initial,question:'temporary'});noOp.edit(initial);assert.equal(storage.getItem('noop'),null);await noOp.flush()
console.log('in-flight undo/dispose recovery and clean undo cache removal: PASS')

const {publicationDecision,clearRejectedPublication}=await import('../src/lib/authoring-writer.ts')
assert.equal(publicationDecision({state:'unknown',draft:initial}).kind,'frozen')
assert.equal(publicationDecision({state:'rejected',draft:{...initial,version:3}}).kind,'merge')
const pendingOperation={operation_id:'durable',status:'partial',items:[]}
assert.equal(publicationDecision({state:'rejected',draft:{...initial,pending_operation:pendingOperation}}).kind,'resume')
assert.equal(publicationDecision({state:'operation',operation:pendingOperation,draft:initial}).operation.operation_id,'durable')
storage.setItem('publish','newer-request');assert.equal(clearRejectedPublication(storage,'publish','old-request'),false)
assert.equal(storage.getItem('publish'),'newer-request');assert.equal(clearRejectedPublication(storage,'publish','newer-request'),true)
console.log('authoritative rejection vs unknown/pending and exact marker ownership: PASS')
