import type {OcclusionDraft,Publication} from './lecture-api'
export const authoringContent=(d:OcclusionDraft)=>JSON.stringify([d.crop,d.masks,d.mode,d.question,d.answer])
export interface WriterState {sequence:number;savedSequence:number;version:number;dirty:boolean;locked:boolean;disposed:boolean;saving:boolean}
type Reason='save'|'edit'|'ack'|'adopt'|'lock'|'dispose'
/** One version owner and one draining promise. Disposal never starts a write. */
export class AuthoringWriter {
 private current:OcclusionDraft
 private saved:string
 private sequence=0
 private savedSequence=0
 private inflight:Promise<OcclusionDraft>|undefined
 private locked=false
 private disposed=false
 constructor(initial:OcclusionDraft,private write:(value:OcclusionDraft,version:number)=>Promise<OcclusionDraft>,private notify:(value:OcclusionDraft,state:WriterState,reason:Reason)=>void=()=>{}){
  this.current=structuredClone(initial);this.saved=authoringContent(initial)
 }
 value(){return structuredClone(this.current)}
 state():WriterState{return {sequence:this.sequence,savedSequence:this.savedSequence,version:this.current.version,dirty:authoringContent(this.current)!==this.saved,locked:this.locked,disposed:this.disposed,saving:!!this.inflight}}
 private emit(reason:Reason){this.notify(this.value(),this.state(),reason)}
 edit(value:OcclusionDraft){
  if(this.disposed||this.locked)throw new Error('发布恢复期间不能修改冻结草稿')
  if(authoringContent(value)===authoringContent(this.current))return false
  const next=structuredClone(value);this.current={...this.current,crop:next.crop,masks:next.masks,mode:next.mode,question:next.question,answer:next.answer};++this.sequence
  if(!this.state().dirty)this.savedSequence=this.sequence
  this.emit('edit');return true
 }
 setLocked(value:boolean){this.locked=value;this.emit('lock')}
 flush():Promise<OcclusionDraft>{
  if(this.inflight)return this.inflight
  if(this.disposed)return Promise.reject(new Error('编辑器已关闭，草稿仍保留'))
  if(!this.state().dirty)return Promise.resolve(this.value())
  if(this.locked)return Promise.reject(new Error('先恢复未完成的发布，再保存草稿'))
  this.inflight=Promise.resolve().then(async()=>{
   while(!this.disposed&&this.state().dirty){
    if(this.locked)throw new Error('先恢复未完成的发布，再保存草稿')
    const snapshot=this.value(),seq=this.sequence
    const next=await this.write(snapshot,snapshot.version)
    this.saved=authoringContent(snapshot);this.savedSequence=seq
    // Preserve edits made during the request, adopting only server metadata.
    this.current={...this.current,version:next.version,published_version:next.published_version,note_links:next.note_links??this.current.note_links}
    if(!this.state().dirty)this.savedSequence=this.sequence
    this.emit('ack')
   }
   return this.value()
  }).finally(()=>{this.inflight=undefined;this.emit('ack')})
  this.emit('save');return this.inflight
 }
 adopt(server:OcclusionDraft){
  if(this.inflight||this.state().dirty||this.disposed)throw new Error('仍有未确认草稿，不能替换服务器基线')
  this.current=structuredClone(server);this.saved=authoringContent(server);this.savedSequence=this.sequence;this.emit('adopt')
 }
 rebase(server:OcclusionDraft,chosen:OcclusionDraft){
  if(this.inflight||this.disposed||this.locked)throw new Error('请先完成当前保存或发布')
  this.current=structuredClone(server);this.saved=authoringContent(server);this.savedSequence=this.sequence;this.emit('adopt');this.edit({...server,crop:chosen.crop,masks:chosen.masks,mode:chosen.mode,question:chosen.question,answer:chosen.answer})
 }
 dispose(){this.disposed=true;this.emit('dispose')}
}

export interface PublicationStatus {state:'unknown'|'rejected'|'operation';draft:OcclusionDraft;operation?:Publication}
export function publicationDecision(status:PublicationStatus):{kind:'resume';operation:Publication}|{kind:'merge'|'frozen'} {
 const operation=status.operation??status.draft.pending_operation
 if(operation)return {kind:'resume',operation}
 return {kind:status.state==='rejected'?'merge':'frozen'}
}
export function clearRejectedPublication(storage:{getItem:(key:string)=>string|null;removeItem:(key:string)=>void},key:string,raw:string):boolean {
 if(storage.getItem(key)!==raw)return false
 storage.removeItem(key);return true
}
