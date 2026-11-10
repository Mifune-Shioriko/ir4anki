import {createEffect,createMemo,createSignal,on,onCleanup,For,Show} from 'solid-js'
import {getDocument,GlobalWorkerOptions,TextLayer,type PDFDocumentProxy,type RenderTask} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {ownPdfLoadingTask} from '../lib/pdf-lifetime'
import {pageAspect,visiblePages} from '../lib/pdf-window'
import {lectureRequest,locator,revisionPdfUrl,type PageSource,type LectureRevision} from '../lib/lecture-api'
import {PageAnnotation} from './PageAnnotation'
GlobalWorkerOptions.workerSrc=workerUrl

/** One page's resources live only while its slot is in the bounded window. */
function ReferenceCanvas(props:{doc:PDFDocumentProxy;page:number;width:number}){
 let canvas!:HTMLCanvasElement,text!:HTMLDivElement,render:RenderTask|undefined,layer:TextLayer|undefined,sequence=0
 const [error,setError]=createSignal(''),[ready,setReady]=createSignal(false),[retry,setRetry]=createSignal(0)
 createEffect(()=>{
  const width=props.width,doc=props.doc,n=props.page,previous=render;retry()
  const token=++sequence;setReady(false);setError('')
  async function draw(){
   let page:Awaited<ReturnType<PDFDocumentProxy['getPage']>>|undefined
   try{
    await previous?.promise.catch(()=>{});if(token!==sequence)return
    page=await doc.getPage(n);if(token!==sequence)return
    const natural=page.getViewport({scale:1}),viewport=page.getViewport({scale:width/natural.width})
    const dpr=Math.min(window.devicePixelRatio||1,2)
    canvas.width=Math.ceil(viewport.width*dpr);canvas.height=Math.ceil(viewport.height*dpr)
    canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px'
    text.style.setProperty('--scale-factor',String(viewport.scale));text.style.setProperty('--total-scale-factor',String(viewport.scale))
    const task=page.render({canvas,canvasContext:canvas.getContext('2d')!,viewport,transform:dpr===1?undefined:[dpr,0,0,dpr,0,0]});render=task
    await task.promise;if(token!==sequence)return
    const content=await page.getTextContent();if(token!==sequence)return
    text.replaceChildren();layer=new TextLayer({textContentSource:content,container:text,viewport});await layer.render()
    if(token===sequence)setReady(true)
   }catch(e){if(token===sequence&&(e as Error).name!=='RenderingCancelledException')setError((e as Error).message)}
   finally{page?.cleanup()}
  }
  if(width>0)void draw()
  onCleanup(()=>{++sequence;render?.cancel();layer?.cancel();canvas.width=canvas.height=0;text.replaceChildren()})
 })
 return <div class="pdf-page pdf-reference-raster" data-ready={ready()}>
  <canvas ref={canvas} aria-label={`参考第 ${props.page} 页`}/><div ref={text} class="textLayer"/>
  <Show when={error()}><div class="pdf-page-error" role="alert">第 {props.page} 页：{error()}<md-text-button onClick={()=>setRetry(n=>n+1)}>重试此页</md-text-button></div></Show>
 </div>
}

/** Full document reference. Scrolling never writes reading/flow state. */
export function PdfReferenceViewer(props:{source:PageSource;active?:boolean;plain?:boolean}){
 const [doc,setDoc]=createSignal<PDFDocumentProxy>(),[revision,setRevision]=createSignal<LectureRevision>(),[error,setError]=createSignal(''),[retry,setRetry]=createSignal(0)
 const [width,setWidth]=createSignal(0),[windowPages,setWindowPages]=createSignal<number[]>([],{equals:(a,b)=>a.length===b.length&&a.every((n,i)=>n===b[i])}),[editingPage,setEditingPage]=createSignal<number>()
 let scroll!:HTMLDivElement,frame=0,anchorFrame=0,generation=0,aligned='',measuredWidth=0,alive=true
 let viewAnchor:{page:number;fraction:number}|undefined
 let viewportAnchor:{page:number;top:number;bottom:boolean}|undefined,manualBrowse=false
 const slots=new Map<number,HTMLElement>(),annotationHeights=new Map<number,number>()
 const documentKey=createMemo(()=>props.source.document_id+'/'+props.source.revision_id)
 const anchorKey=createMemo(()=>documentKey()+'/'+props.source.segment_id)
 const schedule=()=>{if(alive&&!frame)frame=requestAnimationFrame(measure)}
 function captureViewport(){
  if(!scroll||!scroll.clientHeight)return
  const top=scroll.getBoundingClientRect().top
  const first=[...slots].find(([,el])=>el.getBoundingClientRect().bottom>top)
  if(first)viewportAnchor={page:first[0],top:first[1].getBoundingClientRect().top-top,bottom:manualBrowse&&scroll.scrollHeight-scroll.clientHeight-scroll.scrollTop<=2}
 }
 function restoreViewport(){
  if(!viewportAnchor||!aligned||props.active===false||!scroll.clientHeight)return
  let desired=scroll.scrollTop
  if(viewportAnchor.bottom)desired=scroll.scrollHeight-scroll.clientHeight
  else{
   const target=slots.get(viewportAnchor.page)
   if(target)desired+=target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-viewportAnchor.top
  }
  if(Math.abs(desired-scroll.scrollTop)>0.01)scroll.scrollTop=desired
 }
 const browsing=()=>{manualBrowse=true;aligned=anchorKey()}

 function measure(){
  frame=0;if(!scroll||props.active===false||scroll.clientWidth===0){setWindowPages([]);return}
  const nextWidth=Math.max(0,scroll.clientWidth-parseFloat(getComputedStyle(scroll).paddingLeft)-parseFloat(getComputedStyle(scroll).paddingRight))
  const resized=measuredWidth>0&&nextWidth!==measuredWidth
  if(resized&&viewAnchor){
   const target=slots.get(viewAnchor.page)
   if(target)scroll.scrollTop=target.offsetTop+viewAnchor.fraction*target.getBoundingClientRect().height
  }
  measuredWidth=nextWidth;setWidth(nextWidth)
  const top=scroll.getBoundingClientRect().top
  const rects=[...slots].map(([page,el])=>{const r=el.getBoundingClientRect();return {page,top:r.top-top,bottom:r.bottom-top}})
  const first=rects.find(r=>r.bottom>0)
  if(first)viewAnchor={page:first.page,fraction:-first.top/(first.bottom-first.top)}
  // A queued frame can run after note growth but before its ResizeObserver.
  // Only initial layout/explicit width repositioning may establish an anchor
  // here; actual scroll and source alignment establish their own authority.
  if(!viewportAnchor||resized)captureViewport()
  setWindowPages(visiblePages(rects,scroll.clientHeight))
 }
 createEffect(on(()=>[documentKey(),retry()] as const,()=>{
  const source=props.source,token=++generation,controller=new AbortController();setDoc(undefined);setRevision(undefined);setError('');setEditingPage(undefined);aligned='';measuredWidth=0;viewAnchor=undefined;viewportAnchor=undefined;manualBrowse=false;annotationHeights.clear()
  const task=getDocument({url:revisionPdfUrl(source),cMapUrl:'/assets/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/assets/pdfjs/standard_fonts/',wasmUrl:'/assets/pdfjs/wasm/',isEvalSupported:false})
  const lifetime=ownPdfLoadingTask(task,value=>{if(token===generation){setDoc(value);schedule()}},e=>{if(token===generation)setError((e as Error).message)},e=>console.error('PDF reference release failed',e))
  lectureRequest<LectureRevision>(`/${source.document_id}/revisions/${source.revision_id}`,'GET',undefined,controller.signal).then(value=>{
   if(token!==generation)return
   if(!value.pages?.length)throw new Error('课件缺少页面尺寸，请重新导入完整 PDF')
   value.pages.forEach(pageAspect);setRevision(value);schedule()
  }).catch(e=>{if(token===generation)setError((e as Error).message)})
  onCleanup(()=>{++generation;controller.abort();setDoc(undefined);void lifetime.dispose()})
 }))
 createEffect(()=>{
  const key=anchorKey(),active=props.active!==false;revision();doc()
  if(active&&revision()&&doc()&&aligned!==key){
   const token=generation
   anchorFrame=requestAnimationFrame(()=>{
    anchorFrame=0
    if(token!==generation)return
    if(props.active===false||key!==anchorKey()||aligned===key||!scroll.clientWidth)return
    const target=slots.get(props.source.page??props.source.page_start)
    if(target){scroll.scrollTop=Math.max(0,target.offsetTop-parseFloat(getComputedStyle(scroll).paddingTop));aligned=key;captureViewport();schedule()}
   })
  }
  schedule()
  onCleanup(()=>{if(anchorFrame)cancelAnimationFrame(anchorFrame);anchorFrame=0})
 })
 createEffect(()=>{
  if(!scroll)return
  const observer=new ResizeObserver(schedule);observer.observe(scroll)
  onCleanup(()=>observer.disconnect())
 })
 onCleanup(()=>{alive=false;++generation;if(frame)cancelAnimationFrame(frame);if(anchorFrame)cancelAnimationFrame(anchorFrame);slots.clear()})
 function AnnotationSlot(p:{page:number}){
  let host!:HTMLDivElement
  // Reserve the entire flow-root wrapper, including note margin and padding.
  // Loading controls never replace the last known loaded extent in the cache.
  const [height,setHeight]=createSignal(annotationHeights.get(p.page)??93)
  createEffect(()=>{
   if(!windowPages().includes(p.page)&&editingPage()!==p.page)return
   const content=host.firstElementChild
   if(!content)return
   const observer=new ResizeObserver(()=>{
    // An observer notification alone is not permission to scroll: mounting a
    // cached note (or its loading controls) must leave native input untouched.
    if(!content.querySelector('.page-annotation[data-ready="true"]'))return
    const h=content.getBoundingClientRect().height,previous=height()
    annotationHeights.set(p.page,h)
    if(Math.abs(h-previous)<=0.01)return
    const affectsViewport=host.getBoundingClientRect().top<scroll.getBoundingClientRect().bottom
    setHeight(h)
    // Only a real retained-extent change at/above the viewport can displace it.
    if(affectsViewport)restoreViewport()
    schedule()
   })
   observer.observe(content);onCleanup(()=>observer.disconnect())
  })
  return <div ref={host} class="pdf-reference-annotation" style={{'min-height':height()+'px'}}>
   <Show when={windowPages().includes(p.page)||editingPage()===p.page}>
    <div class="pdf-reference-annotation-content"><PageAnnotation source={locator(props.source,p.page)} onEditingChange={editing=>setEditingPage(old=>editing?p.page:old===p.page?undefined:old)}/></div>
   </Show>
  </div>
 }

 return <div class="pdf-source pdf-reference" aria-label="完整 PDF 参考文档">
  <Show when={error()}><p role="alert" class="lecture-error">{error()}<md-text-button onClick={()=>setRetry(n=>n+1)}>重新载入参考文档</md-text-button></p></Show>
  <Show when={(!revision()||props.plain&&!doc())&&!error()}><p role="status">正在载入完整文档…</p></Show>
  <div ref={scroll} class="pdf-reference-scroll" tabIndex={0} aria-label="滚动完整 PDF 文档" onScroll={()=>{captureViewport();schedule()}} onWheel={browsing} onTouchMove={browsing} onPointerDown={e=>{if(e.target===scroll)browsing()}} onKeyDown={e=>{e.stopPropagation();if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))browsing()}}>
   <For each={revision()?.pages??[]}>{(geometry,i)=>{
    const page=i()+1
    return <article ref={el=>{slots.set(page,el);onCleanup(()=>slots.delete(page))}} class="pdf-reference-page" data-page={page} data-fragment={!props.plain&&page>=props.source.page_start&&page<=props.source.page_end}>
     <div class="pdf-reference-page-label">第 {page} 页<Show when={!props.plain}><span>{page>=props.source.page_start&&page<=props.source.page_end?'当前片段':''}</span></Show></div>
     <div class="pdf-reference-sheet" style={{'aspect-ratio':String(pageAspect(geometry))}}>
      <Show when={doc()&&width()>0&&windowPages().includes(page)} fallback={<div class="pdf-page-placeholder" aria-label={`第 ${page} 页待显示`}>第 {page} 页</div>}>
       <ReferenceCanvas doc={doc()!} page={page} width={width()}/>
      </Show>
     </div>
     <Show when={!props.plain}><AnnotationSlot page={page}/></Show>
    </article>
   }}</For>
  </div>
 </div>
}
