import {AuthoringWriter,authoringContent,publicationDecision,clearRejectedPublication,type PublicationStatus,type WriterState} from '../lib/authoring-writer'
import {persistWriterDraft} from '../lib/persistent-draft'
import {createMemo,createEffect,on,createSignal,onCleanup,For,Show} from 'solid-js'
import {MarkdownField} from './MarkdownField'
import {lectureRequest,type OcclusionDraft,type Publication,type Mask,pageResourceUrl,verifiedPng} from '../lib/lecture-api'
import {rect,tuple,between,validateRect,withinCrop,type Point} from '../lib/page-geometry'
export function OcclusionEditor(props:{initial:OcclusionDraft;onClose:()=>void;onPublished:()=>void}){
 const [draft,setDraft]=createSignal<OcclusionDraft>(structuredClone(props.initial)),[tool,setTool]=createSignal<'select'|'mask'|'crop'>('select'),[selected,setSelected]=createSignal(''),[error,setError]=createSignal(''),[status,setStatus]=createSignal('草稿'),[busy,setBusy]=createSignal(false),[zoom,setZoom]=createSignal(1),[preview,setPreview]=createSignal<string[]>([]),[result,setResult]=createSignal<Publication|null>(props.initial.pending_operation??null),[pendingRect,setPendingRect]=createSignal<number[]|null>(null)
 const [conflictDraft,setConflictDraft]=createSignal<OcclusionDraft|null>(null)
 const [rejectedConflict,setRejectedConflict]=createSignal(false),[serverVersion,setServerVersion]=createSignal(props.initial.version)
 let retained:{key:string;raw:string}|undefined
 const writerId=crypto.randomUUID()
 const key='ir4anki.occlusion.'+props.initial.authoring_id
 let viewport!:HTMLDivElement,disposed=false,saveTimer:ReturnType<typeof setTimeout>|undefined
 const [imageReady,setImageReady]=createSignal(false),[imageError,setImageError]=createSignal(''),[bitmap,setBitmap]=createSignal(''),[publicationRecovery,setPublicationRecovery]=createSignal(false)
 const [writerState,setWriterState]=createSignal<WriterState>({sequence:0,savedSequence:0,version:props.initial.version,dirty:false,locked:false,disposed:false,saving:false})
 const [historyTick,setHistoryTick]=createSignal(0)
 let undo:OcclusionDraft[]=[],redo:OcclusionDraft[]=[],gesture:{start:Point;before:OcclusionDraft;mask?:Mask;resize?:boolean}|null=null
 function persistSnapshot(value:OcclusionDraft,state:WriterState,reason:string){
  if(conflictDraft())return
  try{
   persistWriterDraft(localStorage,key,value,writerId,state,reason==='edit'&&!disposed)
  }catch{if(!disposed)setError('浏览器草稿存储不可用，请先保存到服务器再离开')}
 }
 const writer=new AuthoringWriter(props.initial,(value,expected_version)=>lectureRequest<OcclusionDraft>(`/authoring/${value.authoring_id}`,'PUT',{value,expected_version}),(value,state,reason)=>{
  persistSnapshot(value,state,reason)
  if(disposed)return
  if(reason==='ack'&&gesture)setDraft(current=>({...current,version:value.version,published_version:value.published_version,note_links:value.note_links}))
  else setDraft(value)
  setWriterState(state)
  if(reason==='ack')setStatus(state.dirty?'草稿已保留':'草稿已保存')
 })
 try{
  setPublicationRecovery(!!localStorage.getItem(key+'.publish'))
  const fork=localStorage.getItem(key+'.rejected'),forkRaw=fork?localStorage.getItem(fork):null
  const baseRaw=localStorage.getItem(key),raw=baseRaw??forkRaw,recovered:OcclusionDraft|null=JSON.parse(raw??'null')
  const usingRejected=!baseRaw&&!!forkRaw
  if(usingRejected)retained={key:fork!,raw:forkRaw!}
  if(recovered){
   if(usingRejected){setConflictDraft(recovered);setRejectedConflict(true);setError('上次发布已确认未创建操作；本地内容仍保留，请检查最新版本并明确合并')}
   else if(authoringContent(recovered)===authoringContent(props.initial)){if(localStorage.getItem(key)===raw)localStorage.removeItem(key)}
   else if(recovered.version===props.initial.version&&!props.initial.pending_operation&&!publicationRecovery())writer.edit(recovered)
   else{setConflictDraft(recovered);setError('发现其他版本或冻结发布的本地草稿，请比较后明确合并；旧草稿不会被覆盖')}
  }
 }catch{setError('无法读取本地草稿；离开前请确认已保存')}
 const frozen=()=>publicationRecovery()||!!(result()&&result()!.status!=='complete')
 writer.setLocked(frozen())
 const mutationLocked=()=>busy()||frozen()||!!conflictDraft()||!imageReady()
 const stopTimer=()=>{if(saveTimer){clearTimeout(saveTimer);saveTimer=undefined}}
 function failure(e:unknown){if(disposed)return;setError((e as Error).message);setStatus('保存失败，草稿已保留');if((e as Error).message.includes('版本冲突'))setConflictDraft(writer.value())}
 async function flush(){
  if(conflictDraft())throw new Error('请先处理草稿版本冲突；本地内容已保留')
  if(writer.state().dirty&&!disposed)setStatus('保存草稿中…')
  return writer.flush()
 }
 function changed(next:OcclusionDraft,history=true){
  if(mutationLocked())return
  const before=writer.value();if(!writer.edit(next))return
  if(history){undo.push(before);redo=[]}
  setHistoryTick(v=>v+1);setPreview([]);setError('');setStatus('草稿已保留');stopTimer()
  saveTimer=setTimeout(()=>{saveTimer=undefined;void flush().catch(failure)},900)
 }
 async function close(){
  if(busy())return
  setBusy(true);stopTimer()
  try{if(!(rejectedConflict()&&!frozen()))await flush();if(!disposed)props.onClose()}catch(e){failure(e)}finally{if(!disposed)setBusy(false)}
 }
 function history(back:boolean){if(mutationLocked())return;const from=back?undo:redo,to=back?redo:undo;if(!from.length)return;to.push(structuredClone(draft()));changed(from.pop()!,false)}
 function pointer(e:PointerEvent):Point{const b=viewport.getBoundingClientRect(),c=rect(draft().crop);return {x:Math.max(c.x,Math.min(c.x+c.w,c.x+(e.clientX-b.left)/b.width*c.w)),y:Math.max(c.y,Math.min(c.y+c.h,c.y+(e.clientY-b.top)/b.height*c.h))}}
 function down(e:PointerEvent,mask?:Mask,resize=false){
  if(mutationLocked()||e.button!==0)return;e.preventDefault();e.stopPropagation();viewport.focus();viewport.setPointerCapture(e.pointerId)
  if(tool()==='select'&&!mask){setSelected('');return}
  gesture={start:pointer(e),before:structuredClone(draft()),mask,resize};if(mask)setSelected(mask.mask_id)
 }
 function move(e:PointerEvent){
  if(!gesture)return
  const point=pointer(e),g=gesture,c=rect(g.before.crop)
  if(tool()==='crop'||tool()==='mask'){setPendingRect(tuple(between(g.start,point)));return}
  if(!g.mask)return
  const old=rect(g.mask.rect),dx=point.x-g.start.x,dy=point.y-g.start.y
  const next=g.resize?{...old,w:Math.max(.003,Math.min(c.x+c.w-old.x,old.w+dx)),h:Math.max(.003,Math.min(c.y+c.h-old.y,old.h+dy))}:{...old,x:Math.max(c.x,Math.min(c.x+c.w-old.w,old.x+dx)),y:Math.max(c.y,Math.min(c.y+c.h-old.h,old.y+dy))}
  setDraft({...g.before,masks:g.before.masks.map(m=>m.mask_id===g.mask!.mask_id?{...m,rect:tuple(next)}:m)})
 }
 function finish(e:PointerEvent){
  if(!gesture)return;const g=gesture;gesture=null;viewport.releasePointerCapture(e.pointerId)
  try{
   const pending=pendingRect();setPendingRect(null)
   if(pending){const r=validateRect(rect(pending));if(r.w<.004||r.h<.004)return
    if(tool()==='crop'){
     if(draft().masks.some(m=>!withinCrop(rect(m.rect),r)))throw new Error('裁剪会排除现有遮罩，请先移动或删除这些遮罩')
     changed({...draft(),crop:pending})
    }else{const m={mask_id:crypto.randomUUID().replaceAll('-',''),rect:pending};changed({...draft(),masks:[...draft().masks,m]});setSelected(m.mask_id)}
    setTool('select')
   }else if(g.mask){const next=draft();setDraft(g.before);changed(next)}
  }catch(e){setDraft(g.before);setError((e as Error).message)}
 }
 const remove=()=>{if(selected())changed({...draft(),masks:draft().masks.filter(m=>m.mask_id!==selected())});setSelected('')}
 const cancelGesture=()=>{if(gesture)setDraft(gesture.before);gesture=null;setPendingRect(null)}
 function keydown(e:KeyboardEvent){
  e.stopPropagation();if((e.target as HTMLElement).closest('input,textarea,.cm-editor'))return
  if(e.key==='Escape'){e.preventDefault();cancelGesture();return}
  if(mutationLocked())return
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();history(!e.shiftKey);return}
  if((e.key==='Delete'||e.key==='Backspace')&&selected()){e.preventDefault();remove();return}
  if(e.key.startsWith('Arrow')&&selected()){
   e.preventDefault();const delta=e.shiftKey?.01:.001,c=rect(draft().crop)
   changed({...draft(),masks:draft().masks.map(m=>{if(m.mask_id!==selected())return m;const r=rect(m.rect);r.x=Math.max(c.x,Math.min(c.x+c.w-r.w,r.x+(e.key==='ArrowRight'?delta:e.key==='ArrowLeft'?-delta:0)));r.y=Math.max(c.y,Math.min(c.y+c.h-r.h,r.y+(e.key==='ArrowDown'?delta:e.key==='ArrowUp'?-delta:0)));return {...m,rect:tuple(r)}})})
  }
 }
 async function showPreview(){
  if(mutationLocked())return
  setBusy(true);stopTimer()
  try{await flush();if(disposed)return;const p=await lectureRequest<{images:string[]}>(`/authoring/${draft().authoring_id}/preview/${selected()||draft().masks[0]?.mask_id}`,'POST');if(!disposed)setPreview(p.images)}catch(e){failure(e)}finally{if(!disposed)setBusy(false)}
 }
 function retainRejected(requestId:string){
  const fork=key+'.rejected.'+requestId,raw=JSON.stringify(writer.value())
  localStorage.setItem(fork,raw);localStorage.setItem(key+'.rejected',fork);retained={key:fork,raw}
 }
 function clearRetained(){
  if(!retained)return
  if(localStorage.getItem(retained.key)===retained.raw)localStorage.removeItem(retained.key)
  if(localStorage.getItem(key+'.rejected')===retained.key)localStorage.removeItem(key+'.rejected')
  retained=undefined
 }
 async function publish(){
  if(busy()||!imageReady())return
  setBusy(true);setError('');stopTimer()
  let attempted:{version:number;request_id:string;confirmed_deletions:number[]}|null=null,attemptedRaw:string|null=null
  try{
   const existing=result()
   let request:{version:number;request_id:string;confirmed_deletions:number[]}|null=existing&&existing.status!=='complete'?null:JSON.parse(localStorage.getItem(key+'.publish')??'null')
   if(!request&&(!existing||existing.status==='complete')){
    await flush()
    if(disposed)return
    const removed=(draft().note_links??[]).filter(l=>!draft().masks.some(m=>m.mask_id===l.mask_id)).map(l=>l.note_id)
    if(removed.length&&!window.confirm('将永久删除这些准确的受管笔记：'+removed.join('、')+'。其余卡片保持原身份。确认删除？'))return
    request={version:writer.state().version,request_id:crypto.randomUUID(),confirmed_deletions:removed};localStorage.setItem(key+'.publish',JSON.stringify(request))
   }
   attempted=request;attemptedRaw=localStorage.getItem(key+'.publish')
   setPublicationRecovery(true);writer.setLocked(true)
   const response=existing&&existing.status!=='complete'
    ?await lectureRequest<Publication>(`/authoring/${draft().authoring_id}/resume/${existing.operation_id}`,'POST')
    :await lectureRequest<Publication>(`/authoring/${draft().authoring_id}/publish`,'POST',request)
   if(disposed)return
   setResult(response)
   if(response.status==='complete'){
    // Publication can finish after a previous browser disappeared. Always
    // rehydrate the authoritative version and mappings before editing/close.
    const latest=await lectureRequest<OcclusionDraft>('/authoring/'+draft().authoring_id)
    if(disposed)return
    writer.adopt(latest);writer.setLocked(false);setPublicationRecovery(false)
    localStorage.removeItem(key+'.publish');setStatus('已发布');props.onPublished()
   }else setStatus('部分完成，已保留逐项结果；可安全重试')
  }catch(e){
   failure(e)
   if(attempted&&!disposed){
    try{
     const status=await lectureRequest<PublicationStatus>('/authoring/'+draft().authoring_id+'/publication-status','POST',attempted)
     if(disposed)return
     setServerVersion(status.draft.version)
     const decision=publicationDecision(status)
     if(decision.kind==='merge'){
      // Preserve a separate durable local fork; never replace another writer's
      // normal browser draft. Clear only this exact proven-rejected marker.
      retainRejected(attempted.request_id)
      if(!attemptedRaw||!clearRejectedPublication(localStorage,key+'.publish',attemptedRaw))throw new Error('其他窗口已更改发布请求；保持冻结，请重新打开检查')
      setConflictDraft(writer.value());setRejectedConflict(true);setResult(null)
      writer.setLocked(false);setPublicationRecovery(false)
      setError('发布已确认未创建操作；本地内容已保留，请检查最新版本并明确合并')
      setStatus('发布未创建，等待版本合并')
     }else if(decision.kind==='resume'){
      setResult(decision.operation);writer.setLocked(true);setPublicationRecovery(true)
      setStatus('服务器发布已存在，保留原身份等待恢复')
     }else setStatus('发布结果尚未确认，保留原请求等待恢复')
    }catch(queryError){failure(queryError)}
   }
  }finally{if(!disposed)setBusy(false)}
 }
 async function mergeConflict(){
  const local=conflictDraft();if(!local)return
  setBusy(true)
  try{
   const latest=await lectureRequest<OcclusionDraft>('/authoring/'+draft().authoring_id)
   if(disposed)return
   if(latest.raster.raster_hash!==local.raster.raster_hash)throw new Error('基准图不同，不能自动合并；请复制本地草稿并保留原基准图')
   if(!window.confirm(`服务器版本 ${latest.version}，${latest.masks.length} 个遮罩；本地 ${local.masks.length} 个遮罩。确认已检查差异，以本地裁剪、遮罩和说明合并到最新版本？`))return
   if(latest.pending_operation)throw new Error('先恢复服务器未完成的发布，再合并本地草稿')
   setConflictDraft(null);writer.rebase(latest,local);await flush()
   if(disposed)return
   clearRetained();setRejectedConflict(false);setError('');setStatus('已合并，可重新发布')
  }catch(e){failure(e)}finally{if(!disposed)setBusy(false)}
 }

 function orderMask(direction:number){const i=draft().masks.findIndex(m=>m.mask_id===selected()),j=i+direction;if(i<0||j<0||j>=draft().masks.length)return;const masks=[...draft().masks];[masks[i],masks[j]]=[masks[j],masks[i]];changed({...draft(),masks})}
 const maskStyle=(r:number[])=>{const c=rect(draft().crop),m=rect(r);return {left:`${(m.x-c.x)/c.w*100}%`,top:`${(m.y-c.y)/c.h*100}%`,width:`${m.w/c.w*100}%`,height:`${m.h/c.h*100}%`}}
 const imageUrl=createMemo(()=>pageResourceUrl(draft().source,draft().source.page,'image',draft().raster.raster_hash))
 createEffect(on(imageUrl,url=>{
  const controller=new AbortController();let ownedUrl:string|undefined
  setImageReady(false);setImageError('');setBitmap('')
  void (async()=>{
   try{
    const response=await fetch(url,{signal:controller.signal}),blob=await verifiedPng(response,draft().raster.raster_hash)
    if(controller.signal.aborted||disposed)return
    ownedUrl=URL.createObjectURL(blob);setBitmap(ownedUrl)
   }catch(e){if(!controller.signal.aborted&&!disposed)setImageError((e as Error).message)}
  })()
  onCleanup(()=>{controller.abort();if(ownedUrl)URL.revokeObjectURL(ownedUrl)})
 }))
 onCleanup(()=>{disposed=true;stopTimer();writer.dispose()})
 return <section class="occlusion-editor" aria-label="图片遮罩编辑器" data-raster-ready={imageReady()} data-authoring-id={draft().authoring_id} data-page={draft().source.page} data-operation={result()?.operation_id??''} data-writer-state={JSON.stringify(writerState())} onKeyDown={keydown}>
  <div class="lecture-section-heading"><div><span class="lecture-eyebrow">图片挖空</span><h2>遮住答案，留下线索</h2></div><md-text-button disabled={busy()} onClick={()=>void close()}>保存草稿并返回</md-text-button></div>
  <Show when={!imageReady()&&!imageError()}><p role="status">正在验证并加载基准图…</p></Show>
  <Show when={imageError()}><p role="alert" class="lecture-error">{imageError()}（请关闭后重新打开课件重试）</p></Show>
  <Show when={frozen()}><p class="lecture-help" role="status">发布快照已冻结；先恢复未完成项，再修改内容。返回不会重复保存已确认的草稿。</p></Show>
  <fieldset disabled={mutationLocked()} inert={mutationLocked()} style={{border:'0',padding:'0',margin:'0','min-width':'0'}}>
  <div class="lecture-controls occlusion-tools" role="toolbar" aria-label="遮罩编辑工具">
   <div class="lecture-tool-group"><For each={[['select','选择 / 移动'],['mask','矩形遮罩'],['crop','裁剪']] as const}>{([value,label])=><button classList={{selected:tool()===value}} aria-pressed={tool()===value} onClick={()=>setTool(value)}>{label}</button>}</For></div>
   <md-text-button disabled={!selected()||busy()} onClick={remove}>删除选中</md-text-button>
   <details><summary>更多操作</summary><div class="lecture-more"><md-text-button onClick={()=>history(true)} disabled={(historyTick(),!undo.length)}>撤销编辑</md-text-button><md-text-button onClick={()=>history(false)} disabled={(historyTick(),!redo.length)}>重做编辑</md-text-button><md-text-button onClick={()=>changed({...draft(),crop:[0,0,1,1]})}>恢复整页</md-text-button><md-text-button disabled={!selected()} onClick={()=>orderMask(-1)}>所选向前</md-text-button><md-text-button disabled={!selected()} onClick={()=>orderMask(1)}>所选向后</md-text-button><label>缩放<input aria-label="画布缩放" type="range" min="1" max="3" step=".1" value={zoom()} onInput={e=>setZoom(Number(e.currentTarget.value))}/></label></div></details>
  </div>
  <p class="lecture-help">{tool()==='crop'?'拖动选择裁剪区域；已有遮罩必须完整保留。':tool()==='mask'?'拖动绘制一个矩形；画完自动回到选择。':'选择遮罩可移动，拖动右下角可缩放。方向键微调，Escape 取消手势。'}</p>
  <div class="occlusion-scroll"><div ref={viewport} tabIndex={0} role="group" aria-label="遮罩画布" class="occlusion-canvas" style={{width:`${zoom()*100}%`,'aspect-ratio':`${draft().raster.width*draft().crop[2]}/${draft().raster.height*draft().crop[3]}`}} onPointerDown={e=>down(e)} onPointerMove={move} onPointerUp={finish} onPointerCancel={cancelGesture}>
   <img draggable={false} src={bitmap()||undefined} data-resource-url={imageUrl()} data-raster-hash={draft().raster.raster_hash} alt="课件原始基准图" onLoad={e=>{if(disposed)return;const image=e.currentTarget;if(image.naturalWidth===draft().raster.width&&image.naturalHeight===draft().raster.height){setImageError('');setImageReady(true)}else setImageError('基准图像素尺寸不匹配，不能编辑或发布')}} onError={()=>{if(!disposed){setImageReady(false);setImageError('基准图无法解码，不能编辑或发布')}}} style={{width:`${100/draft().crop[2]}%`,left:`${-draft().crop[0]/draft().crop[2]*100}%`,top:`${-draft().crop[1]/draft().crop[3]*100}%`}}/>
   <For each={draft().masks}>{(mask,i)=><button class="occlusion-mask" classList={{'is-selected':selected()===mask.mask_id}} style={maskStyle(mask.rect)} aria-label={`遮罩 ${i()+1}`} aria-pressed={selected()===mask.mask_id} onFocus={()=>setSelected(mask.mask_id)} onPointerDown={e=>down(e,mask)}><span>{i()+1}</span><span class="occlusion-handle" role="button" aria-label={`缩放遮罩 ${i()+1}`} onPointerDown={e=>down(e,mask,true)}/></button>}</For>
   <Show when={pendingRect()}>{r=><div class="occlusion-pending" style={maskStyle(r())}/>}</Show>
  </div></div>
  <div class="lecture-controls"><label>考法 <select aria-label="遮罩考法" value={draft().mode} onChange={e=>changed({...draft(),mode:e.currentTarget.value as 'all'|'current'})}><option value="all">全遮考一</option><option value="current">只遮当前</option></select></label><span>{draft().masks.length} 个目标 · 每个目标一张卡</span></div>
  <details class="occlusion-extras"><summary>题目说明与答案补充</summary><div class="lecture-qa-fields"><MarkdownField label="题目说明" initial={draft().question} onChange={question=>changed({...draft(),question})}/><MarkdownField label="答案补充" initial={draft().answer} onChange={answer=>changed({...draft(),answer})}/></div></details>
  </fieldset>
  <Show when={conflictDraft()}>{local=><section class="lecture-conflict" aria-label="遮罩草稿版本冲突"><h3>本地草稿仍在此设备</h3><p>服务器 {serverVersion()} 版；本地 {local().version} 版。比较后再合并，不会自动改动 Anki。</p><details><summary>查看可复制的本地作者数据</summary><textarea aria-label="本地冲突草稿" readOnly value={JSON.stringify(local(),null,2)}/></details><md-filled-tonal-button disabled={busy()||frozen()} onClick={()=>void mergeConflict()}>检查最新版本并合并本地内容</md-filled-tonal-button></section>}</Show>
  <Show when={error()}><p role="alert" class="lecture-error">{error()}</p></Show>
  <Show when={result()}>{r=><ul class="lecture-results"><For each={r().items}>{item=><li>{item.action==='delete'?'删除':'目标'} {draft().masks.findIndex(m=>m.mask_id===item.mask_id)+1}：{item.status==='complete'?'已完成':item.error||'等待发布'}</li>}</For></ul>}</Show>
  <Show when={preview().length}><div class="lecture-qa-preview"><For each={preview()}>{(src,i)=><figure><figcaption>{i()?'答案':'问题'}</figcaption><img src={src} onError={()=>{if(!disposed)setError('后端预览图无法解码，请重新预览')}} alt={i()?'后端答案预览':'后端问题预览'}/></figure>}</For></div></Show>
  <footer class="lecture-save-dock"><span role="status">{status()}</span><md-text-button disabled={mutationLocked()||!draft().masks.length} onClick={()=>void showPreview()}>预览所选卡片</md-text-button><md-filled-button disabled={busy()||!imageReady()||(!draft().masks.length&&!(draft().note_links?.length))} onClick={()=>void publish()}>{result()?.status==='partial'?'恢复未完成项':'保存到 Anki'}</md-filled-button></footer>
 </section>
}
