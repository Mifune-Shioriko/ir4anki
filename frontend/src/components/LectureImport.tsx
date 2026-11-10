import {createSignal,Show} from 'solid-js'
import {lectureRequest,type LectureRevision} from '../lib/lecture-api'
/** First import lives above the responsive list/preview switch. */
export function LectureImport(props:{directory:string;onImported:(path:string,revision:LectureRevision)=>void}){
 const [busy,setBusy]=createSignal(false),[error,setError]=createSignal(''),[message,setMessage]=createSignal('')
 let input!:HTMLInputElement
 async function importing(file:File){
  if(busy())return
  setBusy(true);setError('');setMessage('')
  try{
   const path=[props.directory,file.name].filter(Boolean).join('/'),body=new FormData()
   body.append('file',file);body.append('path',path)
   const revision=await lectureRequest<LectureRevision>('/import','POST',body)
   setMessage(revision.status==='ready'?'导入完成，尚未加入阅读清单。请在预览或阅读清单中加入。':'原件已保留，等待本机转换；转换完成后可加入阅读清单。')
   window.dispatchEvent(new Event('lecture-documents-changed'));props.onImported(path,revision)
  }catch(e){setError((e as Error).message)}finally{setBusy(false);input.value=''}
 }
 return <div class="lecture-import" aria-busy={busy()}>
  <md-filled-tonal-button disabled={busy()} onClick={()=>input.click()}>{busy()?'正在导入…':'导入 PDF / PPT / PPTX'}</md-filled-tonal-button>
  <input ref={input} aria-label="导入 PDF、PPT 或 PPTX" type="file" accept=".pdf,.ppt,.pptx" hidden onChange={e=>{const file=e.currentTarget.files?.[0];if(file)void importing(file)}}/>
  <span class="lecture-import-hint">导入到 {props.directory||'根目录'}</span>
  <Show when={message()}><p role="status">{message()}</p></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show>
 </div>
}
