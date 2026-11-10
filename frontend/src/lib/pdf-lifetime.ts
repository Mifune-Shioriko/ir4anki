/** Own every loading/destroy promise. PDF.js may reject an internal worker
 * continuation if Terminate races initial document parsing, even when the
 * public destroy promise is handled. Retire immediately, release after load
 * settles, and never install a retired document. One task has one finalizer. */
export function ownPdfLoadingTask<T>(task:{promise:Promise<T>;destroy:()=>Promise<void>},ready:(value:T)=>void,failed:(error:unknown)=>void,releaseFailed:(error:unknown)=>void){
 let retired=false,release:Promise<void>|undefined
 const settled=task.promise.then(value=>{if(!retired)ready(value)},error=>{if(!retired)failed(error)}).catch(error=>{if(retired)releaseFailed(error);else failed(error)})
 return {dispose():Promise<void>{
  retired=true
  return release??=settled.then(()=>task.destroy()).catch(releaseFailed)
 }}
}
