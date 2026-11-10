/** Actual browser geometry, independent of source spelling and PDF rendering timing. */
export async function inspectStudyContainment(page){
 return page.evaluate(()=>{
  const describe=el=>{
   if(!el)return null
   const r=el.getBoundingClientRect(),s=getComputedStyle(el)
   return {tag:el.tagName,class:el.className,visible:r.width>0&&r.height>0,top:r.top,bottom:r.bottom,height:r.height,clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,scrollTop:el.scrollTop,cssHeight:s.height,minHeight:s.minHeight,display:s.display,overflowY:s.overflowY,flex:s.flex,gridTemplateRows:s.gridTemplateRows}
  }
  const reference=document.querySelector('.pdf-reference-scroll'),chain=[]
  for(let el=reference;el;el=el.parentElement)chain.push(describe(el))
  return {viewportHeight:innerHeight,documentHeight:document.scrollingElement.scrollHeight,outerScrollY:scrollY,main:describe(document.querySelector('.study-scroll')),reference:describe(reference),dock:describe(document.querySelector('.study-action-dock')),chain,outsideRoot:[...document.body.children].filter(el=>el.id!=='app').map(describe),measurementCanvases:[...document.querySelectorAll('body > canvas.hiddenCanvasElement')].map(describe)}
 })
}
export function containmentProblems(m,{requireBoth=false}={}){
 const problems=[],h=m.viewportHeight
 if(m.documentHeight>h+2)problems.push('outer document exceeds viewport')
 if(Math.abs(m.outerScrollY)>1)problems.push('outer document scrolled')
 for(const name of ['main','reference']){
  const box=m[name]
  if(requireBoth&&!box?.visible)problems.push(name+' scroller missing/hidden')
  if(box?.visible&&(box.clientHeight<100||box.height>h+1||box.bottom>h+1||box.top<0||!['auto','scroll'].includes(box.overflowY)))problems.push(name+' scroller is not bounded inside viewport')
 }
 if(m.reference?.visible&&m.reference.scrollHeight<=m.reference.clientHeight*2)problems.push('long PDF does not overflow its internal scroller')
 if(m.main?.visible&&(!m.dock?.visible||m.dock.height<40||m.dock.top<m.main.bottom-2||m.dock.bottom>h+1))problems.push('actual dock is outside viewport or overlaps main scroll')
 return problems
}
