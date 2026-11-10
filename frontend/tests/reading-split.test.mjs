import assert from 'node:assert/strict'
import {createSignal} from 'solid-js'
import {pathToFileURL} from 'node:url'
import {root,render,createComponent,find,all,text} from './split-renderer.mjs'
// Host-only stubs; real ReadingCard, StudyCardShell, split picker and previews run.
globalThis.document={addEventListener(){},removeEventListener(){},createTreeWalker(){return {nextNode:()=>null}}}
globalThis.NodeFilter={SHOW_TEXT:4}
const {ReadingCard}=await import(pathToFileURL((process.env.SPLIT_SOURCE_ROOT??process.cwd()+'/src')+'/components/ReadingCard.tsx'))
const host=root(),calls=[];let release
const [busy,setBusy]=createSignal(false),[chunk,setChunk]=createSignal({kind:'markdown',path:'synthetic.md',chunk_key:'synthetic',seg_id:9,status:'todo',heading_path:[],line_start:1,text:'head\nmiddle\ntail',cards_created:[]})
const dispose=render(()=>createComponent(ReadingCard,{get chunk(){return chunk()},get busy(){return busy()},onEdit(){},onComplete(){},onNext(){},onSkip(){},onExit(){},onAdd(){},onCloze(){},onSplit:sel=>{calls.push(sel);return new Promise(r=>release=r)}}),host)
await Promise.resolve()
const cut=()=>find(host,n=>n.tag==='md-icon-button'&&n.props['data-split-action']!==undefined)
assert.ok(cut(),'MISSING_PERMANENT_MARKDOWN_SCISSORS: actual ReadingCard mounted')
assert.equal(cut().props.disabled,true)
const body=find(host,n=>n.props.class?.includes('reading-chunk-body'))
function pick(start,end=start){const item={dataset:{lineStart:String(start),lineEnd:String(end)}};body.props.onClick({target:{closest:s=>s==='a'?null:s==='.line-handle'?item:item}})}
pick(2);assert.equal(cut().props.disabled,false);pick(2);assert.equal(cut().props.disabled,true,'re-clicking selected line cancels pick');
pick(1);pick(3);assert.equal(cut().props.disabled,false);pick(2);assert.equal(cut().props.disabled,true,'clicking inside selected range cancels pick so user can re-select');
pick(1);cut().props.onClick();let single=find(host,n=>n.tag==='md-dialog');assert.match(text(single),/头：head/);assert.match(text(single),/尾：head/);find(single,n=>n.tag==='md-text-button').props.onClick();pick(3);assert.equal(cut().props.disabled,false)
assert.equal(find(host,n=>n.tag==='md-dialog'),undefined,'selection alone must not open popup')
cut().props.onClick();let dialog=find(host,n=>n.tag==='md-dialog');assert.ok(dialog)
assert.match(text(dialog),/head/);assert.match(text(dialog),/tail/)
find(dialog,n=>n.tag==='md-text-button'&&text(n)==='取消').props.onClick();assert.equal(find(host,n=>n.tag==='md-dialog'),undefined);assert.equal(cut().props.disabled,false);assert.equal(calls.length,0)
cut().props.onClick();dialog=find(host,n=>n.tag==='md-dialog');const confirm=find(dialog,n=>n.tag==='md-filled-tonal-button')
confirm.props.onClick();confirm.props.onClick();assert.deepEqual(calls,[{start_line:1,end_line:3}],'immediate duplicate dispatch blocked before parent busy updates')
release(false);await Promise.resolve();await Promise.resolve();assert.match(text(find(host,n=>n.tag==='md-dialog')),/分割未完成/);assert.ok(find(host,n=>n.tag==='md-dialog'),'same chunk after failed operation keeps confirmation for retry');assert.equal(cut().props.disabled,false)
setBusy(true);assert.equal(cut().props.disabled,true);setBusy(false)
setChunk({...chunk(),status:'container'});assert.equal(cut().props.disabled,true);setChunk({...chunk(),status:'todo',seg_id:null});assert.equal(cut().props.disabled,true)
setChunk({...chunk(),chunk_key:'child',seg_id:10,text:'child'});await Promise.resolve();assert.equal(find(host,n=>n.tag==='md-dialog'),undefined);assert.equal(cut().props.disabled,true)
dispose();console.log('PASS mounted ReadingCard: permanent action, both real previews, cancel preserves range, duplicate guard, retry, availability, chunk reset')
