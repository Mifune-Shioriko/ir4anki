import {ApiError} from '../api'
export interface PageLocator {kind:'pdf';document_id:string;revision_id:string;segment_id:string;page:number;annotation_id?:string}
export interface PageSource {ownership_error?:string|null;page?:number;annotation_id?:string;page_count?:number;kind:'pdf';document_id:string;revision_id:string;segment_id:string;page_start:number;page_end:number;version:number;path:string;chunk_key:string;status:string}
export interface Raster {raster_hash:string;render_profile:string;path:string;width:number;height:number;url?:string}
export interface Mask {mask_id:string;rect:number[]}
export interface OcclusionDraft {effective_source?:PageLocator|null;ownership_error?:string|null;pending_operation?:Publication;note_links?:{mask_id:string;note_id:number}[];authoring_id:string;source:PageLocator;raster:Raster;crop:number[];masks:Mask[];mode:'all'|'current';question:string;answer:string;version:number;published_version:number}
export interface Annotation {annotation_id:string;version:string;body:string;markdown_path:string}
export interface Publication {operation_id:string;status:'pending'|'partial'|'complete';items:{mask_id:string;action:string;status:string;error?:string;result?:{note_id:number}}[]}
export interface LectureRevision {document_id:string;revision_id:string;status:string;pages?:{width:number;height:number;rotation:number}[];page_count?:number;job_id?:string;error?:string;conversion?:{font_warning:string}}
export interface LectureDocument {document_id:string;path:string;title:string;current_revision:string;listed:boolean;reading_revision?:string;revision:LectureRevision;revisions?:LectureRevision[]}
export async function lectureRequest<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
 const response=await fetch(lectureResourceUrl(path),{method,signal,headers:body instanceof FormData?undefined:body===undefined?undefined:{'Content-Type':'application/json'},body:body instanceof FormData?body:body===undefined?undefined:JSON.stringify(body)})
 let value:unknown
 try{value=await response.json()}catch{if(response.ok)throw new Error('课件服务响应异常，请核对前后端版本')}
 if(!response.ok){const detail=value&&typeof value==='object'?(value as {detail?:unknown}).detail:undefined;throw new ApiError(typeof detail==='string'&&detail.trim()?detail:`HTTP ${response.status}`,response.status)}
 return value as T
}
export const pageRequestPath=(s:{document_id:string;revision_id:string},page:number)=>`/${s.document_id}/revisions/${s.revision_id}/pages/${page}`
export const locator=(s:PageSource,page:number):PageLocator=>({kind:'pdf',document_id:s.document_id,revision_id:s.revision_id,segment_id:s.segment_id,page})

/** Request paths are API-relative; only these helpers produce DOM/fetch URLs. */
export function lectureResourceUrl(path:string){
 if(path!==''&&(!path.startsWith('/')||path.startsWith('//')||path.startsWith('/api/')))throw new Error('课件路径必须为 API 相对路径，不能重复前缀')
 return '/api/lectures'+path
}
export const pageResourceUrl=(s:{document_id:string;revision_id:string},page:number,resource:'image'|'thumbnail',hash?:string)=>lectureResourceUrl(pageRequestPath(s,page)+'/'+resource+(hash===undefined?'':'?hash='+hash))
export const revisionPdfUrl=(s:{document_id:string;revision_id:string})=>lectureResourceUrl(`/${s.document_id}/revisions/${s.revision_id}/pdf`)
export async function verifiedPng(response:Response,hash?:string):Promise<Blob>{
 if(!response.ok||response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='image/png')throw new Error('基准图响应不是 PNG，请检查课件服务后重试')
 const bytes=await response.arrayBuffer(),data=new Uint8Array(bytes),signature=[137,80,78,71,13,10,26,10]
 if(signature.some((n,i)=>data[i]!==n))throw new Error('基准图 PNG 内容损坏，未进入编辑状态')
 if(hash!==undefined){const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');if(actual!==hash)throw new Error('基准图哈希不匹配，请重新打开原版本')}
 return new Blob([bytes],{type:'image/png'})
}
