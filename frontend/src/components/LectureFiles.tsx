import {requestDraftLeave} from '../lib/draft-leave'
import {createEffect,on,createSignal,onMount,onCleanup,For,Show,createMemo} from 'solid-js'
import {lectureRequest,type LectureDocument,type LectureRevision,type PageSource} from '../lib/lecture-api'
import {PdfSourceViewer} from './PdfSourceViewer'
export function LectureFiles(props:{directory:string;onChanged:()=>void;selectedPath?:string|null;onSelect:(path:string)=>void;mutationAllowed?:()=>boolean;onMutationBusy?:(busy:boolean)=>void}){
 const [noteQuery,setNoteQuery]=createSignal(''),[noteHits,setNoteHits]=createSignal<{revision_id:string;page:number;excerpt:string}[]>([]),[previewPage,setPreviewPage]=createSignal<number|undefined>()
 const [documents,setDocuments]=createSignal<LectureDocument[]>([]),[error,setError]=createSignal(''),[busy,setBusy]=createSignal(false),[revision,setRevision]=createSignal<LectureRevision|null>(null)
 let replacementTarget:string|null=null,revisionTarget:LectureDocument|null=null
 let refreshSequence=0,viewedDocument=''
 const mutationOwner={alive:true,generation:0}
 function beginMutation(){return {id:++mutationOwner.generation,path:props.selectedPath,directory:props.directory}}
 function mutationCurrent(token:ReturnType<typeof beginMutation>){return mutationOwner.alive&&token.id===mutationOwner.generation&&token.path===props.selectedPath&&token.directory===props.directory}
 function releaseMutation(){++mutationOwner.generation;if(busy())props.onMutationBusy?.(false);setBusy(false);replacementTarget=null;revisionTarget=null}
 createEffect(on(()=>[props.selectedPath,props.directory],()=>releaseMutation()))
 let input!:HTMLInputElement,alive=true,timer:ReturnType<typeof setInterval>|undefined
 async function refresh(){if(!alive)return;const token=++refreshSequence;try{const d=await lectureRequest<{documents:LectureDocument[]}>('');if(alive&&token===refreshSequence){const old=documents().find(x=>x.path===props.selectedPath),next=d.documents.find(x=>x.path===props.selectedPath);if(old&&old.current_revision!==next?.current_revision&&!requestDraftLeave()){setError('课件列表已有变化；请先保存或复制当前页笔记，再刷新。');return}setDocuments(d.documents)}}catch(e){if(alive)setError((e as Error).message)}}
 const filesChanged=()=>{setNoteHits([]);setNoteQuery('');void refresh()}
 onMount(()=>{window.addEventListener('lecture-documents-changed',filesChanged);window.addEventListener('files-relocated',filesChanged);window.addEventListener('files-deleted',filesChanged);void refresh();timer=setInterval(()=>{if(documents().some(d=>d.revision.status!=='ready'))void refresh()},1800)})
 onCleanup(()=>{window.removeEventListener('lecture-documents-changed',filesChanged);window.removeEventListener('files-relocated',filesChanged);window.removeEventListener('files-deleted',filesChanged);releaseMutation();mutationOwner.alive=false;alive=false;if(timer)clearInterval(timer)})
 const selected=createMemo(()=>documents().find(d=>d.path===props.selectedPath))
 createEffect(on(()=>selected()?.document_id,id=>{if(id&&id!==viewedDocument){viewedDocument=id;setRevision(null);setPreviewPage(undefined);setNoteHits([]);setNoteQuery('')}}))
 async function importing(file:File){
  if(busy()||props.mutationAllowed?.()===false||!requestDraftLeave())return
  const token=beginMutation(),replacement=replacementTarget,target=revisionTarget
  const path=target?.path??[props.directory,file.name].filter(Boolean).join('/'),old=target??documents().find(d=>d.path===path)
  if(!replacement&&old&&!target&&!window.confirm('为此课件导入新版本？旧版本、卡片引用和阅读进度会保留，不自动迁移。'))return
  props.onMutationBusy?.(true);setBusy(true);setError('');input.value=''
  try{
   const body=new FormData();body.append('file',file)
   if(replacement)await lectureRequest('/'+replacement+'/pdf-revision','POST',body)
   else {body.append('path',path);if(old)body.append('document_id',old.document_id);await lectureRequest('/import','POST',body)}
   // A confirmed mutation may refresh its parent, but never restores a disposed view.
   window.dispatchEvent(new Event('lecture-documents-changed'))
   if(!mutationCurrent(token))return
   await refresh();if(!mutationCurrent(token))return
   props.onChanged();if(!replacement)props.onSelect(path);setRevision(null)
  }catch(e){if(mutationCurrent(token))setError((e as Error).message)}
  finally{if(mutationCurrent(token)){props.onMutationBusy?.(false);setBusy(false);revisionTarget=null;replacementTarget=null}}
 }
 async function join(d:LectureDocument){
  if(busy()||props.mutationAllowed?.()===false)return
  const token=beginMutation();props.onMutationBusy?.(true);setBusy(true);setError('')
  try{const r=revision()??d.revision;await lectureRequest(`/${d.document_id}/revisions/${r.revision_id}/read`,'POST');window.dispatchEvent(new Event('lecture-documents-changed'));if(!mutationCurrent(token))return;await refresh();if(mutationCurrent(token))props.onChanged()}
  catch(e){if(mutationCurrent(token))setError((e as Error).message)}finally{if(mutationCurrent(token)){props.onMutationBusy?.(false);setBusy(false)}}
 }
 return <section class="lecture-files" aria-label="课件文件">
  <input ref={input} type="file" accept=".pdf,.ppt,.pptx" hidden onChange={e=>{const f=e.currentTarget.files?.[0];if(f)void importing(f)}}/>
  <Show when={busy()}><p role="status">正在处理课件…</p></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show>
  <Show when={!selected()}><div class="lecture-document-list"><For each={documents()}>{d=><button class="lecture-document-row" onClick={()=>{props.onSelect(d.path);setRevision(null)}}><strong>{d.title}</strong><span>{d.revision.status==='ready'?`${d.revision.page_count} 页`:d.revision.status==='failed'?'转换失败，原件保留':d.revision.status==='running'?'本机转换中…':'等待本机转换'}</span></button>}</For></div></Show>
  <Show when={selected()?.document_id} keyed>{id=>{
   const d=new Proxy({} as LectureDocument,{get:(_,key)=>documents().find(x=>x.document_id===id)?.[key as keyof LectureDocument]})
   const shown=()=>revision()??d.revision
   return <><div class="lecture-controls"><label>版本<select aria-label="课件版本" value={shown().revision_id} onChange={e=>{if(requestDraftLeave())setRevision(d.revisions?.find(r=>r.revision_id===e.currentTarget.value)??null);else e.currentTarget.value=shown().revision_id}}><For each={d.revisions??[d.revision]}>{(r,i)=><option value={r.revision_id}>{r.revision_id===d.current_revision?'当前版本':`历史版本 ${i()+1}`} · {r.revision_id.slice(0,8)}</option>}</For></select></label><md-text-button disabled={busy()||shown().status!=='ready'||d.listed} onClick={()=>void join(d)}>{d.listed?(shown().revision_id===(d.reading_revision??d.current_revision)?'已在阅读清单':'请先移出旧阅读版本'):'加入阅读清单'}</md-text-button><md-text-button disabled={busy()} onClick={()=>void importingSelection()}>导入新版本</md-text-button><md-text-button disabled={busy()} onClick={()=>{replacementTarget=d.document_id;input.accept='.pdf';input.click()}}>使用自行导出的 PDF</md-text-button></div>
    <details class="lecture-notes-search"><summary>页笔记 · 按真实 Markdown 搜索</summary><div class="lecture-controls"><input aria-label="搜索此课件页笔记" value={noteQuery()} onInput={e=>setNoteQuery(e.currentTarget.value)}/><md-text-button onClick={async()=>{try{const result=await lectureRequest<{matches:{revision_id:string;page:number;excerpt:string}[]}>(`/${d.document_id}/annotations/search?q=${encodeURIComponent(noteQuery())}`);setNoteHits(result.matches)}catch(e){setError((e as Error).message)}}}>搜索页笔记</md-text-button></div><For each={noteHits()}>{hit=><button class="lecture-document-row" onClick={()=>{if(!requestDraftLeave())return;setRevision(d.revisions?.find(r=>r.revision_id===hit.revision_id)??null);setPreviewPage(hit.page)}}><strong>第 {hit.page} 页</strong><span>{hit.excerpt}</span></button>}</For></details>
    <Show when={shown().conversion?.font_warning}><p class="lecture-help">{shown().conversion?.font_warning}</p></Show>
    <Show when={shown().status==='ready'} fallback={<div role="status"><p>{shown().status==='failed'?'转换失败；原件已保留，可重试。':'正在本机转换；原件已保留。'}</p><md-text-button onClick={async()=>{if(shown().job_id){try{await lectureRequest(`/jobs/${shown().job_id}/retry`,'POST');await refresh()}catch(e){setError((e as Error).message)}}}}>重试转换</md-text-button></div>}>
     <PdfSourceViewer onPage={setPreviewPage} initialPage={previewPage()} source={{kind:'pdf',document_id:d.document_id,revision_id:shown().revision_id,path:d.path,page_start:1,page_end:shown().page_count!} as PageSource}/>
    </Show>
   </>
   function importingSelection(){replacementTarget=null;revisionTarget=d;input.accept='.pdf,.ppt,.pptx';input.click()}
  }}</Show>
 </section>
}
