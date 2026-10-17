import assert from 'node:assert/strict'
import { sourceScroll } from '../src/lib/source-scroll.ts'

// Real helper with deterministic geometry/clock; mounted browser suite checks DOM.
let frameId=0,timerId=0
const frames=new Map(),timers=new Map(),observers=[]
globalThis.requestAnimationFrame=fn=>{frames.set(++frameId,fn);return frameId}
globalThis.cancelAnimationFrame=id=>frames.delete(id)
globalThis.setTimeout=fn=>{timers.set(++timerId,fn);return timerId}
globalThis.clearTimeout=id=>timers.delete(id)
globalThis.getComputedStyle=()=>({paddingTop:'16px'})
globalThis.ResizeObserver=class {
  targets=new Set()
  constructor(callback){this.callback=callback;observers.push(this)}
  observe(target){this.targets.add(target)}
  disconnect(){this.targets.clear()}
}
const flush=()=>{const pending=[...frames.values()];frames.clear();for(const fn of pending)fn()}
function fixture(visible=true){
  let absoluteTop=1000,height=30,contentHeight=2000
  const classes=new Set(),listeners=new Map(),scrolls=[]
  const panel={isConnected:true,clientHeight:400,clientWidth:500,clientTop:0,scrollHeight:5000,scrollTop:0,
    getClientRects:()=>visible?[{}]:[],getBoundingClientRect:()=>({top:100}),
    scrollTo(options){this.scrollTop=options.top;scrolls.push(options)},
    addEventListener(name,fn){listeners.set(name,fn)},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name)},
  }
  const anchor={isConnected:true,offsetWidth:500,getBoundingClientRect:()=>({top:100+absoluteTop-panel.scrollTop,height}),classList:{add:n=>classes.add(n),remove:n=>classes.delete(n)}}
  const session=sourceScroll(panel,{getBoundingClientRect:()=>({height:contentHeight,width:500})},anchor,[anchor],visible),observer=observers.at(-1)
  return {panel,session,observer,classes,listeners,scrolls,
    show(){visible=true;session.setVisible(true)},
    grow(amount){absoluteTop+=amount;contentHeight+=amount;observer.callback()},resizeAnchor(amount){height+=amount;observer.callback()},
    event(name,event={}){listeners.get(name)?.(event)},
  }
}
let f=fixture(false);flush();assert.equal(f.scrolls.length,0);assert.equal(timers.size,0)
f.show();flush();assert.equal(f.panel.scrollTop,984);assert(f.classes.has('note-hl'))
const firstTimer=timerId
f.grow(300);f.grow(200);assert.equal(frames.size,1);flush();assert.equal(f.panel.scrollTop,1484);assert.equal(timers.size,1);assert(timerId>firstTimer)
let before=timerId
f.observer.callback();flush();assert.equal(timerId,before,'steady layout does not restart fade')
f.resizeAnchor(200);flush();assert(timerId>before,'image inside target renews exposure')
f.event('wheel',{deltaX:0,deltaY:550});assert.equal(f.listeners.size,0);assert.equal(f.observer.targets.size,0)
before=f.scrolls.length;f.grow(500);f.session.setVisible(false);f.show();flush();assert.equal(f.scrolls.length,before,'reader position survives layout/re-show')
f.session.dispose();assert.equal(timers.size,0);assert.equal(f.classes.size,0)
f.grow(500);flush();assert.equal(f.scrolls.length,before,'disposed target ignores stale callback')
for(const [event,data] of [['touchstart',{}],['pointerdown',{button:0}],['keydown',{key:'PageDown',target:{closest:()=>null}}]]){
 f=fixture();flush();f.event(event,data);before=f.scrolls.length;f.grow(500);flush();assert.equal(f.scrolls.length,before);f.session.dispose()
}
f=fixture();flush();f.event('keydown',{key:'ArrowDown',target:{closest:()=>({})}});assert(f.listeners.size,'caret navigation is not panel scroll intent');f.session.dispose()
f=fixture();flush();f.panel.scrollTop+=200;f.event('scroll');assert.equal(f.listeners.size,0,'native scrollbar scroll cancels ownership');f.session.dispose()
f=fixture();flush();f.grow(200);f.panel.scrollTop+=200;f.event('scroll');assert(f.listeners.size,'layout-induced scroll anchoring is not user intent');flush();f.session.dispose()
f=fixture();flush();f.panel.clientHeight=250;f.panel.scrollTop+=30;f.event('scroll');assert(f.listeners.size,'viewport-induced scroll is not reader intent');f.observer.callback();flush();f.session.dispose()
f=fixture();f.session.dispose();flush();assert.equal(f.scrolls.length,0,'unmount before first frame is safe')
assert.equal(frames.size,0);assert.equal(timers.size,0);assert(observers.every(o=>o.targets.size===0))
console.log('PASS pending visibility, panel-only alignment, coalesced layout, exposure, input cancellation, stale cleanup')
