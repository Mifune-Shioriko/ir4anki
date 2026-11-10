import {createMemo,createSignal,type Accessor} from 'solid-js'

type DocumentSource={path:string;kind?:string;document_id?:string;revision_id?:string}
/** A mounted document branch never receives null during parent invalidation.
 * Same-document updates stay reactive; replacement/clearing retires the old
 * branch with its last valid value, including throughout child cleanup. */
export function createDocumentBranch<T extends DocumentSource>(source:()=>T|null|undefined){
 type Branch={key:string;current:Accessor<T>;update:(value:T)=>void}
 return createMemo<Branch|undefined>(previous=>{
  const value=source()
  if(!value)return undefined
  const key=JSON.stringify(value.kind==='pdf'?['pdf',value.document_id,value.revision_id]:['md',value.path])
  if(previous?.key===key){previous.update(value);return previous}
  const [current,setCurrent]=createSignal(value)
  return {key,current,update:next=>{setCurrent(()=>next)}}
 })
}
