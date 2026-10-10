import assert from 'node:assert/strict'
import { api } from '../src/api.ts'
const splitCalls = []
globalThis.localStorage = { getItem: () => 'extract' }
globalThis.fetch = async (url, init) => {
 splitCalls.push({url, body:JSON.parse(init.body)})
 return new Response(JSON.stringify({ok:true}), {headers:{'Content-Type':'application/json'}})
}
await api.readingSplit('source.md', 7, [{start_line:2,end_line:4}], {fingerprint:'exact-fp',line_start:8})
await api.readingSplit('source.md', 7, [{start_line:2,end_line:4}])
assert.deepEqual(splitCalls[0], {url:'/api/reading/split',body:{path:'source.md',seg_id:7,selections:[{start_line:2,end_line:4}],fingerprint:'exact-fp',line_start:8}})
assert.equal('gap_policy' in splitCalls[1].body, false)
console.log('PASS bookmark split omits policy and preserves fourth-argument coordinate guards')
