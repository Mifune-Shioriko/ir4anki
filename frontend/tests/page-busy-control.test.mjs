// Actual production TSX controls; all collaborators below are unit fakes.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import {sourceKey} from '../src/lib/lecture-session.ts'
const source=readFileSync(process.env.PAGE_BUSY_SOURCE??new URL('../src/components/LectureReadingCard.tsx',import.meta.url),'utf8')
const tree=ts.createSourceFile('LectureReadingCard.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
function find(predicate,node=tree){if(predicate(node))return node;return ts.forEachChild(node,n=>find(predicate,n))}
const compile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const handler=find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='confirmSplit').getText(tree)
const button=find(n=>ts.isJsxElement(n)&&n.openingElement.tagName.getText(tree)==='md-filled-tonal-button'&&n.children.some(child=>ts.isJsxText(child)&&child.text.includes('确认书签分割')))
const disabled=button.openingElement.attributes.properties.find(p=>p.name?.text==='disabled').initializer.expression.getText(tree)
const memo=find(n=>ts.isVariableDeclaration(n)&&n.name.getText(tree)==='sourceIdentity').initializer.arguments[0].getText(tree)
const leaf={kind:'pdf',document_id:'d',revision_id:'r',segment_id:'s',page_start:3,page_end:8,version:1}
test('pre-opened split confirmation is disabled during parent publication refresh',()=>{
 const evaluate=new Function('loading','props',compile('return ('+disabled+')'))
 assert.equal(evaluate(()=>false,{busy:true}),true)
 assert.equal(evaluate(()=>false,{busy:false}),false)
})
for(const [label,busy,loading] of [['parent busy',true,false],['already loading',false,true]])test(`confirmSplit handler refuses ${label} before beginning mutation`,async()=>{
 let calls=0,begins=0,loadingWrites=0
 const props={source:leaf,busy,onSplitBegin:()=>{begins++;return {current:()=>true,finish:()=>{}}},onSplit:()=>{},onChanged:()=>{}}
 const bindings={props,loading:()=>loading,setLoading:()=>loadingWrites++,setError:()=>{},setSplit:()=>{},start:()=>4,end:()=>6,lectureRequest:async()=>{calls++;return {chunks:[leaf]}},crypto:{randomUUID:()=> 'synthetic-unit-request'}}
 const confirm=new Function(...Object.keys(bindings),compile('let generation=0;'+handler+';return confirmSplit'))(...Object.values(bindings))
 await confirm();assert.equal(begins,0);assert.equal(calls,0);assert.equal(loadingWrites,0)
})
test('same-leaf version/page metadata refresh preserves pre-open form identity',()=>{
 const props={source:leaf},identity=new Function('props','sourceKey',compile('return ('+memo+')'))(props,sourceKey)
 const initial=identity();props.source={...leaf,version:2,page:4,status:'active'}
 assert.equal(identity(),initial,'metadata-only refresh would close/reset the pre-opened 4–6 form')
 props.source={...props.source,segment_id:'child',page_start:4,page_end:6}
 assert.notEqual(identity(),initial,'a real child change still resets the form')
})
