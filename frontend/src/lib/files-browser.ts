import {validMove} from './files-state'
export const fileIntentId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')
export type Entry={path:string;isDir:boolean;revision?:string}
export const parentPath=(p:string)=>p.includes('/')?p.slice(0,p.lastIndexOf('/')):''
export const basename=(p:string)=>p.split('/').pop()??p
export function normalizeTargets(items:Entry[]):Entry[]{
 const unique=[...new Map(items.map(t=>[t.path,t])).values()].filter(t=>t.path)
 return unique.filter(t=>!unique.some(p=>p.isDir&&t.path.startsWith(p.path+'/')))
}
export function visibleEntries(entries:Entry[],dir:string,query:string,scope:string):Entry[]{
 const q=query.trim().toLocaleLowerCase()
 return entries.filter(t=>(!q||scope==='current'?parentPath(t.path)===dir:scope==='all'||!dir||t.path.startsWith(dir+'/'))&&(!q||basename(t.path).toLocaleLowerCase().includes(q)))
 .sort((a,b)=>Number(b.isDir)-Number(a.isDir)||basename(a.path).localeCompare(basename(b.path),'zh-Hans-CN'))
}
export function moveError(items:Entry[],dest:string,all:Entry[]):string{
 const names=new Set<string>()
 for(const t of normalizeTargets(items)){
  if(!validMove(t.path,t.isDir,dest))return '不能移动到原位置、自身或子目录'
  const name=basename(t.path),path=[dest,name].filter(Boolean).join('/')
  if(names.has(name)||all.some(x=>x.path===path))return '目标已有同名项目：'+name
  names.add(name)
 }
 return ''
}
export type ItemStatus='not-started'|'running'|'confirmed'|'failed'|'unconfirmed'
export type BatchReport={id:string;action:string;items:(Entry&{status:ItemStatus;message?:string})[]}
// Authority belongs to this mounted owner, never to a stored path or a late response.
export class SerialBatch{
 private epoch=0
 constructor(private publish:(r:BatchReport)=>void){}
 revoke(){++this.epoch}
 async run(action:string,targets:Entry[],execute:(t:Entry)=>Promise<'confirmed'|'unconfirmed'|'not-started'>){
  const epoch=++this.epoch,r:BatchReport={id:fileIntentId(),action,items:targets.map(t=>({...t,status:'not-started'}))}
  const emit=()=>{if(epoch===this.epoch)this.publish(structuredClone(r))}
  emit()
  for(const item of r.items){
   if(epoch!==this.epoch)break
   item.status='running';emit()
   try{item.status=await execute(item)}catch(e){item.status=(e as {uncertain?:boolean}).uncertain?'unconfirmed':'failed';item.message=(e as Error).message}
   if(epoch!==this.epoch)break
   emit();if(item.status==='unconfirmed')break
  }
  return r
 }
 // A coupled request dispatches every member together; publish all its outcomes
 // before an independent request can begin, without reordering the report.
 async runUnits(action:string,targets:Entry[],units:Entry[][],execute:(items:Entry[])=>Promise<{path:string;status:Exclude<ItemStatus,'running'>;message?:string}[]>){
  const epoch=++this.epoch,r:BatchReport={id:fileIntentId(),action,items:targets.map(t=>({...t,status:'not-started'}))}
  const emit=()=>{if(epoch===this.epoch)this.publish(structuredClone(r))};emit()
  for(const unit of units){
   if(epoch!==this.epoch)break
   const paths=new Set(unit.map(t=>t.path));for(const item of r.items)if(paths.has(item.path))item.status='running';emit()
   let results:Awaited<ReturnType<typeof execute>>
   try{results=await execute(unit)}catch(e){results=unit.map(t=>({path:t.path,status:(e as {uncertain?:boolean}).uncertain?'unconfirmed':'failed',message:(e as Error).message}))}
   if(epoch!==this.epoch)break
   for(const item of r.items)if(paths.has(item.path)){const result=results.find(x=>x.path===item.path);item.status=result?.status??'unconfirmed';item.message=result?.message}
   emit();if(r.items.some(i=>i.status==='unconfirmed'))break
  }
  return r
 }

}
// Shortcut owner must inspect the composed path, including Material shadow inputs.
export function editingEvent(e:KeyboardEvent):boolean{
 return e.isComposing||e.composedPath().some(t=>t instanceof Element&&(t.matches('input,textarea,select,button,a,summary,[contenteditable]:not([contenteditable="false"]),md-dialog,dialog,md-menu,md-menu-item,md-icon-button,md-text-button,md-filled-button,md-filled-tonal-button,md-outlined-text-field,[role="textbox"],[role="dialog"]')||!!t.closest('.page-annotation,.cm-editor,.pdf-source,.files-menu')))
}
