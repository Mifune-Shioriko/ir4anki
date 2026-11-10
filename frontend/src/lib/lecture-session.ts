export function sourcePage(source:{page_start:number;page_end:number;page?:number},initial?:number){
 const n=initial??source.page??source.page_start
 return Math.max(source.page_start,Math.min(source.page_end,Number.isInteger(n)?n:source.page_start))
}
export async function currentText<T>(load:()=>Promise<T>,current:()=>boolean,attach:(value:T)=>unknown){
 const value=await load();if(current())return attach(value)
}
export function selectionText(previous:string,value:string,toolbar:boolean){return toolbar?previous:value}

export function sourceKey(s:{document_id:string;revision_id:string;segment_id:string;page_start:number;page_end:number;page?:number}){return [s.document_id,s.revision_id,s.segment_id,s.page_start,s.page_end,s.page].join('/')}

export function referenceViewer<T extends {page?:number;page_start:number;page_end:number;page_count?:number}>(source:T){
 return {source:{...source,page_start:1,page_end:source.page_count??source.page_end},initialPage:source.page??source.page_start}
}
export function readingDockVisible(state:{annotation?:boolean;qa?:boolean;occlusion?:boolean;back?:boolean}){
 return !state.annotation&&!state.qa&&!state.occlusion&&!state.back
}
export function commitPage(text:string,current:number,start:number,end:number){
 const n=Number(text)
 return text.trim()&&Number.isInteger(n)&&n>=start&&n<=end?{page:n,error:''}:{page:current,error:`请输入 ${start}–${end} 范围内的整数页码`}
}

export function samePage(a:{document_id:string;revision_id:string;page:number},b:{document_id:string;revision_id:string;page:number}){
 return a.document_id===b.document_id&&a.revision_id===b.revision_id&&a.page===b.page
}
/** /reading/cards has a 100-ID request cap. Filter page first, then fetch all. */
export async function relatedDetails<T extends {noteId:number}>(ids:number[],load:(ids:number[])=>Promise<{cards:T[];degraded?:boolean}>){
 const unique=[...new Set(ids.filter(n=>n>0))],rows=new Map<number,T>()
 for(let i=0;i<unique.length;i+=100){
  const result=await load(unique.slice(i,i+100))
  if(result.degraded)throw new Error('卡片内容暂不可用')
  for(const card of result.cards??[])rows.set(card.noteId,card)
 }
 return [...rows.values()]
}
