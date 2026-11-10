import test from 'node:test'
import assert from 'node:assert/strict'
import {toPage,toView,pixelBox,validateRect,withinCrop} from '../src/lib/page-geometry.ts'
test('page coordinates survive view zoom pan and device pixels',()=>{
 for(const scale of [.25,1,3])for(const dpr of [1,2,3]){
  const view={width:1200,height:800,scale,panX:17,panY:-31,dpr,crop:{x:.1,y:.2,w:.5,h:.6}}
  const p={x:.25,y:.45};const screen=toView(p,view);const round=toPage(screen,view)
  assert.ok(Math.abs(round.x-p.x)<1e-10);assert.ok(Math.abs(round.y-p.y)<1e-10)
 }
})
test('outward rounding conceals entire target',()=>assert.deepEqual(pixelBox({x:.11,y:.22,w:.1,h:.1},101,99),[11,21,22,32]))
test('bounds and crop exclusion reject instead of silently cutting answers',()=>{
 assert.throws(()=>validateRect({x:NaN,y:0,w:1,h:1}))
 assert.throws(()=>validateRect({x:.9,y:0,w:.2,h:.2}))
 assert.equal(withinCrop({x:.1,y:.1,w:.2,h:.2},{x:0,y:0,w:1,h:1}),true)
 assert.equal(withinCrop({x:.1,y:.1,w:.2,h:.2},{x:.2,y:.2,w:.4,h:.4}),false)
})
