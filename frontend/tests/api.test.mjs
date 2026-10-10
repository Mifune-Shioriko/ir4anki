import assert from 'node:assert/strict'
import { api, setEngineToken } from '../src/api.ts'
setEngineToken('isolated-test-token')
const calls = []
globalThis.fetch = async (url,init) => {
 calls.push({url,init})
 return new Response(JSON.stringify(url.endsWith('/export') ? {download_url:'/api/engine/download/handle'} : {ok:true}),{headers:{'Content-Type':'application/json'}})
}
await api.engineStats(); await api.engineStatus(); await api.engineBackup(); await api.engineExport(); await api.engineSync()
assert.deepEqual(calls.map(c=>c.url),['/api/engine/stats','/api/engine/status','/api/engine/backup','/api/engine/export','/api/engine/sync'])
assert.equal(calls[0].init.headers.Authorization,'Bearer isolated-test-token')
assert.equal(calls[4].init.headers.Authorization,'Bearer isolated-test-token')
assert.deepEqual(JSON.parse(calls[4].init.body),{commit_undo:true})
const fields={Front:'  **exact** <br>\n',Back:'$x$ ![](/media/a.png)'}
await api.addCard(fields,['user','ir4anki::markdown'])
assert.deepEqual(JSON.parse(calls[5].init.body),{fields,tags:['user','ir4anki::markdown']})
await api.updateNote(1,fields,['user','ir4anki::markdown'])
assert.deepEqual(JSON.parse(calls[6].init.body),{card_id:1,fields,tags:['user','ir4anki::markdown']})
globalThis.fetch=async()=>new Response('<html>SPA fallback</html>')
await assert.rejects(()=>api.engineStats(),/后端响应异常/)
console.log('PASS API native routes/explicit undo commit, unchanged fields/tags, unsupported SPA rejection')

const { engineDownloadPath } = await import('../src/api.ts')
assert.equal(engineDownloadPath('/api/engine/download/handle','https://local.test'),'/api/engine/download/handle')
for (const url of ['javascript:alert(1)','https://evil.test/api/engine/download/x','//evil.test/api/engine/download/x','/api/other','/api/engine/../../else']) assert.throws(()=>engineDownloadPath(url,'https://local.test'))
console.log('PASS controlled same-origin engine download URLs')

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
