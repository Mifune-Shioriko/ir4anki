import assert from 'node:assert/strict'
import * as module from '../src/api.ts'
const detail={partial:true,saved:[{path:'done.md',action:'written'}],failed:'late.md',remaining:['late.md'],message:'上传 I/O 失败；请核对已保存文件后重试'}
globalThis.fetch=async()=>new Response(JSON.stringify({detail}),{status:503})
await assert.rejects(()=>module.api.filesUpload([],''),e=>e.name==='UploadPartialError' && e.saved[0].path==='done.md' && e.message.includes('done.md'))
globalThis.fetch=async()=>new Response(JSON.stringify({detail:'批内重复目标：duplicate.md'}),{status:409})
await assert.rejects(()=>module.api.filesUpload([],''),e=>e instanceof module.ApiError && e.message.includes('重复目标'))
console.log('PASS structured partial upload receipt and non-retryable duplicate errors')
