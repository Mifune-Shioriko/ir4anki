// Real LectureReadingCard + SplitDialog + lectureRequest. Browser-only child
// viewers/editors are omitted here; the parent browser fixture mounts them.
import assert from 'node:assert/strict'
import {pathToFileURL} from 'node:url'
import {root,render,createComponent,find,all,text} from './split-renderer.mjs'
globalThis.document={activeElement:null}
globalThis.window={addEventListener(){},removeEventListener(){}}
const calls=[];let reject=false,release
const source={kind:'pdf',document_id:'synthetic-doc',revision_id:'synthetic-rev',segment_id:'synthetic-seg',page_start:1,page_end:8,version:3,path:'synthetic.pdf',chunk_key:'synthetic-seg',status:'todo'}
globalThis.fetch=async(url,options)=>{calls.push({url,...options});await new Promise(r=>release=r);return {ok:!reject,json:async()=>reject?{detail:'Synthetic failure'}:{chunks:[{...source,segment_id:'child'}]}}}
const {LectureReadingCard}=await import(pathToFileURL((process.env.SPLIT_SOURCE_ROOT??process.cwd()+'/src')+'/components/LectureReadingCard.tsx'))
const {IconContentCut}=await import(pathToFileURL((process.env.SPLIT_SOURCE_ROOT??process.cwd()+'/src')+'/components/icons.tsx'))
const iconHost=root();render(()=>createComponent(IconContentCut,{}),iconHost);const expected=find(iconHost,n=>n.tag==='path').props.d
const host=root(),adopted=[];const dispose=render(()=>createComponent(LectureReadingCard,{source,busy:false,onComplete(){},onNext(){},onSkip(){},onExit(){},onChanged(){},onSplit:(parent,children)=>adopted.push({parent,children})}),host)
await Promise.resolve()
const cut=find(host,n=>n.tag==='md-icon-button'&&n.props['aria-label']==='按页书签分割')
assert.equal(find(cut,n=>n.tag==='path').props.d,expected,'PDF split must be scissors, not pencil')
cut.props.onClick();let d=find(host,n=>n.tag==='md-dialog');assert.ok(d,'split confirmation must be a viewport modal')
assert.equal(all(d,n=>n.tag==='img').length,2)
const start=find(d,n=>n.props['aria-label']==='分割开始页'),end=find(d,n=>n.props['aria-label']==='分割结束页')
end.props.onInput({currentTarget:{value:'9'}});let confirm=()=>find(d,n=>n.tag==='md-filled-tonal-button');assert.equal(confirm().props.disabled,true);confirm().props.onClick();assert.equal(calls.length,0)
start.props.onInput({currentTarget:{value:'2'}});end.props.onInput({currentTarget:{value:'4'}});assert.match(text(d),/第 2–4 页/);assert.match(text(d),/5–8 页/)
find(d,n=>n.tag==='md-text-button').props.onClick();assert.equal(find(host,n=>n.tag==='md-dialog'),undefined);assert.equal(calls.length,0)
cut.props.onClick();d=find(host,n=>n.tag==='md-dialog');assert.match(text(d),/第 2–4 页/,'cancel keeps boundary')
reject=true;confirm().props.onClick();confirm().props.onClick();assert.equal(calls.length,1);release();await new Promise(r=>setImmediate(r));assert.match(text(d),/Synthetic failure/);assert.equal(confirm().props.disabled,false)
reject=false;confirm().props.onClick();assert.equal(calls.length,2);release();await new Promise(r=>setImmediate(r));assert.equal(find(host,n=>n.tag==='md-dialog'),undefined);assert.equal(adopted.length,1)
for(const c of calls){assert.equal(c.url,'/api/lectures/split');assert.equal(c.method,'POST');const p=JSON.parse(c.body);assert.equal(typeof p.request_id,'string');delete p.request_id;assert.deepEqual(p,{segment_id:'synthetic-seg',revision_id:'synthetic-rev',page_start:2,page_end:4,expected_version:3})}
dispose();console.log('PASS mounted LectureReadingCard: scissors, modal, both thumbnails, valid boundaries, cancel preserves boundary, duplicate guard, failure/retry and exact existing request')
