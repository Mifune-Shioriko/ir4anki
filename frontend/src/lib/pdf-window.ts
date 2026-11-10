export function pageAspect(page:{width:number;height:number;rotation?:number}){
 if(!Number.isFinite(page.width)||!Number.isFinite(page.height)||page.width<=0||page.height<=0)throw new Error('课件页面尺寸无效')
 // Inspection dimensions already include the page's native rotation/CropBox.
 return page.width/page.height
}
export function visiblePages(rects:{page:number;top:number;bottom:number}[],height:number,margin=700,max=6):number[]{
 return rects.filter(r=>r.bottom>=-margin&&r.top<=height+margin)
  .sort((a,b)=>Math.abs((a.top+a.bottom)/2-height/2)-Math.abs((b.top+b.bottom)/2-height/2))
  .slice(0,max).map(r=>r.page).sort((a,b)=>a-b)
}
