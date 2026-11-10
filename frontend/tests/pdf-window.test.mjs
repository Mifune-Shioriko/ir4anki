import {test} from 'node:test'
import assert from 'node:assert/strict'
import {pageAspect,visiblePages} from '../src/lib/pdf-window'
test('true display geometry uses already rotated inspection dimensions',()=>{
 assert.equal(pageAspect({width:640,height:480,rotation:0}),640/480)
 assert.equal(pageAspect({width:480,height:640,rotation:90}),480/640)
 assert.throws(()=>pageAspect({width:0,height:640}))
})
test('long document resource window reaches far end and is strictly bounded',()=>{
 const rects=Array.from({length:1000},(_,i)=>({page:i+1,top:i*340-330000,bottom:i*340-330000+320}))
 const chosen=visiblePages(rects,800)
 assert(chosen.includes(972));assert(chosen.length<=6);assert(!chosen.includes(1))
 assert.deepEqual(visiblePages([],800),[])
})
test('short pages cannot create an unbounded canvas/text/annotation window',()=>{
 const chosen=visiblePages(Array.from({length:100},(_,i)=>({page:i+1,top:i*10,bottom:i*10+8})),800)
 assert.equal(chosen.length,6)
})
