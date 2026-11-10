import {createEffect,createSignal,on,onCleanup,Show} from 'solid-js'
import {lectureRequest,type LectureDocument,type PageSource} from '../lib/lecture-api'
import {PdfReferenceViewer} from './PdfReferenceViewer'

/** Files owns only the selected current document, never a reading/authoring session. */
export function FilesLecturePreview(props:{path:string}){
 const [source,setSource]=createSignal<PageSource>(),[loading,setLoading]=createSignal(true),[error,setError]=createSignal(''),[status,setStatus]=createSignal(''),[retry,setRetry]=createSignal(0),[failedJob,setFailedJob]=createSignal<string>(),[retrying,setRetrying]=createSignal(false)
 let generation=0
 createEffect(on(()=>[props.path,retry()] as const,()=>{
  const path=props.path,token=++generation,controller=new AbortController()
  let timer:ReturnType<typeof setTimeout>|undefined
  setSource(undefined);setError('');setStatus('');setFailedJob(undefined);setRetrying(false);setLoading(true)
  function refresh(){
  lectureRequest<{documents:LectureDocument[]}>('','GET',undefined,controller.signal).then(result=>{
   if(token!==generation)return
   const document=result.documents.find(d=>d.path===path)
   if(!document)throw new Error('未找到此课件，请刷新文件列表')
   const revision=document.revisions?.find(r=>r.revision_id===document.current_revision)??document.revision
   if(revision.revision_id!==document.current_revision)throw new Error('当前课件版本信息不一致，请重新载入')
   if(revision.status!=='ready'){
    setStatus(revision.status==='failed'?'课件转换失败；原件已保留。'+(revision.error??''):'课件正在转换，完成后自动显示。')
    setFailedJob(revision.status==='failed'?revision.job_id:undefined)
    if(revision.status==='waiting'||revision.status==='running')timer=setTimeout(refresh,1200)
    return
   }
   if(!revision.page_count)throw new Error('课件缺少页面信息，请重新载入')
   setStatus('');setFailedJob(undefined)
   setSource({kind:'pdf',document_id:document.document_id,revision_id:revision.revision_id,path,segment_id:'files-preview',chunk_key:path,version:1,status:'ready',page_start:1,page_end:revision.page_count})
  }).catch(e=>{if(token===generation)setError((e as Error).message)}).finally(()=>{if(token===generation)setLoading(false)})
  }
  refresh()
  onCleanup(()=>{++generation;controller.abort();clearTimeout(timer)})
 }))
 return <section class="files-lecture-preview" aria-label="只读课件预览">
  <Show when={loading()}><p role="status">正在载入课件…</p></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show>
  <Show when={status()}><p role="status">{status()}</p></Show>
  <Show when={failedJob()}><md-text-button disabled={retrying()} onClick={async()=>{const token=generation,job=failedJob();if(!job||retrying())return;setRetrying(true);setError('');try{await lectureRequest(`/jobs/${job}/retry`,'POST');if(token===generation)setRetry(n=>n+1)}catch(e){if(token===generation)setError((e as Error).message)}finally{if(token===generation)setRetrying(false)}}}>重试转换</md-text-button></Show>
  <Show when={error()||status()}><md-text-button onClick={()=>setRetry(n=>n+1)}>重新载入文档</md-text-button></Show>
  <Show when={source()} keyed>{current=><PdfReferenceViewer plain source={current}/>}</Show>
 </section>
}
