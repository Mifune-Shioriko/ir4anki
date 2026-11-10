import {requestDraftLeave} from '../lib/draft-leave'
import {ownPdfLoadingTask} from '../lib/pdf-lifetime'
import {commitPage,sourceKey,sourcePage,currentText} from '../lib/lecture-session'
import {createMemo,createEffect,on,createSignal,onCleanup,Show,For} from 'solid-js'
import {getDocument,GlobalWorkerOptions,TextLayer,type PDFDocumentProxy,type RenderTask} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {IconArrowBack,IconChevronRight} from './icons'
import {PageAnnotation} from './PageAnnotation'
import {locator,pageResourceUrl,revisionPdfUrl,type PageSource,type PageLocator} from '../lib/lecture-api'
GlobalWorkerOptions.workerSrc=workerUrl
export function PdfSourceViewer(props:{source:PageSource;onAnnotationEditing?:(editing:boolean)=>void;active?:boolean;onPage?:(n:number)=>void;initialPage?:number;annotations?:boolean;onQuote?:(text:string,source:PageLocator)=>void;onCloze?:(text:string,source:PageLocator)=>void}){
 const [page,setPage]=createSignal(sourcePage(props.source,props.initialPage)),[zoom,setZoom]=createSignal(1),[fit,setFit]=createSignal<'width'|'page'>('width'),[count,setCount]=createSignal(0),[loading,setLoading]=createSignal(true),[error,setError]=createSignal(''),[thumbs,setThumbs]=createSignal(false)
 const [pageInput,setPageInput]=createSignal(String(page())),[pageError,setPageError]=createSignal('')
 createEffect(on(page,n=>setPageInput(String(n))))
 let host!:HTMLDivElement,canvas!:HTMLCanvasElement,text!:HTMLDivElement,doc:PDFDocumentProxy|undefined,render:RenderTask|undefined,layer:TextLayer|undefined,sequence=0,loadSequence=0,observer:ResizeObserver|undefined
 const documentIdentity=createMemo(()=>props.source.document_id+'/'+props.source.revision_id)
 const pageIdentity=createMemo(()=>sourceKey(props.source)+'/'+props.initialPage)
 const annotationSource=createMemo(()=>locator(props.source,page()),undefined,{equals:(a,b)=>JSON.stringify(a)===JSON.stringify(b)})
 async function draw(){
  if(!doc||!host||props.active===false||host.clientWidth<1)return
  const token=++sequence;render?.cancel();layer?.cancel();setLoading(true);setError('')
  try{
   const p=await doc.getPage(page());if(token!==sequence){p.cleanup();return}
   try{
   const natural=p.getViewport({scale:1}),available=host.clientWidth
   const scale=(fit()==='width'?available/natural.width:Math.min(available/natural.width,Math.max(360,window.innerHeight*.66)/natural.height))*zoom()
   const viewport=p.getViewport({scale}),dpr=window.devicePixelRatio||1
   canvas.width=Math.ceil(viewport.width*dpr);canvas.height=Math.ceil(viewport.height*dpr);canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px'
   text.replaceChildren();text.style.width=viewport.width+'px';text.style.height=viewport.height+'px';text.style.setProperty('--scale-factor',String(scale));text.style.setProperty('--total-scale-factor',String(scale))
   const task=p.render({canvasContext:canvas.getContext('2d')!,canvas,viewport,transform:dpr===1?undefined:[dpr,0,0,dpr,0,0]});render=task;await task.promise
   if(token!==sequence)return
   await currentText(()=>p.getTextContent(),()=>token===sequence,async content=>{
    const localLayer=new TextLayer({textContentSource:content,container:text,viewport});layer=localLayer;
    await localLayer.render();if(token!==sequence)localLayer.cancel()
   })
   if(token===sequence)setLoading(false)
   }finally{p.cleanup()}
  }catch(e){if(token===sequence&&(e as Error).name!=='RenderingCancelledException'){setLoading(false);setError((e as Error).message)}}
 }
 createEffect(on(documentIdentity,()=>{
  const source=props.source,token=++loadSequence;setPage(sourcePage(source,props.initialPage));setCount(0);setLoading(true)
  const task=getDocument({url:revisionPdfUrl(source),cMapUrl:'/assets/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/assets/pdfjs/standard_fonts/',wasmUrl:'/assets/pdfjs/wasm/',isEvalSupported:false})
  const lifetime=ownPdfLoadingTask(task,value=>{if(token===loadSequence){doc=value;setCount(value.numPages);void draw()}},e=>{if(token===loadSequence){setError((e as Error).message);setLoading(false)}},e=>{
   // Cleanup errors still have an owner and diagnostic context, including
   // after unmount when this source no longer has a visible error region.
   console.error('PDF resource release failed',source.document_id,source.revision_id,e)
   if(token===loadSequence){setError('课件资源释放失败：'+(e as Error).message);setLoading(false)}
  })
  onCleanup(()=>{++sequence;render?.cancel();layer?.cancel();doc=undefined;void lifetime.dispose()})
 }))
 createEffect(()=>{page();zoom();fit();props.active;void draw()})
 createEffect(()=>{if(!host)return;observer=new ResizeObserver(()=>void draw());observer.observe(host);onCleanup(()=>observer?.disconnect())})
 onCleanup(()=>{++loadSequence;++sequence;render?.cancel();layer?.cancel();canvas.width=canvas.height=0})
 function jump(n:number){if(n!==page()&&!requestDraftLeave()){setPageInput(String(page()));return}setPageError('');if(Number.isInteger(n)&&n>=props.source.page_start&&n<=Math.min(props.source.page_end,count())){setPage(n);setPageInput(String(n));props.onPage?.(n)}}
 function submitPage(){const result=commitPage(pageInput(),page(),props.source.page_start,Math.min(props.source.page_end,count()));setPageInput(String(result.page));if(result.error)setPageError(result.error);else jump(result.page)}
 createEffect(on(pageIdentity,()=>{const n=sourcePage(props.source,props.initialPage);setPage(n);setPageInput(String(n));setPageError('');props.onPage?.(n)}))
 return <section class="pdf-source" aria-label="PDF 课件" onKeyDown={e=>{if((e.target as HTMLElement).closest('button,input,md-icon-button,md-text-button,md-filled-tonal-button'))e.stopPropagation()}}>
  <div class="lecture-controls lecture-pager" role="toolbar" aria-label="课件页导航">
   <md-icon-button class="lecture-page-step" aria-label="上一页" disabled={!count()||page()<=props.source.page_start} onClick={()=>jump(page()-1)}><md-icon><IconArrowBack/></md-icon></md-icon-button>
   <label class="lecture-page-input"><input aria-label="页码" disabled={!count()} aria-invalid={!!pageError()} type="number" min={props.source.page_start} max={props.source.page_end} value={pageInput()} onInput={e=>{setPageInput(e.currentTarget.value);setPageError('')}} onBlur={submitPage} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();e.stopPropagation();submitPage()}}}/> <span>/ {count()||'…'} 页</span></label>
   <md-icon-button class="lecture-page-step" aria-label="下一页" disabled={!count()||page()>=props.source.page_end} onClick={()=>jump(page()+1)}><md-icon><IconChevronRight/></md-icon></md-icon-button>
   <details class="lecture-options"><summary aria-label="页面视图">视图</summary><div role="group" aria-label="页面显示">
    <md-text-button onClick={()=>{setFit('width');setZoom(1)}}>适合宽度</md-text-button><md-text-button onClick={()=>{setFit('page');setZoom(1)}}>适合页面</md-text-button>
    <label>缩放 <input aria-label="页面缩放" type="range" min="0.5" max="3" step="0.1" value={zoom()} onInput={e=>setZoom(Number(e.currentTarget.value))}/></label>
    <md-text-button onClick={()=>setThumbs(!thumbs())}>缩略图</md-text-button>
   </div></details>
  </div>
  <Show when={pageError()}><p class="lecture-page-error lecture-error" role="alert">{pageError()}</p></Show>
  <Show when={thumbs()}><div class="lecture-thumbnails" aria-label="按需页面缩略图"><For each={Array.from({length:Math.min(12,props.source.page_end-props.source.page_start+1)},(_,i)=>Math.max(props.source.page_start,Math.min(page()-5,props.source.page_end-11))+i).filter(n=>n<=props.source.page_end)}>{n=><button aria-label={`跳转第 ${n} 页`} aria-current={page()===n?'page':undefined} onClick={()=>jump(n)}><img loading="lazy" src={pageResourceUrl(props.source,n,'thumbnail')} onError={()=>setError('缩略图加载失败，请重试课件')} alt={`第 ${n} 页缩略图`}/><span>第 {n} 页</span></button>}</For></div></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p><md-text-button onClick={()=>void draw()}>重试</md-text-button></Show>
  <Show when={loading()}><p class="lecture-loading" role="status">正在读取第 {page()} 页…</p></Show>
  <div ref={host} class="pdf-viewport"><div class="pdf-page"><canvas ref={canvas} aria-label={`第 ${page()} 页课件`}/><div ref={text} class="textLayer"/></div></div>
  <Show when={props.annotations!==false&&count()}><Show when={annotationSource()} keyed>{source=><PageAnnotation onEditingChange={props.onAnnotationEditing} source={source} onQuote={props.onQuote} onCloze={props.onCloze}/>}</Show></Show>
 </section>
}
