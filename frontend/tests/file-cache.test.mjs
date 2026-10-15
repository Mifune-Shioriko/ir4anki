import assert from 'node:assert/strict'
import { acquireDocument, invalidateDocuments } from '../src/lib/file-cache.ts'
const turn = () => new Promise(resolve => setTimeout(resolve, 0))
let calls = 0, aborts = 0, finish
const fetcher = (path, signal) => {
  calls++
  return new Promise((resolve,reject) => {
    finish = resolve
    signal.addEventListener('abort', () => { aborts++; reject(new DOMException('cancelled','AbortError')) })
  })
}
invalidateDocuments()
const a=acquireDocument('a.md','1',fetcher), b=acquireDocument('a.md','1',fetcher)
assert.equal(calls,1)
a.release();await turn();assert.equal(aborts,0)
finish({text:'# A\n\n$x^2$',revision:'1'})
const doc=await b.promise;assert(doc.html.includes('data-src-line'));assert(doc.html.includes('katex'));b.release()
const warm=acquireDocument('a.md','1',fetcher);assert.equal((await warm.promise).html,doc.html);assert.equal(calls,1)
const cancelled=acquireDocument('b.md','1',fetcher);const caught=cancelled.promise.catch(e=>e.name);cancelled.release();await turn();assert.equal(await caught,'AbortError');assert.equal(aborts,1)
// Rapid A/B/A cannot attach the third consumer to an already-aborted request.
const rapid1=acquireDocument('race.md','1',fetcher);const ignored=rapid1.promise.catch(e=>e.name);rapid1.release();await turn()
const rapid2=acquireDocument('race.md','1',fetcher);finish({text:'# Latest A'});assert((await rapid2.promise).html.includes('Latest A'));rapid2.release();assert.equal(await ignored,'AbortError')
const rev=acquireDocument('a.md','2',async()=>({text:'# Overwritten',revision:'2'}));assert((await rev.promise).html.includes('Overwritten'));rev.release()
invalidateDocuments('a.md');const invalid=acquireDocument('a.md','1',async()=>({text:'# Invalidated'}));assert((await invalid.promise).html.includes('Invalidated'));invalid.release()
// Prefix invalidation also covers nested entries and does not hit sibling names.
for(const path of ['dir/a.md','directory/b.md']) {const req=acquireDocument(path,'1',async()=>({text:'# '+path}));await req.promise;req.release()}
invalidateDocuments('dir');let refreshed=0
await acquireDocument('dir/a.md','1',async()=>{refreshed++;return {text:'# fresh'}}).promise
await acquireDocument('directory/b.md','1',async()=>{throw Error('sibling wrongly evicted')}).promise
assert.equal(refreshed,1)
invalidateDocuments()
for(let i=0;i<25;i++)await acquireDocument(`lru${i}.md`,'1',async()=>({text:'# Synthetic '+i})).promise
let misses=0;await acquireDocument('lru0.md','1',async()=>{misses++;return {text:'# evicted'}}).promise;assert.equal(misses,1)
console.log('PASS shared-consumer dedup/cancellation, rapid reacquire, source/KaTeX, revision overwrite, prefix invalidation, bounded LRU')
