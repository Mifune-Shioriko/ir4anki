import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {ownPdfLoadingTask} from '../src/lib/pdf-lifetime.ts'
const require=createRequire(import.meta.url)
// Legacy build supplies Node20's missing JS builtins; actual installed PDF.js
// loading/worker implementation runs, with no HTTP or simulated task protocol.
const {getDocument}=await import('pdfjs-dist/legacy/build/pdf.mjs')
function fixture(){const chunks=['%PDF-1.4\n'],offset=[0];for(const [i,obj] of ['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>'].entries()){offset.push(chunks.join('').length);chunks.push(`${i+1} 0 obj\n${obj}\nendobj\n`)}const start=chunks.join('').length;chunks.push(`xref\n0 4\n0000000000 65535 f \n${offset.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`);return new TextEncoder().encode(chunks.join(''))}
const unhandled=[]
const listener=e=>unhandled.push(e)
process.on('unhandledRejection',listener)
try{
 const warm=getDocument({data:fixture()});await warm.promise;await warm.destroy()
 // Retire precisely after the real transport exists, before metadata settles.
 for(let i=0;i<3;i++){
  const task=getDocument({data:fixture(),isEvalSupported:false}),ready=[],errors=[],release=[]
  const owner=ownPdfLoadingTask(task,v=>ready.push(v),e=>errors.push(e),e=>release.push(e))
  for(let n=0;n<1000&&!task._transport;n++)await Promise.resolve()
  assert.ok(task._transport,'must reach real worker transport before retirement')
  assert.equal(ready.length,0,'must retire before document ready')
  const closing=owner.dispose();assert.equal(owner.dispose(),closing,'one finalizer per loading task')
  await closing;await new Promise(setImmediate)
  assert.equal(ready.length,0,'retired load must never install stale document/page')
  assert.deepEqual(errors,[]);assert.deepEqual(release,[]);assert.equal(task.destroyed,true)
 }
 // Current-source corrupt input remains an explicit failure, not cancellation.
 const bad=getDocument({data:new TextEncoder().encode('%PDF-1.4\ncorrupt fixture'),isEvalSupported:false}),failures=[]
 const badOwner=ownPdfLoadingTask(bad,()=>assert.fail('corrupt PDF ready'),e=>failures.push(e),e=>assert.fail(e))
 await bad.promise.catch(()=>{});await new Promise(setImmediate)
 assert.equal(failures.length,1);assert.match(failures[0].name,/InvalidPDF/);await badOwner.dispose()
 // Loaded-document page/text work can race disposal, but owns its rejection.
 const loaded=getDocument({data:fixture(),isEvalSupported:false}),active=[]
 const owner=ownPdfLoadingTask(loaded,v=>active.push(v),e=>assert.fail(e),e=>assert.fail(e))
 const doc=await loaded.promise;await new Promise(setImmediate);assert.equal(active.length,1)
 const text=doc.getPage(1).then(page=>page.getTextContent()).then(()=>true,()=>false)
 await owner.dispose();await text;await new Promise(setImmediate)
 assert.deepEqual(unhandled,[],'all real loading/worker/page promises must be owned')
 console.log('PASS real PDF.js pending-load retirement x3, active corrupt failure, loaded text/disposal; zero unhandled rejections')
}finally{process.off('unhandledRejection',listener)}
