import {test} from 'node:test'
import assert from 'node:assert/strict'
import {containmentProblems} from './study-containment-browser.mjs'
const box={visible:true,top:100,bottom:900,height:800,clientHeight:800,scrollHeight:32000,overflowY:'auto'}
const bounded={viewportHeight:1000,documentHeight:1000,outerScrollY:0,main:box,reference:box,dock:{visible:true,top:900,bottom:980,height:80}}
test('live-browser contract accepts only bounded inner scrolling and an in-viewport dock',()=>assert.deepEqual(containmentProblems(bounded,{requireBoth:true}),[]))
test('parent v1 32095px outer expansion is rejected even when six canvases exist',()=>{
 const failure={...bounded,documentHeight:32095,main:{...box,bottom:32000,height:31900,clientHeight:31900},reference:{...box,bottom:32000,height:31900,clientHeight:31900},dock:{visible:true,top:32000,bottom:32095,height:95}}
 assert(containmentProblems(failure,{requireBoth:true}).length>=3)
})
test('a collapsed source, hidden dock and fake non-overflowing PDF cannot pass',()=>{
 assert(containmentProblems({...bounded,reference:{...box,clientHeight:0,height:0},dock:{visible:false}},{requireBoth:true}).length)
 assert(containmentProblems({...bounded,reference:{...box,scrollHeight:800}},{requireBoth:true}).includes('long PDF does not overflow its internal scroller'))
})
