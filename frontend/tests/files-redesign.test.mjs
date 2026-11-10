import assert from 'node:assert/strict'
import {webcrypto} from 'node:crypto'
Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues:v=>webcrypto.getRandomValues(v)}})
import {normalizeTargets,moveError,visibleEntries,SerialBatch} from '../src/lib/files-browser.ts'
const entries=[{path:'甲',isDir:true},{path:'甲/长名.md',isDir:false},{path:'乙',isDir:true},{path:'乙/长名.md',isDir:false},{path:'a.md',isDir:false}]
assert.deepEqual(normalizeTargets([entries[1],entries[0],entries[1]]),[entries[0]])
assert.equal(moveError([entries[0]],'甲/子',entries),'不能移动到原位置、自身或子目录')
assert.equal(moveError([entries[1]],'乙',entries),'目标已有同名项目：长名.md')
assert.deepEqual(visibleEntries(entries,'甲','','all').map(x=>x.path),['甲/长名.md'])
assert.equal(visibleEntries(entries,'甲','长名','all').length,2)
assert.equal(visibleEntries(entries,'甲','长名','current').length,1)
// Mandatory lifecycle regression: late response cannot authorize another dispatch.
let release;const dispatched=[],reports=[]
const batch=new SerialBatch(r=>reports.push(structuredClone(r)))
const done=batch.run('移动',entries.slice(0,2),async t=>{dispatched.push(t.path);await new Promise(r=>release=r);return 'confirmed'})
await Promise.resolve();batch.revoke()
const replacementReports=[];const replacement=new SerialBatch(r=>replacementReports.push(structuredClone(r)))
await replacement.run('新目标',[entries[2]],async()=> 'confirmed')
release();await done
assert.deepEqual(dispatched,['甲']);assert.equal(replacementReports.at(-1).items[0].path,'乙')
assert.equal(reports.at(-1).items[0].status,'running') // no stale writer after revocation
const partial=new SerialBatch(()=>{});let n=0
const report=await partial.run('移动',[entries[1],entries[3],entries[4]],async()=>++n===1?'unconfirmed':'confirmed')
assert.equal(n,1);assert.deepEqual(report.items.map(x=>x.status),['unconfirmed','not-started','not-started'])
console.log('PASS files model, collisions, search, serial uncertainty, revoked late-response authority')
