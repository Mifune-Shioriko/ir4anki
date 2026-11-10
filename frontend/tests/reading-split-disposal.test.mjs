// Actual ReadingCard confirmation, followed by App's trace-close ordering.
import assert from 'node:assert/strict'
import {pathToFileURL} from 'node:url'
import {root,render,createComponent,find} from './split-renderer.mjs'
globalThis.document={addEventListener(){},removeEventListener(){},createTreeWalker(){return {nextNode:()=>null}}}
globalThis.NodeFilter={SHOW_TEXT:4}
const {ReadingCard}=await import(pathToFileURL((process.env.SPLIT_SOURCE_ROOT??process.cwd()+'/src')+'/components/ReadingCard.tsx'))
const observations=[];const unhandled=[];const onUnhandled=e=>unhandled.push(e);process.on('unhandledRejection',onUnhandled)
try{
 for(const mode of ['false','reject']){
  const host=root();let source={kind:'markdown',path:'synthetic.md',chunk_key:'trace',seg_id:9,status:'todo',heading_path:[],line_start:1,text:'head\ntail',cards_created:[]},settle,fail,staleReads=0
  const pending=new Promise((resolve,reject)=>{settle=resolve;fail=reject})
  const dispose=render(()=>createComponent(ReadingCard,{get chunk(){if(!source){staleReads++;throw new Error('trace source cleared')}return source},busy:false,onComplete(){},onNext(){},onSkip(){},onExit(){},onAdd(){},onCloze(){},onSplit:()=>pending}),host)
  await Promise.resolve()
  const body=find(host,n=>n.props.class?.includes('reading-chunk-body')),item={dataset:{lineStart:'1',lineEnd:'2'}}
  body.props.onClick({target:{closest:s=>s==='a'?null:item}})
  find(host,n=>n.tag==='md-icon-button'&&n.props['data-split-action']!==undefined).props.onClick()
  find(host,n=>n.tag==='md-filled-tonal-button').props.onClick()
  dispose();source=null
  if(mode==='false')settle(false);else fail(new Error('stale split rejection'))
  await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve))
  observations.push({mode,staleReads,unhandled:unhandled.map(e=>e.message)})
  unhandled.length=0
 }
 console.log(JSON.stringify(observations))
 for(const observation of observations){assert.equal(observation.staleReads,0,`${observation.mode}: disposed confirmation must not read invalid props`);assert.deepEqual(observation.unhandled,[],`${observation.mode}: no unhandled rejection after trace close`)}
}finally{process.removeListener('unhandledRejection',onUnhandled)}
console.log('PASS pending confirmation -> trace owner disposal/source clear -> false or rejection: no stale reads or unhandled rejection')
