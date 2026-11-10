// Render the actual ordinary shell host. Do not use a Material ARIA attribute as a layout hook.
import fs from 'node:fs'
import assert from 'node:assert/strict'
import path from 'node:path'
import {pathToFileURL,fileURLToPath} from 'node:url'
import postcss from 'postcss'
import ts from 'typescript'
import {root,render,createComponent,find} from './solid-lifecycle-renderer.mjs'
const source=process.env.LECTURE_LAYOUT_SOURCE_ROOT??fileURLToPath(new URL('../src/',import.meta.url))
const {StudyCardShell,StudyToolbar}=await import(pathToFileURL(path.join(source,'components/StudyCardShell.tsx')))
const read=path.join(source,'components/LectureReadingCard.tsx')
const ast=ts.createSourceFile(read,fs.readFileSync(read,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
let call;function visit(n){if(ts.isJsxOpeningElement(n)&&n.tagName.getText(ast)==='StudyCardShell')call=n;ts.forEachChild(n,visit)}visit(ast)
for(const lecture of [true,false,undefined]){
 const host=root();const dispose=render(()=>createComponent(StudyCardShell,{stage:'reading',source:'SYNTHETIC',lecture,get toolbar(){return createComponent(StudyToolbar,{label:'阅读操作',children:'SYNTHETIC'})},children:''}),host)
 const shell=find(host,n=>n.tag==='section')
 assert.equal(shell.props['data-lecture-reading'],lecture?'':undefined,'actual shell opt-in marker/default behavior')
 assert.equal(shell.props.class,'flashcard-wrapper stage-reading study-shell','shared default classes stay identical')
 dispose()
}
assert.ok(call?.attributes.properties.some(a=>a.name?.text==='lecture'&&(!a.initializer||a.initializer.getText(ast)==='{true}')),'actual lecture caller must opt into the ordinary host layout marker')
const tree=postcss.parse(fs.readFileSync(path.join(source,'study.css'),'utf8'))
const header='.study-shell[data-lecture-reading] > .study-header'
const toolbar=header+' > .study-toolbar'
const needed=new Map([[header,{'grid-template-rows':'32px auto'}],[toolbar,{'flex-wrap':'wrap','margin-left':'0','justify-content':'flex-end'}],[toolbar+' md-text-button',{'min-height':'48px','--md-text-button-container-height':'48px'}]])
for(const [selector,props] of needed){
 const rules=[];tree.walkRules(selector,r=>rules.push(r));assert.equal(rules.length,1,'ordinary host scoped rule: '+selector)
 const rule=rules[0];assert.equal(rule.parent.type,'atrule');assert.equal(rule.parent.name,'media');assert.equal(rule.parent.params,'(max-width:640px)','desktop unchanged')
 const actual=Object.fromEntries(rule.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
 for(const [name,value] of Object.entries(props))assert.equal(actual[name],value,selector+' '+name)
}
assert.ok(!tree.toString().includes(':has(> [aria-label="按页书签分割"])'),'no delegated Material ARIA layout hook')
let icons;tree.walkRules('.study-toolbar md-icon-button',rule=>icons=Object.fromEntries(rule.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value])))
assert.equal(icons.width,'48px');assert.equal(icons.height,'48px')
console.log('PASS mounted ordinary lecture shell marker; defaults unchanged; scoped mobile wrap/48px; desktop unchanged')
