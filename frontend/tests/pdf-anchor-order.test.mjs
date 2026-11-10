// Execute the actual component callbacks against controlled geometry, forcing
// the reviewer order: old queued RAF -> grown DOM -> measure -> ResizeObserver.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const source=readFileSync(process.env.PDF_ANCHOR_SOURCE??new URL('../src/components/PdfReferenceViewer.tsx',import.meta.url),'utf8')
function callback(name){
 const start=source.indexOf(' function '+name+'('),open=source.indexOf('{',start)
 assert(start>=0);let depth=1,end=open+1
 for(;depth&&end<source.length;end++){if(source[end]==='{')depth++;if(source[end]==='}')depth--}
 return source.slice(start,end)
}
function observerBody(){
 const start=source.indexOf('const observer=new ResizeObserver(()=>{'),open=source.indexOf('{',start)
 assert(start>=0);let depth=1,end=open+1
 for(;depth&&end<source.length;end++){if(source[end]==='{')depth++;if(source[end]==='}')depth--}
 return source.slice(open+1,end-1)
}
function fixture(){
 return new Function(`
  let growth=0,frame=99,measuredWidth=600,viewportAnchor,viewAnchor,aligned='same-source',manualBrowse=true;
  let position=1000,writes=0,reserved=93,extent=93,ready=true,hostTop=-1000;
  const annotationHeights=new Map(),p={page:1},height=()=>reserved,setHeight=n=>reserved=n,schedule=()=>{};
  const host={getBoundingClientRect:()=>({top:hostTop})};
  const content={querySelector:()=>ready?{}:null,getBoundingClientRect:()=>({height:extent})};
  const props={active:true};
  const scroll={get scrollTop(){return position},set scrollTop(n){position=n;writes++},clientHeight:800,clientWidth:632,get scrollHeight(){return 4000+growth},getBoundingClientRect(){return {top:0,bottom:800}}};
  const slots=new Map([1,2,3,4].map(page=>[page,{get offsetTop(){return (page-1)*1000+(page>1?growth:0)},getBoundingClientRect(){const top=this.offsetTop-scroll.scrollTop;return {top,bottom:top+1000,height:1000}}}]));
  const getComputedStyle=()=>({paddingLeft:'16',paddingRight:'16'}),setWindowPages=()=>{},setWidth=()=>{},visiblePages=()=>[];
  ${callback('captureViewport')}
  ${callback('restoreViewport')}
  ${callback('measure')}
  const notify=()=>{${observerBody()}};
  return {scroll,captureViewport,restoreViewport,measure,notify,grow:n=>{growth+=n;extent+=n},nativeScroll:n=>position=n,resetWrites:()=>writes=0,get writes(){return writes},get reserved(){return reserved},setReady:v=>ready=v,setHostTop:v=>hostTop=v,resizeBelow:n=>extent+=n};
 `)()
}
test('queued measure before annotation observer cannot replace the pre-growth anchor',()=>{
 const f=fixture();f.captureViewport();f.grow(100);f.measure();f.restoreViewport()
 assert.equal(f.scroll.scrollTop,1100,'100px above-reader growth must be compensated even if a queued RAF measures first')
})
test('actual manual scroll is new authority while an observer/frame is pending',()=>{
 const f=fixture();f.captureViewport();f.grow(100);f.scroll.scrollTop=1250;f.captureViewport();f.measure();f.restoreViewport()
 assert.equal(f.scroll.scrollTop,1250,'do not chase a previous reader position after actual scrolling')
})
test('explicit bottom intent survives queued post-growth measurement',()=>{
 const f=fixture();f.scroll.scrollTop=3200;f.captureViewport();f.grow(100);f.measure();f.restoreViewport()
 assert.equal(f.scroll.scrollTop,3300)
})

test('unchanged cached observer cannot pull native Home back to stale bottom intent',()=>{
 const f=fixture();f.nativeScroll(3200);f.captureViewport();f.nativeScroll(3000);f.resetWrites();f.notify()
 assert.equal(f.writes,0,'cached equal extent must generate zero programmatic scrollTop writes')
 assert.equal(f.scroll.scrollTop,3000)
})
test('unchanged observer cannot undo native wheel progression before its scroll event',()=>{
 const f=fixture();f.captureViewport();f.nativeScroll(1250);f.resetWrites();f.notify()
 assert.equal(f.writes,0);assert.equal(f.scroll.scrollTop,1250)
})
test('initial/loading observer is not geometry authority',()=>{
 const f=fixture();f.captureViewport();f.nativeScroll(1250);f.setReady(false);f.resetWrites();f.notify()
 assert.equal(f.writes,0);assert.equal(f.reserved,93)
})
test('real loaded extent delta still compensates above-reader growth after queued measure',()=>{
 const f=fixture();f.captureViewport();f.grow(100);f.measure();f.resetWrites();f.notify()
 assert.equal(f.reserved,193);assert.equal(f.scroll.scrollTop,1100);assert.equal(f.writes,1)
})
test('below-viewport real extent changes update reservation without scroll writes',()=>{
 const f=fixture();f.captureViewport();f.setHostTop(900);f.resizeBelow(100);f.resetWrites();f.notify()
 assert.equal(f.reserved,193);assert.equal(f.writes,0);assert.equal(f.scroll.scrollTop,1000)
})
test('new manual scroll authority is retained during real loaded extent update',()=>{
 const f=fixture();f.captureViewport();f.grow(100);f.nativeScroll(1250);f.captureViewport();f.measure();f.notify()
 assert.equal(f.scroll.scrollTop,1250);assert.equal(f.writes,0,'same anchored position is not permission for a scrollTop assignment')
})
test('real loaded extent growth retains explicit user bottom intent',()=>{
 const f=fixture();f.nativeScroll(3200);f.captureViewport();f.grow(100);f.measure();f.notify()
 assert.equal(f.scroll.scrollTop,3300)
})

test('negative real extent compensation performs exactly one necessary write',()=>{
 const f=fixture();f.captureViewport();f.grow(100);f.notify();f.captureViewport();f.grow(-40);f.measure();f.resetWrites();f.notify()
 assert.equal(f.scroll.scrollTop,1060);assert.equal(f.writes,1)
})
test('same bottom target after a real extent update produces zero writes',()=>{
 const f=fixture();f.grow(100);f.nativeScroll(3300);f.captureViewport();f.resetWrites();f.notify()
 assert.equal(f.scroll.scrollTop,3300);assert.equal(f.writes,0)
})
