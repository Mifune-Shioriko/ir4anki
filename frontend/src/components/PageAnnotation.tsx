import {selectionText} from '../lib/lecture-session'
import {registerDraftLeave} from '../lib/draft-leave'
import {writeDraft,clearDraft,ownsDraft} from '../lib/persistent-draft'
import {createEffect,createSignal,onMount,onCleanup,Show} from 'solid-js'
import {MarkdownField} from './MarkdownField'
import {renderMarkdown} from '../lib/markdown'
import {lectureRequest,pageRequestPath,type PageLocator,type Annotation} from '../lib/lecture-api'
export function PageAnnotation(props:{source:PageLocator;onEditingChange?:(editing:boolean)=>void;onQuote?:(text:string,source:PageLocator)=>void;onCloze?:(text:string,source:PageLocator)=>void}){
 // This callback belongs to this page instance. Cleanup must not traverse
 // reactive parent/spread getters after their source has already been cleared.
 const notifyEditing=props.onEditingChange
 const writer=crypto.randomUUID()
 const key=`ir4anki.annotation.${props.source.document_id}.${props.source.revision_id}.${props.source.page}`
 const endpoint=pageRequestPath(props.source,props.source.page)+'/annotation'
 const [selection,setSelection]=createSignal(''),[annotationId,setAnnotationId]=createSignal('')
 const [body,setBody]=createSignal(''),[version,setVersion]=createSignal(''),[editing,setEditing]=createSignal(false),[ready,setReady]=createSignal(false),[status,setStatus]=createSignal('加载笔记…'),[error,setError]=createSignal(''),[conflict,setConflict]=createSignal(false),[storageFailed,setStorageFailed]=createSignal(false)
 let disposed=false,timer:ReturnType<typeof setTimeout>|undefined,saving=false,dirty=false,requestId:string=crypto.randomUUID()
 function persist(){
  try{writeDraft(localStorage,key,{body:body(),version:version(),requestId},writer,disposed);setStorageFailed(false);return true}
  catch{setStorageFailed(true);setError('浏览器无法保留草稿。请复制内容备份并手动保存后再离开。');return false}
 }
 async function save(){
  if(saving||!dirty||conflict()||!version())return
  saving=true;const text=body(),base=version(),id=requestId
  if(!disposed)setStatus('保存中…')
  try{
   const result=await lectureRequest<Annotation>(endpoint,'PUT',{body:text,expected_version:base,request_id:id})
   setVersion(result.version);dirty=body()!==text
   if(dirty){requestId=crypto.randomUUID();persist()}
   else{try{clearDraft(localStorage,key,id,text)}catch{};if(!disposed){setStatus('已保存');setError('')}}
  }catch(e){if(!disposed){setStatus('保存失败');setError((e as Error).message);setConflict((e as Error).message.includes('冲突')||(e as Error).message.includes('外部'))}persist()}
  finally{saving=false;let owned=true;try{owned=ownsDraft(localStorage,key,writer)}catch{};if(dirty&&!conflict()&&!error()&&(!disposed||owned))void save()}
 }
 const change=(value:string)=>{setBody(value);dirty=true;requestId=crypto.randomUUID();persist();setStatus('草稿已保留');if(timer)clearTimeout(timer);timer=setTimeout(()=>void save(),600)}
 async function load(){
  try{
   const result=await lectureRequest<Annotation>(endpoint);if(disposed)return
   let cached:{body:string;version:string;requestId:string}|null=null
   try{cached=JSON.parse(localStorage.getItem(key)??'null')}catch{setStorageFailed(true);setError('无法读取浏览器草稿，请先检查存储权限')}
   setAnnotationId(result.annotation_id);setVersion(result.version);setBody(cached?.body??result.body);setStatus(cached?'已恢复本地草稿':'已保存')
   if(cached){dirty=true;requestId=cached.requestId;setEditing(true);if(cached.version!==result.version){setVersion(cached.version);setConflict(true);setError('页笔记已变化，本地草稿保留。请检查最新内容后合并。')}else void save()}
   setReady(true)
  }catch(e){if(!disposed)setError((e as Error).message)}
 }
 createEffect(()=>notifyEditing?.(editing()))
 onMount(()=>void load())
 async function merge(){
  try{const latest=await lectureRequest<Annotation>(endpoint);if(!window.confirm('最新服务器内容：\n\n'+latest.body+'\n\n确认已将需要保留的内容合并到编辑区后，使用当前编辑区保存？'))return
   setVersion(latest.version);requestId=crypto.randomUUID();setConflict(false);setError('');persist();void save()
  }catch(e){setError((e as Error).message)}
 }
 const unregister=registerDraftLeave(()=>!dirty||persist()||window.confirm('当前页笔记尚未保存，浏览器也无法保留草稿。离开会丢失编辑区内容。请取消并复制或重试保存；仍要丢弃并离开？'))
 onCleanup(unregister)
 const beforeUnload=(e:BeforeUnloadEvent)=>{if(storageFailed()&&dirty){e.preventDefault();e.returnValue=''}}
 onMount(()=>window.addEventListener('beforeunload',beforeUnload))
 onCleanup(()=>{notifyEditing?.(false);disposed=true;if(timer)clearTimeout(timer);if(dirty){persist();void save()}window.removeEventListener('beforeunload',beforeUnload)})
 return <section class="page-annotation" data-ready={ready()} onMouseUp={e=>{const selected=window.getSelection();if(selected?.anchorNode&&e.currentTarget.contains(selected.anchorNode))setSelection(previous=>selectionText(previous,selected.toString(),!!(e.target as HTMLElement).closest('[role=toolbar]')))}} aria-label={`第 ${props.source.page} 页理解笔记`} onKeyDown={e=>e.stopPropagation()}>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p><md-text-button onClick={()=>!ready()?void load():conflict()?void merge():void save()}>{conflict()?'查看最新内容并合并':'重试保存'}</md-text-button></Show>
  <Show when={selection()&&(props.onQuote||props.onCloze)}><div class="lecture-controls" role="toolbar" aria-label="附注选文制卡"><Show when={props.onQuote}><md-text-button onClick={()=>props.onQuote?.(selection(),{...props.source,annotation_id:annotationId()})}>选文制问答</md-text-button></Show><Show when={props.onCloze}><md-text-button onClick={()=>props.onCloze?.(selection(),{...props.source,annotation_id:annotationId()})}>选文制文字挖空</md-text-button></Show></div></Show>
  <Show when={ready()}>
   <Show when={editing()} fallback={<Show when={body()} fallback={<md-text-button onClick={()=>setEditing(true)}>＋ 添加理解笔记</md-text-button>}><div class="note-body" innerHTML={renderMarkdown(body())}/><md-text-button onClick={()=>setEditing(true)}>编辑理解笔记</md-text-button></Show>}>
    <div class="lecture-section-heading"><h3>自己的理解</h3><span role="status">{status()}</span><md-text-button onClick={()=>{if(!dirty||persist()){void save();setEditing(false)}}}>收起</md-text-button></div>
    <MarkdownField initial={body()} onChange={change} onSave={()=>void save()} label="理解笔记"/>
   </Show>
  </Show>
 </section>
}
