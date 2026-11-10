export interface DraftStorage {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void;removeItem:(key:string)=>void}
export function ownsDraft(storage:DraftStorage,key:string,writer:string):boolean {
 const current=JSON.parse(storage.getItem(key)??'null');return !!current&&current.writer_id===writer
}
export function writeDraft(storage:DraftStorage,key:string,value:object,writer:string,onlyIfOwner=false):boolean {
 if(onlyIfOwner&&!ownsDraft(storage,key,writer))return false
 storage.setItem(key,JSON.stringify({...value,writer_id:writer}));return true
}
export function clearDraft(storage:DraftStorage,key:string,requestId:string,body:string):boolean {
 const current=JSON.parse(storage.getItem(key)??'null')
 if(!current||current.requestId!==requestId||current.body!==body)return false
 storage.removeItem(key);return true
}

export function clearOwnedDraft(storage:DraftStorage,key:string,writer:string,sequence:number):boolean {
 const current=JSON.parse(storage.getItem(key)??'null')
 if(!current||current.writer_id!==writer||current.edit_sequence!==sequence)return false
 storage.removeItem(key);return true
}

export function persistWriterDraft(storage:DraftStorage,key:string,value:object,writer:string,state:{sequence:number;dirty:boolean;saving:boolean},claim=false):boolean {
 if(state.dirty||state.saving||claim){
  if(!writeDraft(storage,key,{...value,edit_sequence:state.sequence},writer,!claim))return false
 }
 if(!state.dirty&&!state.saving)return clearOwnedDraft(storage,key,writer,state.sequence)
 return true
}
