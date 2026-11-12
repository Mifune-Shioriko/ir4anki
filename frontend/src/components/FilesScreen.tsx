import {Component,For,Show,createSignal,createMemo,onMount,onCleanup} from 'solid-js'
import {api,ApiError,UploadPartialError,UploadConflictError} from '../api'
import {lectureRequest} from '../lib/lecture-api'
import {FileOperations} from '../lib/fileOperations'
import {remapPath} from '../lib/files-state'
import {fileIntentId,basename,parentPath,normalizeTargets,visibleEntries,moveError,SerialBatch,editingEvent,type Entry,type BatchReport} from '../lib/files-browser'
import {requestDraftLeave} from '../lib/draft-leave'
import {partitionImports,type SkippedImport} from '../lib/file-import'
import type {ReadingFileSummary,ReadingCorpusFile} from '../types'
import {FileViewer,invalidateFileCache} from './FileViewer'
import {FilesLecturePreview} from './FilesLecturePreview'
import {FilesMenu} from './FilesMenu'
import {IconFolder,IconFolderOpen,IconDescription,IconArrowBack,IconChevronRight,IconRestartAlt,IconArrowDownward,IconArrowUpward,IconVerticalAlignTop,IconClose,IconMenuBook} from './icons'
import '../files.css'
interface Props{listedPaths?:()=>Set<string>;onListChange?:()=>void}
type ImportEntry={file:File;rel:string}
type ImportConflict={entries:ImportEntry[];dir:string;names:string[]}
type DialogState={kind:'rename'|'delete'|'mkdir';targets:Entry[]}
const batchKey='files-batch-report-v1',mkdirKey='files-mkdir-pending-v1'
const read=<T,>(key:string,fallback:T):T=>{try{return JSON.parse(sessionStorage.getItem(key)??'null')??fallback}catch{return fallback}}
const FRONTIER_LABEL:Record<string,string>={todo:'未读',active:'正在制卡',done:'制卡完成',skipped:'已跳过',held:'等卡片过预览池'}
export const FilesScreen:Component<Props>=(props)=>{
 const [files,setFiles]=createSignal<{path:string;title:string;revision?:string}[]>([]),[dirs,setDirs]=createSignal<string[]>([]),[membership,setMembership]=createSignal(new Set<string>())
 const [currentDir,setCurrentDir]=createSignal(read('files-directory','')),[selected,setSelected]=createSignal(new Set<string>()),[focused,setFocused]=createSignal(''),[query,setQuery]=createSignal('')
 const [previewPath,setPreviewPath]=createSignal<string|null>(null),[drawer,setDrawer]=createSignal(false),[loading,setLoading]=createSignal(true),[loadError,setLoadError]=createSignal(''),[message,setMessage]=createSignal('')
 const [importConflict,setImportConflict]=createSignal<ImportConflict|null>(null)
 const [skippedImports,setSkippedImports]=createSignal<SkippedImport[]>([])
 const [working,setWorking]=createSignal(false),[version,setVersion]=createSignal(0),[operationVersion,setOperationVersion]=createSignal(0)
 const [undoId,setUndoId]=createSignal<string|null>(read('files-undo-operation',null)),[dialog,setDialog]=createSignal<DialogState|null>(null),[value,setValue]=createSignal(''),[dialogError,setDialogError]=createSignal('')
 const [readingList,setReadingList]=createSignal<ReadingFileSummary[]>([])
 const [corpus,setCorpus]=createSignal<ReadingCorpusFile[]>([])
 const [readingLoading,setReadingLoading]=createSignal(true)
 const [readingActionBusy,setReadingActionBusy]=createSignal(false)
 const [readingError,setReadingError]=createSignal('')
 const [readingMessage,setReadingMessage]=createSignal('')
 const [removeTarget,setRemoveTarget]=createSignal<ReadingFileSummary|null>(null)
 const savedReport=read<BatchReport|null>(batchKey,null)
 const restored=savedReport&&Array.isArray(savedReport.items)?savedReport:null
 if(restored)restored.items=restored.items.map(i=>({...i,status:i.status==='running'?'unconfirmed':i.status}))
 const [report,setReport]=createSignal(restored),[mkdirPending,setMkdirPending]=createSignal<{id:string;path:string}|null>(read(mkdirKey,null)),[mkdirObserved,setMkdirObserved]=createSignal(false)
 const [drag,setDrag]=createSignal<Entry[]>([]),[drop,setDrop]=createSignal<string|null>(null),[externalDrop,setExternalDrop]=createSignal(false)
 const [history,setHistory]=createSignal<string[]>([])
 const owner={}
 let alive=true,generation=0,anchor='',list!:HTMLDivElement,input!:HTMLInputElement,folderInput!:HTMLInputElement,uploadDirectory='',suppressClick=false,returnFocus:HTMLElement|null=null
 const all=createMemo<Entry[]>(()=>[...dirs().map(path=>({path,isDir:true})),...files().map(f=>({path:f.path,isDir:false,revision:f.revision}))])
 const summaryMap=createMemo(()=>{const m=new Map<string,ReadingFileSummary>();for(const s of readingList())m.set(s.path,s);return m})
 const corpusMap=createMemo(()=>{const m=new Map<string,ReadingCorpusFile>();for(const c of corpus())m.set(c.path,c);return m})
 const isPdf=(s:ReadingFileSummary)=>s.kind==='pdf'
 const unitName=(s:ReadingFileSummary)=>(isPdf(s)?'页':'字')
 const totalUnits=(s:ReadingFileSummary)=>isPdf(s)?(s.total_pages??s.total_chunks):(s.total_chars??0)
 const readUnits=(s:ReadingFileSummary)=>isPdf(s)?(s.read_pages??(s.done+s.skipped+(s.background??0))):(s.read_chars??0)
 const activeUnits=(s:ReadingFileSummary)=>isPdf(s)?(s.active_pages??s.active):(s.active_chars??0)
 const readRatio=(s:ReadingFileSummary)=>{const tot=totalUnits(s);if(tot>0)return Math.min(1,readUnits(s)/tot);return s.total_chunks>0?(s.done+s.skipped+(s.background??0))/s.total_chunks:0}
 const activeRatio=(s:ReadingFileSummary)=>{const tot=totalUnits(s);if(tot>0)return Math.min(1-readRatio(s),activeUnits(s)/tot);return 0}
 const calcPercent=(part:number,total:number):number=>total>0?Math.min(100,Math.round((part/total)*100)):0
 const fmtNum=(n:number|undefined):string=>(n??0).toLocaleString('zh-CN')
 const frontierText=(s:ReadingFileSummary,f:NonNullable<ReadingFileSummary['frontier']>):string=>{const lineTag=(f.line_start??0)>1?`L${f.line_start} · `:'';const snippet=f.preview&&f.preview!==s.title?f.preview:f.title;return `${lineTag}${snippet}`}
 const rows=createMemo(()=>visibleEntries(all(),currentDir(),query(),'all'))
 const targets=()=>normalizeTargets(all().filter(t=>selected().has(t.path)))
 const unresolved=()=>report()?.items.some(i=>i.status==='running'||i.status==='unconfirmed')??false
 const busy=()=>{operationVersion();return working()||operations.isBusy()||!!mkdirPending()||unresolved()||(!loading()&&!!currentDir()&&!dirs().includes(currentDir()))}
 const remember=(key:string,v:unknown)=>{try{sessionStorage.setItem(key,JSON.stringify(v))}catch{setMessage('浏览器无法保留操作状态，请勿刷新或离开，先核对结果')}}
 const batch=new SerialBatch(r=>{if(alive){sessionStorage.setItem(batchKey,JSON.stringify(r));setReport(r)}})
 const applyRelocation=(old:string,next:string)=>{
  if(!alive){invalidateFileCache(old);invalidateFileCache(next);window.dispatchEvent(new CustomEvent('files-relocated',{detail:{oldPath:old,newPath:next,owner}}));return}
  invalidateFileCache(old);invalidateFileCache(next)
  setSelected(s=>new Set([...s].map(p=>remapPath(p,old,next))));setFocused(p=>remapPath(p,old,next));setCurrentDir(p=>remapPath(p,old,next));if(previewPath()&&remapPath(previewPath(),old,next)!==previewPath()){if(requestDraftLeave())setPreviewPath(p=>remapPath(p,old,next));else setMessage('文件迁移已确认；当前页笔记无法保留草稿，原编辑器仍保留。请先复制或保存，再关闭预览。')}
  setHistory(h=>h.map(p=>remapPath(p,old,next)));setMembership(s=>new Set([...s].map(p=>remapPath(p,old,next))))
  setFiles(fs=>fs.map(f=>({...f,path:remapPath(f.path,old,next)})));setDirs(ds=>ds.map(p=>remapPath(p,old,next)))
  if(typeof setReadingList==='function')setReadingList(ls=>ls.map(item=>({...item,path:remapPath(item.path,old,next)})))
  window.dispatchEvent(new CustomEvent('files-relocated',{detail:{oldPath:old,newPath:next,owner}}));setVersion(v=>v+1)
 }
 const operations=new FileOperations(api,sessionStorage,(result,old,request)=>{
  applyRelocation(old,result.path)
  if(!alive)return
  setUndoId(request.action==='undo'?null:result.operationId??undoId());remember('files-undo-operation',undoId())
 },()=>{if(alive)setOperationVersion(v=>v+1)})
 async function reloadReading(){
  if(!alive)return
  setReadingLoading(true)
  try{
   const [status,corp]=await Promise.all([
    api.readingStatus().catch(()=>({list:[] as ReadingFileSummary[]})),
    api.readingCorpus().catch(()=>({files:[] as ReadingCorpusFile[]}))
   ])
   if(!alive)return
   setReadingList(status.list);setCorpus(corp.files);setMembership(new Set(status.list.map(s=>s.path)))
  }finally{if(alive)setReadingLoading(false)}
 }
 const mutateReading=async(fn:()=>Promise<unknown>)=>{
  if(readingActionBusy()||working())return
  setReadingActionBusy(true);setReadingError('');setReadingMessage('')
  try{
   await fn();await reloadReading();props.onListChange?.();setReadingMessage('阅读清单已更新')
  }catch(e){setReadingError('操作失败：'+(e as Error).message)}finally{if(alive)setReadingActionBusy(false)}
 }
 const moveUp=(s:ReadingFileSummary)=>mutateReading(()=>api.readingListReorder({path:s.path}))
 const moveTop=(s:ReadingFileSummary)=>mutateReading(()=>api.readingListReorder({path:s.path,top:true}))
 const moveDown=(idx:number)=>{
  const items=readingList()
  if(idx<0||idx>=items.length-1)return
  const order=items.map(x=>x.path)
  const tmp=order[idx];order[idx]=order[idx+1];order[idx+1]=tmp
  mutateReading(()=>api.readingListReorder({order}))
 }
 const addFile=(f:{path:string;kind?:'pdf'|'markdown';document_id?:string;revision_id?:string},fresh=false)=>{
  const isPdf=f.kind==='pdf'||/\.pdf$/i.test(f.path)
  const corp=corpusMap().get(f.path)
  const docId=f.document_id||corp?.document_id,revId=f.revision_id||corp?.revision_id
  return mutateReading(()=>isPdf&&docId&&revId
   ?lectureRequest(`/${docId}/revisions/${revId}/read`,'POST')
   :api.readingListAdd(f.path,fresh))
 }
 const confirmRemove=()=>{
  const t=removeTarget();if(!t)return
  mutateReading(async()=>{await api.readingListRemove(t.path);setRemoveTarget(null)})
 }
 async function reload(){
  if(!alive)return
  const token=++generation;setLoading(true);setLoadError('')
  try{
   await operations.reconcile() // status only; never redispatch a stored intent
   const [d,m]=await Promise.all([api.filesList(),api.filesMembership()])
   if(!alive||token!==generation)return
   setFiles(d.files);setDirs(d.dirs??[]);setMembership(new Set(m.paths))
   if(typeof api.readingStatus==='function'&&typeof api.readingCorpus==='function'){
    try{
     const [status,corp]=await Promise.all([
      api.readingStatus().catch(()=>({list:[] as ReadingFileSummary[]})),
      api.readingCorpus().catch(()=>({files:[] as ReadingCorpusFile[]}))
     ])
     if(alive&&token===generation){setReadingList(status.list);setCorpus(corp.files);setReadingLoading(false)}
    }catch{}
   }
   if(mkdirPending())setMkdirObserved((d.dirs??[]).includes(mkdirPending()!.path))
   // Preserve unsafe editor/source even when server list no longer has its path.
   if(currentDir()&&!(d.dirs??[]).includes(currentDir())&&requestDraftLeave()){setCurrentDir('');remember('files-directory','')}
   if(previewPath()&&!d.files.some(f=>f.path===previewPath())&&requestDraftLeave())setPreviewPath(null)
  }catch(e){if(alive&&token===generation)setLoadError((e as Error).message)}finally{if(alive&&token===generation)setLoading(false)}
 }
 const foreignChange=(event:Event)=>{if((event as CustomEvent).detail?.owner!==owner)void reload()}
 onMount(()=>{window.addEventListener('files-relocated',foreignChange);window.addEventListener('files-deleted',foreignChange);window.addEventListener('lecture-documents-changed',foreignChange);void reload()})
 onCleanup(()=>{alive=false;++generation;batch.revoke();window.removeEventListener('files-relocated',foreignChange);window.removeEventListener('files-deleted',foreignChange);window.removeEventListener('lecture-documents-changed',foreignChange)})
 function focusRow(path=focused()){
  requestAnimationFrame(()=>{if(!alive)return;const row=Array.from(list.querySelectorAll<HTMLElement>('[data-entry]')).find(e=>e.dataset.entry===path);(row??list).focus()})
 }
 function navigate(path:string,back=false){
  if(!requestDraftLeave())return
  if(path!==currentDir()&&!back)setHistory(h=>[...h,currentDir()])
  setCurrentDir(path);remember('files-directory',path);setQuery('');setSelected(new Set<string>());setFocused('');setPreviewPath(null);setDrawer(false);anchor='';focusRow('')
 }
 function goBack(){const h=history();if(!h.length)return;if(!requestDraftLeave())return;setHistory(h.slice(0,-1));navigate(h[h.length-1],true)}
 function choose(t:Entry,event?:MouseEvent|KeyboardEvent){
  if(suppressClick){suppressClick=false;return}
  // Changing selection alone retains the current editor. Opening/closing is explicit.
  const toggle=!!event&&(event.ctrlKey||event.metaKey),range=!!event?.shiftKey
  setSelected(previous=>{
   if(range&&anchor){const paths=rows().map(x=>x.path),a=paths.indexOf(anchor),b=paths.indexOf(t.path);if(a>=0&&b>=0)return new Set([...(toggle?previous:[]),...paths.slice(Math.min(a,b),Math.max(a,b)+1)])}
   if(toggle){const next=new Set(previous);next.has(t.path)?next.delete(t.path):next.add(t.path);return next}
   return new Set([t.path])
  });if(!range)anchor=t.path;setFocused(t.path)
 }
 function preview(t:Entry){
  if(t.isDir){navigate(t.path);return}
  if(!requestDraftLeave())return
  setPreviewPath(p=>p===t.path?null:t.path)
 }
 function closePreview(){if(requestDraftLeave()){setPreviewPath(null);focusRow()}}
 function open(kind:DialogState['kind'],items=targets()){
  if(busy()||(kind!=='mkdir'&&!items.length))return
  if((kind==='rename'||kind==='delete')&&!requestDraftLeave())return
  returnFocus=document.activeElement as HTMLElement
  setValue(kind==='rename'?basename(items[0].path):'');setDialogError('');setDialog({kind,targets:normalizeTargets(items)})
 }
 function restoreFocus(){requestAnimationFrame(()=>{if(returnFocus?.isConnected)returnFocus.focus();else focusRow()})}
 function closeDialog(){if(working())return;setDialog(null);restoreFocus()}
 function uncertain(error:unknown){return !(error instanceof ApiError&&error.status>=400&&error.status<500)}
 async function runItems(action:string,items:Entry[],executor:(t:Entry)=>Promise<'confirmed'|'unconfirmed'>){
  if(busy()||!requestDraftLeave())return
  setWorking(true);setDialog(null)
  try{await batch.run(action,normalizeTargets(items),executor)}catch(e){if(alive)setMessage('操作状态无法保留，请核对后再继续：'+(e as Error).message)}finally{if(alive){setWorking(false);await reload();focusRow()}}
 }
 async function relocation(kind:'move'|'rename',items:Entry[],destination:string){
  if(kind==='move'){const error=moveError(items,destination,all());if(error){setDialogError(error);setMessage(error);return}}
  await runItems(kind==='move'?'移动':'重命名',items,async t=>{
   try{const r=await operations.run(kind,t.path,destination);if(!r)throw Object.assign(new Error('迁移结果待确认，请刷新核对'),{uncertain:true});return 'confirmed'}
   catch(e){if(operations.hasPending())throw Object.assign(new Error((e as Error).message),{uncertain:true});throw e}
  })
 }
 async function remove(items:Entry[]){
  await runItems('永久删除',items,async t=>{
   try{
    await api.filesDelete(t.path,basename(t.path))
    if(!alive)return 'confirmed'
    invalidateFileCache(t.path);setUndoId(null);remember('files-undo-operation',null)
    if(previewPath()===t.path||previewPath()?.startsWith(t.path+'/')){
     if(requestDraftLeave())setPreviewPath(null)
    }
    setSelected(s=>new Set([...s].filter(p=>p!==t.path&&!p.startsWith(t.path+'/'))))
    window.dispatchEvent(new CustomEvent('files-deleted',{detail:{path:t.path,owner}}));props.onListChange?.();return 'confirmed'
   }catch(e){if(uncertain(e))throw Object.assign(new Error('删除结果待确认，请刷新并核对；不会自动重试。'+(e as Error).message),{uncertain:true});throw e}
  })
 }
 async function mkdir(){
  if(busy()||!value().trim())return
  const name=value().trim(),dir=currentDir(),intent={id:fileIntentId(),path:[dir,name].filter(Boolean).join('/')}
  // The persisted identity is observational recovery, never authority to retry.
  try{sessionStorage.setItem(mkdirKey,JSON.stringify(intent))}catch{setDialogError('无法保留创建意图，请先恢复浏览器存储');return}
  setMkdirPending(intent);setMkdirObserved(false);setWorking(true)
  try{await api.filesMkdir(dir,name);if(!alive)return;setMessage('');setMkdirPending(null);sessionStorage.removeItem(mkdirKey);setDialog(null)}
  catch(e){if(!alive)return;if(!uncertain(e)){setMkdirPending(null);sessionStorage.removeItem(mkdirKey);setDialogError((e as Error).message)}else{setDialog(null);setMessage('创建结果待确认。请刷新并核对目标目录；不会自动重试。')}}
  finally{if(alive){setWorking(false);await reload()}}
 }
 function command(action:'rename'|'delete'|'copy'|'preview',items:Entry[]){
  if(busy())return
  const normalized=normalizeTargets(items)
  if(action==='preview'&&normalized.length===1)preview(normalized[0])
  else if(action==='copy'){
   if(!navigator.clipboard?.writeText){setMessage('无法复制：浏览器不支持剪贴板，请从行的完整名称或路径手动复制');return}
   void navigator.clipboard.writeText(normalized.map(t=>t.path).join('\n')).then(()=>{if(alive)setMessage('路径已复制')}).catch(()=>{if(alive)setMessage('无法复制，请从行的完整名称或路径复制')})
  }
  else if(action!=='preview')open(action,normalized)
 }
 const menuTargets=(t:Entry)=>selected().has(t.path)?targets():[t]
 function context(e:MouseEvent,t:Entry){e.preventDefault();if(!selected().has(t.path))choose(t);const row=e.currentTarget as HTMLElement;(row.querySelector('md-icon-button') as HTMLElement)?.click()}
 function key(e:KeyboardEvent){
  if(editingEvent(e)||dialog())return
  const t=rows().find(x=>x.path===focused()),paths=rows().map(t=>t.path),i=paths.indexOf(focused())
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();setSelected(new Set(paths));return}
  if(e.key==='Escape'){e.preventDefault();setDrag([]);setDrop(null);if(drawer())setDrawer(false);else if(previewPath())closePreview();else setSelected(new Set<string>());return}
  let next=-1
  if(e.key==='ArrowDown')next=Math.min(paths.length-1,i+1)
  if(e.key==='ArrowUp')next=Math.max(0,i-1)
  if(e.key==='Home')next=0
  if(e.key==='End')next=paths.length-1
  if(next>=0&&rows()[next]){e.preventDefault();choose(rows()[next],e);focusRow(paths[next]);return}
  if(!t)return
  if(e.key==='Enter'||e.key===' '){e.preventDefault();preview(t)}
  if(e.key==='F2'){e.preventDefault();if(targets().length===1)command('rename',targets())}
  if(e.key==='Delete'){e.preventDefault();command('delete',targets())}
  if(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10'){e.preventDefault();const row=Array.from(list.querySelectorAll<HTMLElement>('[data-entry]')).find(n=>n.dataset.entry===t.path);(row?.querySelector('md-icon-button') as HTMLElement)?.click()}
  // Ctrl+Z belongs only to the explicit file migration undo button, never study undo.
 }
 function startDrag(e:DragEvent,t:Entry){
  if(busy()||!requestDraftLeave()){e.preventDefault();return}
  const items=normalizeTargets(selected().has(t.path)?targets():[t]);setSelected(new Set(items.map(t=>t.path)));setDrag(items);suppressClick=true
  e.dataTransfer!.effectAllowed='move';e.dataTransfer!.setData('application/x-ir4anki-paths',JSON.stringify(items.map(x=>x.path)));e.dataTransfer!.setData('text/plain',items.map(x=>x.path).join('\n'))
 }
 function over(e:DragEvent,dest:string){
  if(!drag().length){if(e.dataTransfer?.types.includes('Files')){e.preventDefault();e.stopPropagation();e.dataTransfer.dropEffect='none';setExternalDrop(true)}return}
  e.preventDefault();e.stopPropagation();setDrop(dest);e.dataTransfer!.dropEffect=moveError(drag(),dest,all())||busy()?'none':'move'
 }
 function dropped(e:DragEvent,dest:string){
  if(e.dataTransfer?.types.includes('Files')){e.preventDefault();setExternalDrop(false);setMessage('这是外部文件，请使用“导入”选择文件并确认当前目录');return}
  if(!drag().length)return
  e.preventDefault();e.stopPropagation();const items=drag();setDrag([]);setDrop(null)
  const error=moveError(items,dest,all());if(error){setMessage(error);return}void relocation('move',items,dest)
 }
 function dropClass(path:string){return {'fb-drop-valid':drop()===path&&!!drag().length&&!moveError(drag(),path,all()),'fb-drop-invalid':drop()===path&&!!drag().length&&!!moveError(drag(),path,all())}}
 function pickImport(folder=false){if(busy()||!requestDraftLeave())return;returnFocus=document.activeElement as HTMLElement;uploadDirectory=currentDir();(folder?folderInput:input).click()}
 async function importFiles(input:HTMLInputElement){
  const entries=Array.from(input.files??[]).map(file=>({file,rel:(file as File&{webkitRelativePath?:string}).webkitRelativePath||file.name}));input.value=''
  if(!entries.length||busy()||!requestDraftLeave())return
  const {supported,skipped}=partitionImports(entries,true)
  setSkippedImports(skipped)
  setMessage(supported.length?'':'没有可导入的文件，请展开跳过清单查看原因')
  if(supported.length)await importEntries(supported,uploadDirectory,'error')
 }
 async function importEntries(entries:ImportEntry[],dir:string,policy:'error'|'overwrite'|'rename'|'skip'='error'){
  if(!entries.length||busy()||!requestDraftLeave())return
  setWorking(true)
  const generic=entries.filter(e=>!(/\.(pdf|ppt|pptx)$/i.test(e.file.name)))
  const target=(e:ImportEntry)=>({path:[dir,e.rel].filter(Boolean).join('/'),isDir:false})
  const targets=entries.map(target),units=[...(generic.length?[generic.map(target)]:[]),...entries.filter(e=>/\.(pdf|ppt|pptx)$/i.test(e.file.name)).map(e=>[target(e)])]
  let genericHalted=false
  const unknown=(e:unknown)=>!(e instanceof ApiError&&e.status>=400&&e.status<500||e instanceof UploadConflictError||e instanceof Error&&/已存在|冲突|格式|不支持|conflict/i.test(e.message))
  try{
   await batch.runUnits('导入',targets,units,async items=>{
    if(genericHalted)return items.map(t=>({path:t.path,status:'not-started' as const}))
    const coupled=generic.some(e=>target(e).path===items[0].path)
    try{
     if(coupled){const result=await api.filesUpload(generic,dir,policy);if(Object.keys(result.images_renamed??{}).length&&alive)setMessage('图片重名已自动改名并更新引用：'+Object.entries(result.images_renamed).map(([a,b])=>a+' → '+b).join('；'))}
     else {const entry=entries.find(e=>target(e).path===items[0].path)!;const body=new FormData();body.append('file',entry.file);body.append('path',items[0].path);await lectureRequest('/import','POST',body)}
     if(alive){for(const t of items)invalidateFileCache(t.path);window.dispatchEvent(new Event('lecture-documents-changed'));setVersion(v=>v+1)}
     return items.map(t=>({path:t.path,status:'confirmed' as const}))
    }catch(e){
     if(e instanceof UploadPartialError){genericHalted=e.remaining.length>0;return items.map(t=>{
      const entry=generic.find(x=>target(x).path===t.path)!
      const status=e.saved.some(s=>s.action==='skipped'?s.path===entry.rel:s.path===t.path||(s as {original?:string}).original===entry.rel)?'confirmed':e.failed===entry.rel||e.failed===t.path?'failed':e.remaining.includes(entry.rel)||e.remaining.includes(t.path)?'not-started':'unconfirmed'
      return {path:t.path,status,message:status==='confirmed'?undefined:e.message}
     })}
     if(e instanceof UploadConflictError&&policy==='error'&&alive)setImportConflict({entries:generic,dir,names:e.conflicts})
     if(unknown(e))throw Object.assign(new Error('导入结果待确认，请刷新核对；不会自动重试。'+(e as Error).message),{uncertain:true})
     throw e
    }
   })
  }catch(e){if(alive)setMessage('导入状态无法保留，请核对已保存文件：'+(e as Error).message)}finally{if(alive){setWorking(false);await reload()}}
 }
 async function undo(){
  const id=undoId();if(!id||busy()||!requestDraftLeave())return;setWorking(true)
  try{await operations.run('undo','',id);if(alive)setMessage('已撤销上次单项文件迁移')}
  catch(e){if(alive)setMessage((e as Error).message)}finally{if(alive){setWorking(false);await reload()}}
 }
 const dialogRef=(el:HTMLDialogElement)=>{queueMicrotask(()=>{if(el.isConnected&&!el.open)el.showModal()})}
 return <section class="fb-workspace" aria-label="文件工作区" onKeyDown={key} onPointerDown={()=>{suppressClick=false}} onDragOver={e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();setExternalDrop(true)}}} onDrop={e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();setExternalDrop(false);setMessage('外部文件请使用导入按钮')}}}>
  <input ref={input} hidden type="file" multiple accept=".md,.pdf,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.webp,.svg,.bmp,.avif" onChange={e=>void importFiles(e.currentTarget)}/>
  <input ref={el=>{folderInput=el;el.setAttribute('webkitdirectory','')}} hidden type="file" multiple onChange={e=>void importFiles(e.currentTarget)}/>
  <header class="fb-header"><div><h1>文件</h1><p>管理阅读文件</p></div><div class="fb-import-actions"><md-filled-button disabled={busy()} onClick={()=>pickImport()}>导入文件</md-filled-button><md-outlined-button disabled={busy()} onClick={()=>pickImport(true)}>导入文件夹</md-outlined-button><md-outlined-button disabled={busy()} onClick={()=>open('mkdir',[])}>新建文件夹</md-outlined-button><Show when={undoId()}><FilesMenu label="更多文件操作" disabled={busy()} actions={[{label:'撤销上次单项迁移',run:()=>void undo()}]}/></Show></div></header>
  <Show when={skippedImports().length}><details class="fb-report fb-import-skipped"><summary>已跳过 {skippedImports().length} 个文件（未上传）</summary><div class="fb-report-items"><For each={skippedImports()}>{item=><div><strong>{item.rel}</strong><span>{item.reason}</span></div>}</For></div></details></Show>
  <div class="fb-layout">
   <Show when={drawer()}><button class="fb-drawer-scrim" aria-label="关闭目录导航" onClick={()=>setDrawer(false)}/></Show>
   <aside class="fb-sidebar" classList={{'fb-sidebar--open':drawer()}} aria-label="文件夹导航" onKeyDown={e=>{
    if(e.key==='Escape'){setDrawer(false);document.querySelector<HTMLElement>('.fb-directory-toggle')?.focus()}
    if(drawer()&&e.key==='Tab'){const controls=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button'));const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
   }}>
    <div class="fb-sidebar-heading"><strong>文件夹</strong><button class="fb-drawer-close" onClick={()=>setDrawer(false)}>关闭</button></div>
    <div class="fb-directories"><button class="fb-directory" classList={{'fb-directory--current':!currentDir(),...dropClass('')}} aria-current={!currentDir()?'page':undefined} onClick={()=>navigate('')} onDragOver={e=>over(e,'')} onDrop={e=>dropped(e,'')}><IconFolderOpen size={20}/><span>全部文件</span></button>
    <For each={dirs().slice().sort((a,b)=>a.localeCompare(b,'zh-Hans-CN'))}>{path=><button class="fb-directory" classList={{'fb-directory--current':currentDir()===path,...dropClass(path)}} style={{'padding-left':`${12+Math.min(path.split('/').length-1,5)*12}px`}} aria-current={currentDir()===path?'page':undefined} title={path} onClick={()=>navigate(path)} onDragOver={e=>over(e,path)} onDrop={e=>dropped(e,path)}><IconFolder size={20}/><span>{basename(path)}</span></button>}</For></div>
    <p class="fb-sidebar-help">点击目录进入；拖入已选项目可移动。</p>
   </aside>
   <main class="fb-main">
    <nav class="fb-navigation" aria-label="当前文件夹路径"><button class="fb-directory-toggle" onClick={()=>{setDrawer(true);requestAnimationFrame(()=>document.querySelector<HTMLElement>('.fb-sidebar button')?.focus())}}>目录</button><md-icon-button aria-label="后退" disabled={!history().length} onClick={goBack}><md-icon><IconArrowBack/></md-icon></md-icon-button><md-icon-button aria-label="上一级" disabled={!currentDir()} onClick={()=>navigate(parentPath(currentDir()))}><md-icon><span style={{display:'flex',transform:'rotate(90deg)'}}><IconArrowBack/></span></md-icon></md-icon-button><div class="fb-breadcrumb"><button onClick={()=>navigate('')}>全部文件</button><For each={currentDir().split('/').filter(Boolean)}>{(part,index)=><><IconChevronRight size={16}/><button title={currentDir().split('/').slice(0,index()+1).join('/')} onClick={()=>navigate(currentDir().split('/').slice(0,index()+1).join('/'))}>{part}</button></>}</For></div><md-icon-button aria-label="刷新文件列表" onClick={()=>void reload()} disabled={loading()||working()}><md-icon><IconRestartAlt/></md-icon></md-icon-button></nav>
    <div class="fb-search"><input type="search" aria-label="在全部文件中搜索" placeholder="在全部文件中搜索" value={query()} onInput={e=>setQuery(e.currentTarget.value)}/></div>
    <Show when={message()||drag().length||externalDrop()}><div role="status" aria-live="polite"><Show when={drag().length}>{drop()===null?'拖到文件夹或根目录；Escape 取消':moveError(drag(),drop()!,all())||`移动到 ${drop()||'根目录'}`}</Show><Show when={externalDrop()}>外部文件请使用导入按钮</Show><span>{message()}</span></div></Show>
  <Show when={report()?.items.some(i=>i.status!=='confirmed')}><details class="fb-operation-feedback fb-recovery" open role="alert"><summary>{report()!.action}结果 · {report()!.items.filter(i=>i.status==='confirmed').length}/{report()!.items.length} 已确认{unresolved()?' · 有待确认项目，已停止后续操作':''}</summary><div class="fb-report-items"><For each={report()!.items}>{item=><div><strong>{item.path}</strong><span>{({'confirmed':'已确认','failed':'失败','unconfirmed':'结果待确认','not-started':'未开始','running':'处理中'})[item.status]}</span><span>{item.message}</span><Show when={item.status==='unconfirmed'}><small>当前列表：{all().some(t=>t.path===item.path)?'原路径仍可见':'原路径未列出'}。请核对原路径与目标；这不代表原请求已确认。</small></Show></div>}</For></div><Show when={!working()}><md-text-button disabled={(operationVersion(),operations.isBusy())} onClick={()=>{setReport(null);sessionStorage.removeItem(batchKey)}}>{unresolved()?'已核对，结束本批；不重试剩余项目':'关闭结果'}</md-text-button></Show></details></Show>
    <div class="fb-command-slot" aria-label="文件操作">
     <Show when={selected().size} fallback={<div class="fb-folder-heading"><strong>{basename(currentDir())||'全部文件'}</strong><span>{rows().length} 项 · 导入到 {currentDir()||'根目录'}</span></div>}>
      <div class="fb-selection-heading"><strong>已选择 {selected().size} 项</strong><button onClick={()=>setSelected(new Set<string>())}>取消选择</button></div><div class="fb-selection-actions"><md-text-button disabled={busy()||targets().length!==1||targets()[0]?.isDir} onClick={()=>command('preview',targets())}>预览与阅读</md-text-button><md-text-button disabled={busy()||targets().length!==1} onClick={()=>command('rename',targets())}>重命名</md-text-button><md-text-button disabled={busy()} onClick={()=>command('delete',targets())}>删除</md-text-button></div>
     </Show>
    </div>
    <div class="fb-list" ref={list} tabIndex={0} role="listbox" aria-label="当前文件夹内容" aria-multiselectable="true" aria-busy={loading()||working()} onDragOver={e=>over(e,currentDir())} onDrop={e=>dropped(e,currentDir())} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node)){setDrop(null);setExternalDrop(false)}}}>
     <Show when={loading()}><p role="status" class="fb-empty">正在读取文件列表…</p></Show>
     <Show when={loadError()}><div class="fb-empty" role="alert">{loadError()}<md-text-button onClick={()=>void reload()}>重试</md-text-button></div></Show>
     <Show when={!loading()&&!loadError()&&!rows().length}><div class="fb-empty"><IconFolderOpen size={40}/><h2>{query()?'没有匹配项目':'这个文件夹还是空的'}</h2><p>{query()?'试试其他文件名或文件夹名。':'可导入 Markdown、PDF / PPT / PPTX 和图片，也可先创建文件夹。'}</p><Show when={!query()}><md-filled-tonal-button disabled={busy()} onClick={()=>pickImport()}>导入到当前文件夹</md-filled-tonal-button><md-text-button disabled={busy()} onClick={()=>open('mkdir',[])}>新建文件夹</md-text-button></Show></div></Show>
     <For each={rows()}>{t=>{
      const s=summaryMap().get(t.path)
      const rank=()=>readingList().findIndex(item=>item.path===t.path)+1
      const pct=()=>s?calcPercent(readUnits(s),totalUnits(s)):0
      const isDone=()=>s&&s.total_chunks>0&&s.todo===0&&s.active===0
      const isArchived=()=>!s&&!!corpusMap().get(t.path)?.archived
      return <div class="fb-row" data-entry={t.path} role="option" aria-selected={selected().has(t.path)} tabindex={focused()===t.path?0:-1} title={t.path} classList={{'fb-row--selected':selected().has(t.path),...t.isDir?dropClass(t.path):{}}} draggable={!busy()} onFocus={()=>setFocused(t.path)} onClick={e=>choose(t,e)} onDblClick={e=>{if(!(e.target as Element).closest('button,md-icon-button,.files-menu,input'))preview(t)}} onContextMenu={e=>context(e,t)} onDragStart={e=>startDrag(e,t)} onDragEnd={()=>{setDrag([]);setDrop(null)}} onDragOver={t.isDir?e=>over(e,t.path):undefined} onDrop={t.isDir?e=>dropped(e,t.path):undefined}>
       <input class="fb-checkbox" type="checkbox" aria-label={`选择 ${basename(t.path)}`} checked={selected().has(t.path)} onClick={e=>{e.stopPropagation();choose(t,{ctrlKey:true,metaKey:false,shiftKey:e.shiftKey} as MouseEvent)}}/>
       <span class="fb-type-icon">{t.isDir?<IconFolder size={22}/>:<IconDescription size={22}/>}</span>
       <div class="fb-name">
        <span class="fb-basename">
         {t.isDir?basename(t.path):<>{basename(t.path).slice(0,Math.max(0,basename(t.path).lastIndexOf('.')))||basename(t.path)}<span class="fb-extension">{basename(t.path).includes('.')?basename(t.path).slice(basename(t.path).lastIndexOf('.')):''}</span></>}
         <Show when={!t.isDir}>
          <Show when={s}><span class="fb-reading-pill fb-reading-pill--in-list" title={`阅读清单 #${rank()} · 已读 ${pct()}%`}>#{rank()} · {pct()}%</span></Show>
          <Show when={!s && isDone()}><span class="fb-reading-pill fb-reading-pill--done" title="已全部读完">已读完 ✓</span></Show>
          <Show when={!s && isArchived()}><span class="fb-reading-pill fb-reading-pill--archived" title="有历史阅读进度">已存档</span></Show>
         </Show>
        </span>
        <span class="fb-meta">
         {t.isDir?'文件夹':/\.pdf$/i.test(t.path)?'PDF':/\.ppt$/i.test(t.path)?'PPT':/\.pptx$/i.test(t.path)?'PPTX':'Markdown'}
         {s ? ` · 阅读中 (${fmtNum(readUnits(s))}/${fmtNum(totalUnits(s))}${unitName(s)})` : membership().has(t.path)?' · 阅读清单':''}
         <Show when={query().trim()}> · {parentPath(t.path)||'根目录'}</Show>
        </span>
       </div>
       <Show when={!t.isDir && s}>
        <div class="fb-row-progress" aria-hidden="true">
         <div class="fb-row-progress__read" style={{ width: `${readRatio(s) * 100}%` }} />
         <div class="fb-row-progress__active" style={{ width: `${activeRatio(s) * 100}%` }} />
        </div>
       </Show>
       <FilesMenu label={`更多操作：${basename(t.path)}`} disabled={busy()||readingActionBusy()} actions={[
        ...(!t.isDir?[{label:'预览与阅读',run:()=>command('preview',[t])}]:[]),
        ...(!t.isDir&&s?[{label:'设为置顶',run:()=>moveTop(s)},{label:'移出阅读清单',run:()=>setRemoveTarget(s)}]:[]),
        ...(!t.isDir&&!s?[{label:isArchived()?'恢复阅读进度':'加入阅读清单',run:()=>void addFile(corpusMap().get(t.path)??{path:t.path})}]:[]),
        {label:'重命名',run:()=>{const ts=menuTargets(t);if(ts.length===1)command('rename',ts);else setMessage('重命名需要只选择一个项目')}},
        {label:'复制路径',run:()=>command('copy',menuTargets(t))},
        {label:'永久删除',run:()=>command('delete',menuTargets(t))}
       ]}/>
      </div>
     }}</For>
    </div>
    <footer class="fb-footer"><span>{rows().length} 项{selected().size?` · 已选 ${selected().size}`:''}</span><span>{currentDir()||'根目录'}</span></footer>
   </main>
   <section class="fb-workbench" classList={{'fb-workbench--detail':!!previewPath()}} role="region" aria-label="阅读工作台">
    <Show when={previewPath()} fallback={
     <div class="fb-queue-pane">
      <div class="reading-queue-inner">
       <div class="reading-queue-head">
        <div class="reading-queue-head__title-row">
         <h2 class="reading-queue-title md-typescale-title-small">阅读清单</h2>
         <span class="reading-queue-count md-typescale-label-small">{readingList().length} 篇</span>
         <md-icon-button aria-label="刷新清单" onClick={reloadReading} disabled={busy()||readingActionBusy()}>
          <md-icon><IconRestartAlt size={18} /></md-icon>
         </md-icon-button>
        </div>
       </div>
       <div class="reading-queue-body" aria-busy={readingLoading()||readingActionBusy()}>
        <Show when={readingError()}><p role="alert" class="lecture-error">{readingError()}</p></Show>
        <Show when={readingMessage()}><p role="status" class="reading-status-banner md-typescale-label-medium">{readingMessage()}</p></Show>
        <Show when={readingLoading()}><div class="reading-list-loading"><md-circular-progress indeterminate style="--md-circular-progress-size:32px" /></div></Show>
        <Show when={!readingLoading()&&readingList().length===0}>
         <div class="reading-list-empty md-typescale-body-medium">
          <md-icon class="reading-list-empty-icon"><IconMenuBook /></md-icon>
          <div>清单还是空的——从左侧文件列表点击「加入清单」把要读的笔记加进来。</div>
         </div>
        </Show>
        <div class="reading-list">
         <For each={readingList()}>{(s,i)=>{
          const pct=()=>calcPercent(readUnits(s),totalUnits(s))
          const unit=()=>unitName(s)
          const doneSegs=()=>s.done+s.skipped+(s.background??0)
          const dirPath=()=>{const stripped=s.path.replace(/^\d{4}\//,'');const slash=stripped.lastIndexOf('/');return slash>0?stripped.slice(0,slash):stripped}
          return <div class="reading-list-item fb-queue-item-clickable" classList={{'reading-list-item--missing':!!s.missing,'reading-list-item--top':i()===0}}>
           <div class="reading-list-item__rank" title={`优先级 #${i()+1}`}>#{i()+1}</div>
           <div class="reading-list-item__main">
            <div class="reading-list-item__header">
             <div class="reading-list-item__title md-typescale-title-small" title="点击打开此文件并查看阅读详情" onClick={()=>{if(requestDraftLeave())setPreviewPath(s.path)}}>
              {s.title}<span class="reading-list-item__path md-typescale-label-small">{dirPath()}</span>
              <Show when={s.missing}><span class="reading-badge reading-badge--warn">文件已不在语料中</span></Show>
              <Show when={!s.missing&&(s as any).orphans>0}><span class="reading-badge reading-badge--warn">文件已变更 · {(s as any).orphans} 段状态待确认</span></Show>
             </div>
             <div class="reading-list-item__actions">
              <md-icon-button aria-label="打开预览" title="打开此文件预览与进度" onClick={()=>{if(requestDraftLeave())setPreviewPath(s.path)}}><md-icon><IconDescription /></md-icon></md-icon-button>
              <md-icon-button aria-label="置顶" title="置顶" disabled={busy()||readingActionBusy()||i()===0} onClick={()=>moveTop(s)}><md-icon><IconVerticalAlignTop /></md-icon></md-icon-button>
              <md-icon-button aria-label="上移" title="上移一位" disabled={busy()||readingActionBusy()||i()===0} onClick={()=>moveUp(s)}><md-icon><IconArrowUpward /></md-icon></md-icon-button>
              <md-icon-button aria-label="下移" title="下移一位" disabled={busy()||readingActionBusy()||i()===readingList().length-1} onClick={()=>moveDown(i())}><md-icon><IconArrowDownward /></md-icon></md-icon-button>
              <md-icon-button aria-label="移出清单" title="移出清单（保留进度）" disabled={busy()||readingActionBusy()} onClick={()=>setRemoveTarget(s)}><md-icon><IconClose /></md-icon></md-icon-button>
             </div>
            </div>
            <Show when={!s.missing}>
             <div class="reading-list-item__progress-row">
              <div class="reading-char-bar reading-list-item__bar" role="progressbar" aria-valuemin={0} aria-valuemax={totalUnits(s)} aria-valuenow={readUnits(s)}>
               <div class="reading-char-bar__read" style={{width:`${readRatio(s)*100}%`}} />
               <div class="reading-char-bar__active" style={{width:`${activeRatio(s)*100}%`}} />
              </div>
              <span class="reading-list-item__pct md-typescale-label-medium">{pct()}%</span>
             </div>
             <div class="reading-list-item__meta md-typescale-label-small">
              <div class="reading-list-item__stats">
               <span class="reading-stat-Highlight">已读 {fmtNum(readUnits(s))} / {fmtNum(totalUnits(s))} {unit()}</span>
               <Show when={activeUnits(s)>0}><span class="reading-stat-active">· 正在制卡 {fmtNum(activeUnits(s))} {unit()} ({s.active} 段)</span></Show>
               <Show when={(s as any).cards_created>0}><span>· 已制卡 {(s as any).cards_created} 张</span></Show>
               <span class="reading-stat-chunks">· 片段 {doneSegs()} / {s.total_chunks}</span>
              </div>
              <div class="reading-list-item__frontier">
               <Show when={s.frontier}><span class="reading-frontier-text" title={s.frontier!.preview||s.frontier!.title}>下一段：{frontierText(s,s.frontier!)}</span><span class={`reading-status-chip reading-status-${s.frontier!.status}`}>{FRONTIER_LABEL[s.frontier!.status]??s.frontier!.status}</span></Show>
               <Show when={!s.frontier&&s.gated_frontier}><span class="reading-frontier-text" title={s.gated_frontier!.preview||s.gated_frontier!.title}>下一段：{frontierText(s,s.gated_frontier!)}</span><span class="reading-status-chip reading-status-held">等卡片过预览池</span></Show>
               <Show when={!s.frontier&&!s.gated_frontier&&!s.missing}><span class="reading-frontier-done">全部读完 ✓</span></Show>
              </div>
             </div>
            </Show>
           </div>
          </div>
         }}</For>
        </div>
       </div>
      </div>
     </div>
    }>
     <div class="fb-detail-pane">
      <div class="fb-detail-head">
       <div class="fb-detail-title-group">
        <span class="fb-detail-path md-typescale-label-small">{parentPath(previewPath()!)||'根目录'}</span>
        <h2 class="fb-detail-title md-typescale-title-small" title={previewPath()!}>{basename(previewPath()!)}</h2>
       </div>
       <div class="fb-detail-actions">
        <md-text-button onClick={closePreview}>
         <md-icon slot="icon"><IconArrowBack size={18} /></md-icon>
         返回阅读清单
        </md-text-button>
       </div>
      </div>
      <div class="fb-file-reading-card">
       {(()=>{
        const s=summaryMap().get(previewPath()!)
        const c=corpusMap().get(previewPath()!)
        const rank=()=>readingList().findIndex(item=>item.path===previewPath()!)+1
        return <Show when={s} fallback={
         <div class="fb-file-reading-card__not-in-list">
          <div><Show when={c?.archived} fallback={<span>该文件尚未加入阅读清单。加入后将在「学习」tab 阶段发牌推进。</span>}><span>该文件有存档进度{c?.chars?` (${fmtNum(c.chars)} 字)`:c?.page_count?` (${c.page_count} 页)`:''}。</span></Show></div>
          <div class="fb-file-reading-card__actions">
           <Show when={c?.archived} fallback={<md-filled-button disabled={busy()||readingActionBusy()} onClick={()=>addFile(c??{path:previewPath()!})}>加入阅读清单</md-filled-button>}>
            <md-filled-tonal-button disabled={busy()||readingActionBusy()} onClick={()=>addFile(c!,false)}>恢复阅读进度</md-filled-tonal-button>
            <md-text-button disabled={busy()||readingActionBusy()} onClick={()=>addFile(c!,true)}>重新播种</md-text-button>
           </Show>
          </div>
         </div>
        }>{item=>{
         const pct=()=>calcPercent(readUnits(item()),totalUnits(item()))
         const unit=()=>unitName(item())
         const doneSegs=()=>item().done+item().skipped+(item().background??0)
         return <>
          <div class="fb-file-reading-card__top">
           <div class="fb-file-reading-card__badge-row">
            <span class="fb-reading-pill fb-reading-pill--in-list">阅读清单 #{rank()}</span>
            <span class="md-typescale-label-medium">{pct()}%</span>
           </div>
           <div class="fb-file-reading-card__actions">
            <Show when={rank()>1}><md-text-button disabled={busy()||readingActionBusy()} onClick={()=>moveTop(item())}>设为置顶</md-text-button></Show>
            <md-text-button disabled={busy()||readingActionBusy()} onClick={()=>setRemoveTarget(item())}>移出清单</md-text-button>
           </div>
          </div>
          <div class="fb-file-reading-card__progress-row">
           <div class="reading-char-bar" role="progressbar" aria-valuemin={0} aria-valuemax={totalUnits(item())} aria-valuenow={readUnits(item())}>
            <div class="reading-char-bar__read" style={{width:`${readRatio(item())*100}%`}} />
            <div class="reading-char-bar__active" style={{width:`${activeRatio(item())*100}%`}} />
           </div>
          </div>
          <div class="fb-file-reading-card__stats">
           <span class="reading-stat-Highlight">已读 {fmtNum(readUnits(item()))} / {fmtNum(totalUnits(item()))} {unit()}</span>
           <Show when={activeUnits(item())>0}><span class="reading-stat-active">· 正在制卡 {fmtNum(activeUnits(item()))} {unit()} ({item().active} 段)</span></Show>
           <Show when={(item() as any).cards_created>0}><span>· 已制卡 {(item() as any).cards_created} 张</span></Show>
           <span>· 片段 {doneSegs()} / {item().total_chunks}</span>
          </div>
          <Show when={item().frontier||item().gated_frontier}>
           <div class="fb-file-reading-card__frontier">
            <span class="fb-file-reading-card__frontier-text" title={item().frontier?.preview||item().frontier?.title}>下一段：{item().frontier?frontierText(item(),item().frontier!):frontierText(item(),item().gated_frontier!)}</span>
            <span class={`reading-status-chip reading-status-${item().frontier?item().frontier!.status:'held'}`}>{item().frontier?(FRONTIER_LABEL[item().frontier!.status]??item().frontier!.status):'等卡片过预览池'}</span>
           </div>
          </Show>
         </>
        }}</Show>
       })()}
      </div>
      <div class="fb-preview-body" classList={{'fb-preview-body--lecture':/\.(pdf|ppt|pptx)$/i.test(previewPath()!)}} inert={working()}>
       <Show when={/\.(pdf|ppt|pptx)$/i.test(previewPath()!)} fallback={<FileViewer path={previewPath()} anchorLine={summaryMap().get(previewPath()!)?.frontier?.line_start??null} anchorEndLine={summaryMap().get(previewPath()!)?.frontier?.line_end??null} fetchFile={api.filesRaw} revision={files().find(f=>f.path===previewPath())?.revision} reloadToken={version()} class="note-body"/>}><FilesLecturePreview path={previewPath()!}/></Show>
      </div>
     </div>
    </Show>
   </section>
  </div>
  <Show when={removeTarget()!==null}>
   <dialog ref={dialogRef} class="fb-dialog" onCancel={e=>{e.preventDefault();setRemoveTarget(null);restoreFocus()}}>
    <h2>移出阅读清单？</h2>
    <p>「{removeTarget()!.title}」将从清单移出。已读进度会保留，重新添加即恢复。</p>
    <div class="fb-dialog-actions">
     <button type="button" disabled={readingActionBusy()} onClick={()=>setRemoveTarget(null)}>取消</button>
     <button type="button" style={{color:'var(--md-sys-color-error)'}} disabled={readingActionBusy()} onClick={confirmRemove}>移出</button>
    </div>
   </dialog>
  </Show>
  <Show when={mkdirPending()}><div class="fb-recovery" role="alert">创建结果待核对：{mkdirPending()!.path}。{mkdirObserved()?'目录现已存在；请确认这是否为预期结果。':'列表尚未确认该目录；请刷新核对。'}<md-text-button disabled={working()||loading()} onClick={()=>{setMkdirPending(null);sessionStorage.removeItem(mkdirKey);setMessage('已结束创建结果核对；不会重发原请求')}}>已核对，结束本次操作</md-text-button></div></Show>
  <Show when={importConflict()&&!working()&&!unresolved()}><dialog ref={dialogRef} class="fb-dialog" onCancel={e=>{e.preventDefault();setImportConflict(null);restoreFocus()}}><h2>导入文件名冲突</h2><p>重新导入到：{importConflict()!.dir||'根目录'}。以下 Markdown 名称已存在；默认不覆盖。</p><div class="fb-dialog-targets"><For each={importConflict()!.names}>{name=><p>{name}</p>}</For></div><p>选择“覆盖”会替换这些原文；其余选择会跳过冲突或改名保留两份。PDF / PPT / PPTX 的历史版本始终在课件预览中单独导入。</p><div class="fb-conflict-actions"><button onClick={()=>{setImportConflict(null);restoreFocus()}}>取消上传</button><For each={[{policy:'skip' as const,label:'跳过重名'},{policy:'rename' as const,label:'改名保留两者'},{policy:'overwrite' as const,label:'覆盖'}]}>{choice=><button disabled={busy()} onClick={()=>{const c=importConflict()!;setImportConflict(null);void importEntries(c.entries,c.dir,choice.policy)}}>{choice.label}</button>}</For></div></dialog></Show>
  <Show when={dialog()}><dialog ref={dialogRef} class="fb-dialog" onCancel={e=>{e.preventDefault();closeDialog()}} onClose={()=>{if(dialog()&&!working())closeDialog()}}><form onSubmit={e=>{e.preventDefault();const d=dialog()!;if(d.kind==='mkdir')void mkdir();else if(d.kind==='rename')void relocation(d.kind,d.targets,value().trim())}}>
   <h2>{({'rename':'重命名','delete':'删除','mkdir':'新建文件夹'})[dialog()!.kind]}</h2>
   <Show when={dialog()!.kind==='mkdir'}><p>创建在：{currentDir()||'根目录'}</p></Show>
   <Show when={dialog()!.kind!=='delete'&&dialog()!.targets.length}><div class="fb-dialog-targets"><For each={dialog()!.targets}>{t=><p>{t.path}{t.isDir?'（含全部子项）':''}</p>}</For></div></Show>
   <Show when={dialog()!.kind==='rename'||dialog()!.kind==='mkdir'}><label>名称<input autofocus aria-label="名称" value={value()} onInput={e=>setValue(e.currentTarget.value)} required maxLength={255}/></label></Show>
   <Show when={dialog()!.kind==='delete'}><p>{dialog()!.targets.length===1?`确认删除${dialog()!.targets[0].isDir?'文件夹':''}「${basename(dialog()!.targets[0].path)}」？此操作无法撤销。`:`确认删除这 ${dialog()!.targets.length} 项？此操作无法撤销。`}</p></Show>
   <Show when={dialogError()}><p class="fb-error" role="alert">{dialogError()}</p></Show>
   <div class="fb-dialog-actions"><button type="button" autofocus={dialog()!.kind==='delete'} disabled={working()} onClick={closeDialog}>取消</button><button type={dialog()!.kind==='delete'?'button':'submit'} disabled={working()||['rename','mkdir'].includes(dialog()!.kind)&&!value().trim()} onClick={()=>{const d=dialog();if(d?.kind==='delete')void remove(d.targets)}}>{dialog()!.kind==='delete'?'删除':'确认'}</button></div>
  </form></dialog></Show>
 </section>
}
