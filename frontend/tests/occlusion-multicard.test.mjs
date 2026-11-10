// Unit harness executes production handlers; collaborators are explicit fakes.
import fs from 'node:fs'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import ts from 'typescript'
const file=process.argv[2]??'src/components/LectureReadingCard.tsx'
const source=fs.readFileSync(file,'utf8'),ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
function handler(name){
 let found;function visit(n){if(ts.isFunctionDeclaration(n)&&n.name?.text===name)found=n;ts.forEachChild(n,visit)}visit(ast)
 assert.ok(found,`missing production handler ${name}`)
 return ts.transpile(found.getText(ast),{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None})
}
const page={kind:'pdf',document_id:'d',revision_id:'r',segment_id:'s',page:3}
const a={authoring_id:'A',source:page},b={authoring_id:'B',source:page}
function setup(options={}){
 const calls=[],cache=new Map([['ir4anki.authoring.page.d.r.3','A']]);let editor=null,error='',loading=false,picker=false,projects=[]
 const context={generation:1,props:{source:page,busy:false,...options},page:()=>3,qa:()=>false,editor:()=>editor,loading:()=>loading,setLoading:v=>loading=v,setError:v=>error=v,hideReader:()=>{},setEditor:v=>editor=v,setPicker:v=>picker=v,setProjects:v=>projects=v,locator:(s,p)=>({...s,page:p}),pageRequestPath:(s,p)=>`/${s.document_id}/revisions/${s.revision_id}/pages/${p}`,samePage:(l,r)=>l.document_id===r.document_id&&l.revision_id===r.revision_id&&l.page===r.page,localStorage:{getItem:k=>cache.get(k),setItem:(k,v)=>cache.set(k,v)},lectureRequest:async(path,method,body)=>{calls.push({path,method,body});if(path.endsWith('/pages/3/authoring'))return {drafts:[a,b]};if(method==='POST')return b;return path.endsWith('/B')?b:a}}
 vm.createContext(context);vm.runInContext(handler('occlusion'),context)
 return {context,calls,cache,state:()=>({editor,error,loading,picker,projects})}
}
let failures=0
async function test(name,fn){try{await fn();console.log('PASS',name)}catch(e){failures++;console.error('FAIL',name,e.message)}}
await test('new independent question bypasses traced and cached A',async()=>{const x=setup({authoringId:'A'});await x.context.occlusion(true);assert.equal(x.state().editor.authoring_id,'B');assert.equal(x.calls.length,1);assert.equal(x.calls[0].method,'POST');assert.equal(x.calls[0].body.source.page,3)})
await test('explicit B selection bypasses traced and cached A',async()=>{const x=setup({authoringId:'A'});await x.context.occlusion(false,'B');assert.equal(x.state().editor.authoring_id,'B');assert.equal(x.calls.length,1);assert.equal(x.calls[0].path,'/authoring/B')})
await test('legacy cached single question reopens A',async()=>{const x=setup();await x.context.occlusion();assert.equal(x.state().editor.authoring_id,'A');assert.equal(x.calls.length,1)})
await test('busy or open editor cannot start another project',async()=>{const x=setup({busy:true});await x.context.occlusion(true);assert.equal(x.calls.length,0);x.context.props.busy=false;await x.context.occlusion(true);await x.context.occlusion(true);assert.equal(x.calls.length,1)})
await test('page picker loads server persisted independent identities',async()=>{const x=setup();vm.runInContext(handler('chooseProject'),x.context);await x.context.chooseProject();assert.equal(x.state().picker,true);assert.equal(x.state().projects.length,2)})
await test('late page response never opens wrong page',async()=>{const x=setup();x.context.lectureRequest=async()=>{x.context.page=()=>4;return b};await x.context.occlusion(true);assert.equal(x.state().editor,null)})
if(failures)process.exitCode=1
