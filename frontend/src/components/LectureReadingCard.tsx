import {SplitDialog} from './SplitDialog'
import {readingDockVisible,sourceKey,sourcePage,samePage} from '../lib/lecture-session'
import {createMemo,createSignal,createEffect,on,onMount,onCleanup,Show} from 'solid-js'
import {StudyCardShell,StudyToolbar,StudyActionDock} from './StudyCardShell'
import {PdfSourceViewer} from './PdfSourceViewer'
import {OcclusionEditor} from './OcclusionEditor'
import {LectureQuestionAnswer} from './LectureQuestionAnswer'
import {IconAdd,IconPassword,IconUndo,IconClose,IconArrowBack,IconContentCut} from './icons'
import {lectureRequest,locator,pageResourceUrl,type PageSource,type OcclusionDraft,type PageLocator} from '../lib/lecture-api'
export function LectureReadingCard(props:{source:PageSource;busy:boolean;onComplete:()=>void;onNext:()=>void;onSkip:()=>void;onExit:()=>void;onUndo?:()=>void;undoEnabled?:boolean;onExitRound?:()=>void;onChanged:()=>void;onPublished?:()=>void;onPage?:(source:PageLocator)=>void;onSplit?:(parent:PageSource,children:PageSource[])=>void;onSplitBegin?:()=>{current:()=>boolean;finish:()=>void};onBack?:()=>void;authoringId?:string;onTextCloze?:(source:PageLocator,text:string)=>void}){
 const [annotationEditing,setAnnotationEditing]=createSignal(false)
 const [qaSeed,setQaSeed]=createSignal(''),[qaSource,setQaSource]=createSignal<PageLocator|null>(null)
 const [page,setPage]=createSignal(sourcePage(props.source)),[editor,setEditor]=createSignal<OcclusionDraft|null>(null),[qa,setQa]=createSignal(false),[error,setError]=createSignal(''),[loading,setLoading]=createSignal(false),[split,setSplit]=createSignal(false),[start,setStart]=createSignal(sourcePage(props.source)),[end,setEnd]=createSignal(sourcePage(props.source))
 const sourceIdentity=createMemo(()=>sourceKey({...props.source,page:undefined}))
 let splitPage:number|undefined
 let generation=0
 let position=0
 const hideReader=()=>{position=document.querySelector('.study-scroll')?.scrollTop??window.scrollY}
 const returnReader=()=>{const wasOcclusion=!!editor();setEditor(null);setQa(false);if(wasOcclusion)requestAnimationFrame(()=>{document.querySelector('.study-scroll')?.scrollTo({top:position});window.scrollTo({top:position})})}
 // Capture provenance once: the visible reader may keep paging beside the draft.
 const openQa=(seed='',source=locator(props.source,page()))=>{if(qa()||editor()||loading()||props.busy||split())return;setQaSeed(seed);setQaSource(source);setQa(true)}
 async function occlusion(newProject=false,existingId?:string){
  if(loading()||props.busy||editor()||qa())return
  const ticket=generation, selected=locator(props.source,page())
  setLoading(true);setError('');hideReader()
  try{
   const localKey=`ir4anki.authoring.page.${selected.document_id}.${selected.revision_id}.${selected.page}`
   // Trace ID is only a hint for its original page; paging never retargets it.
   let id:string|undefined=existingId
   if(!newProject&&!id&&props.authoringId){
    const traced=await lectureRequest<OcclusionDraft>('/authoring/'+props.authoringId)
    if(samePage(selected,traced.source))id=traced.authoring_id
   }
   if(!newProject&&!id)try{id=localStorage.getItem(localKey)??undefined}catch{}
   const draft=!newProject&&id?await lectureRequest<OcclusionDraft>('/authoring/'+id):await lectureRequest<OcclusionDraft>('/authoring','POST',{source:selected})
   if(!samePage(selected,draft.source))throw new Error('已保存工程属于其他文档、版本或页；请核对工程，不会自动改挂')
   if(draft.ownership_error)throw new Error(draft.ownership_error)
   if(ticket!==generation||!samePage(selected,locator(props.source,page())))return
   try{localStorage.setItem(localKey,draft.authoring_id)}catch{}
   setEditor(draft)
  }catch(e){if(ticket===generation)setError((e as Error).message)}finally{if(ticket===generation)setLoading(false)}
 }
 const authorEvent=(event:Event)=>{if(split())return;if((event as CustomEvent).detail==='qa')openQa();else void occlusion()}
 onMount(()=>window.addEventListener('lecture-author',authorEvent))
 onCleanup(()=>{++generation;window.removeEventListener('lecture-author',authorEvent)})
 createEffect(on(sourceIdentity,()=>{++generation;setLoading(false);setPage(sourcePage(props.source));setStart(props.source.page_start);setEnd(props.source.page_start);splitPage=undefined;setSplit(false)}))
 createEffect(()=>props.onPage?.(locator(props.source,page())))
 const validSplit=()=>Number.isInteger(start())&&Number.isInteger(end())&&start()>=props.source.page_start&&end()>=start()&&end()<=props.source.page_end
 async function confirmSplit(){if(!split()||loading()||props.busy||!validSplit()||props.source.status==='container')return;const ticket=generation,parent={...props.source},operation=props.onSplitBegin?.();setLoading(true);setError('');try{const result=await lectureRequest<{chunks:PageSource[]}>('/split','POST',{segment_id:props.source.segment_id,revision_id:props.source.revision_id,page_start:start(),page_end:end(),expected_version:props.source.version,request_id:crypto.randomUUID()});if(ticket!==generation||operation&&!operation.current())return;if(!result.chunks.length)throw new Error('分割未返回选中子段');setSplit(false);props.onSplit?.(parent,result.chunks);props.onChanged()}catch(e){if(ticket===generation)setError((e as Error).message)}finally{operation?.finish();if(ticket===generation)setLoading(false)}}
 return <StudyCardShell lecture stage="reading" source={`${props.source.path} · 第 ${props.source.page_start}–${props.source.page_end} 页`} toolbar={<StudyToolbar label="阅读操作">
  <Show when={!props.onBack}><md-icon-button aria-label="撤销上次评分" disabled={!props.undoEnabled||props.busy} onClick={props.onUndo}><md-icon><IconUndo/></md-icon></md-icon-button><md-icon-button aria-label="退出本轮" disabled={props.busy} onClick={props.onExitRound}><md-icon><IconClose/></md-icon></md-icon-button></Show>
  <md-icon-button aria-label="添加问答" disabled={loading()||props.busy||qa()||!!editor()||split()} onClick={()=>openQa()}><md-icon><IconAdd/></md-icon></md-icon-button>
  <md-icon-button aria-label="新建图片挖空" title="新建图片挖空" disabled={loading()||props.busy||!!editor()||qa()} onClick={()=>void occlusion(true)}><md-icon><IconPassword/></md-icon></md-icon-button>
  <md-icon-button aria-label="按页书签分割" title="按页书签分割" disabled={loading()||props.busy||!!editor()||qa()||props.source.status==='container'} onClick={()=>{if(splitPage!==page()){setStart(page());setEnd(page());splitPage=page()}setError('');setSplit(true)}}><md-icon><IconContentCut/></md-icon></md-icon-button>
  <Show when={props.onBack}><md-icon-button aria-label="回到复习" onClick={props.onBack}><md-icon><IconArrowBack/></md-icon></md-icon-button></Show>
 </StudyToolbar>} dock={<Show when={readingDockVisible({back:!!props.onBack,occlusion:!!editor(),qa:qa(),annotation:annotationEditing()})}><StudyActionDock><md-text-button disabled={props.busy} onClick={props.onExit}>结束阅读</md-text-button><md-text-button disabled={props.busy} onClick={props.onSkip}>无需制卡，跳过</md-text-button><md-text-button disabled={props.busy} onClick={props.onNext}>下一张（稍后继续）</md-text-button><md-filled-button onClick={props.onComplete} disabled={props.busy}>制卡完成</md-filled-button></StudyActionDock></Show>}>
  <Show when={props.source.ownership_error}><p class="lecture-error" role="alert">{props.source.ownership_error}</p></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show><Show when={loading()}><p role="status">正在准备课件…</p></Show>
  <div style={{display:editor()?'none':undefined}}>

   <Show when={split()}><SplitDialog title="确认按页书签分割" busy={loading()||props.busy} disabled={!validSplit()||props.source.status==='container'} confirmLabel="确认书签分割" onCancel={()=>setSplit(false)} onConfirm={()=>void confirmSplit()}>
    <p>选中页成为当前片段，未读尾部留待后续轮次。</p>
    <div class="split-page-range"><label>开始页<input type="number" aria-label="分割开始页" disabled={loading()||props.busy} min={props.source.page_start} max={props.source.page_end} value={start()} onInput={e=>setStart(Number(e.currentTarget.value))}/></label><label>结束页<input type="number" aria-label="分割结束页" disabled={loading()||props.busy} min={start()} max={props.source.page_end} value={end()} onInput={e=>setEnd(Number(e.currentTarget.value))}/></label></div>
    <Show when={!validSplit()}><p role="alert">请选择当前片段内有效的起止页（{props.source.page_start}–{props.source.page_end} 页）</p></Show>
    <Show when={validSplit()}><div class="lecture-split-previews"><figure><img src={pageResourceUrl(props.source,start(),'thumbnail')} onError={()=>setError('首末页预览加载失败，请重新确认分割')} alt={`首：第 ${start()} 页`}/><figcaption>首 · {start()}</figcaption></figure><figure><img src={pageResourceUrl(props.source,end(),'thumbnail')} onError={()=>setError('首末页预览加载失败，请重新确认分割')} alt={`尾：第 ${end()} 页`}/><figcaption>尾 · {end()}</figcaption></figure></div></Show>
    <p>第 {start()}–{end()} 页 · 未读尾部：{end()<props.source.page_end?`${end()+1}–${props.source.page_end} 页`:'无'}</p>
    <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show>
   </SplitDialog></Show>
   <PdfSourceViewer onAnnotationEditing={setAnnotationEditing} initialPage={page()} source={props.source} active={!editor()} onPage={setPage} onQuote={qa()?undefined:(text,source)=>openQa(text,source)} onCloze={qa()?undefined:(text,source)=>props.onTextCloze?.(source,text)}/>
  </div>
  <Show when={editor()} keyed>{initial=><OcclusionEditor initial={initial} onClose={returnReader} onPublished={props.onPublished??props.onChanged}/>}</Show>
  <Show when={qa()&&qaSource()}><LectureQuestionAnswer source={qaSource()!} questionSeed={qaSeed()} path={props.source.path} onClose={returnReader} onAdded={props.onChanged}/></Show>
 </StudyCardShell>
}
