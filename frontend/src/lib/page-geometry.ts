export interface Point { x:number; y:number }
export interface Rect extends Point { w:number; h:number }
export interface PageView { width:number; height:number; scale:number; panX:number; panY:number; dpr:number; crop:Rect }
export function validateRect(r:Rect):Rect {
 if(![r.x,r.y,r.w,r.h].every(Number.isFinite)||r.x<0||r.y<0||r.w<=0||r.h<=0||r.x+r.w>1+1e-9||r.y+r.h>1+1e-9)throw new Error('矩形超出页面')
 return r
}
// CSS pointer positions are independent of devicePixelRatio; canvas backing
// pixels alone use DPR. Persisted coordinates always refer to the full raster.
export function toPage(p:Point,v:PageView):Point { return {x:v.crop.x+(p.x-v.panX)/(v.width*v.scale)*v.crop.w,y:v.crop.y+(p.y-v.panY)/(v.height*v.scale)*v.crop.h} }
export function toView(p:Point,v:PageView):Point { return {x:(p.x-v.crop.x)/v.crop.w*v.width*v.scale+v.panX,y:(p.y-v.crop.y)/v.crop.h*v.height*v.scale+v.panY} }
export function pixelBox(r:Rect,w:number,h:number):[number,number,number,number] {validateRect(r);return [Math.floor(r.x*w),Math.floor(r.y*h),Math.min(w,Math.ceil((r.x+r.w)*w)),Math.min(h,Math.ceil((r.y+r.h)*h))]}
export function withinCrop(r:Rect,c:Rect):boolean {return r.x>=c.x-1e-9&&r.y>=c.y-1e-9&&r.x+r.w<=c.x+c.w+1e-9&&r.y+r.h<=c.y+c.h+1e-9}
export function between(a:Point,b:Point):Rect {return {x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)}}
export function tuple(r:Rect):number[]{return [r.x,r.y,r.w,r.h]}
export function rect(r:number[]):Rect {return {x:r[0],y:r[1],w:r[2],h:r[3]}}
