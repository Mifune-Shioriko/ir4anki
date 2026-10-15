import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const read=p=>readFileSync(process.env.STUDY_SOURCE ? process.env.STUDY_SOURCE+'/'+p : new URL('../src/'+p,import.meta.url),'utf8')
test('both modes share shell and typography without sidebar layout',()=>{for(const f of ['ReadingCard','Flashcard']){const s=read(`components/${f}.tsx`);assert.match(s,/<StudyCardShell/);assert.match(s,/study-content/);assert.doesNotMatch(s,/reading-chunk-body note-body/)}})
test('dock uses the same shell boundary',()=>{assert.match(read('components/ReadingCard.tsx'),/<StudyActionDock/);assert.match(read('components/ActionArea.tsx'),/<StudyActionDock/);assert.match(read('App.tsx'),/dock=\{/)})
test('source hr and toolbar keyboard contract',()=>{assert.doesNotMatch(read('index.css'),/\.card-content hr\s*\{\s*display:\s*none/);const s=read('components/StudyCardShell.tsx');assert.match(s,/stopPropagation/);assert.match(s,/title=\{props.source\}/)})
test('reading command order and exact handlers survive; trace has no status chips',()=>{
 const s=read('components/ReadingCard.tsx')
 const labels=['撤销上次评分','退出本轮','添加卡片','添加挖空','编辑片段'];const positions=labels.map(l=>s.indexOf(`aria-label="${l}"`));assert.deepEqual(positions,[...positions].sort((a,b)=>a-b));assert(positions.every(p=>p>=0))
 for(const handler of ['props.onUndo?.()','props.onExitRound?.()','props.onCloze(sel.selText(), sel.selBlock())','props.onSplit(p)','props.onExit()','props.onComplete()']) assert(s.includes(handler),handler)
 assert.doesNotMatch(s,/reading-status-chip|header-actions-sep|本文件进度/)
 assert.match(s,/disabled=\{props.busy \|\| props.chunk.status === 'container' \|\| props.chunk.seg_id == null\}/)
})
test('review retains delete confirmation wiring and bottom exit while deck is not duplicated',()=>{
 const s=read('components/Flashcard.tsx'),app=read('App.tsx')
 assert.match(app,/onDelete=\{\(\) => askDelete\(currentCard\(\)!\)\}/)
 assert.match(app,/onClick=\{exitFlow\}>退出本轮/)
 assert.equal((s.match(/props.card.deckName/g)||[]).length,1)
 assert.doesNotMatch(s,/header-actions-sep/)
})
test('shared toolbar contains keys without blocking native activation',()=>{
 const s=read('components/StudyCardShell.tsx');const body=s.match(/onKeyDown=\{event => \{(.*?)\}\}/s)[1]
 const handle=new Function('event',body)
 for(const key of [' ','Enter','a','ArrowRight']) {let stopped=0;handle({key,stopPropagation(){stopped++},preventDefault(){throw Error('native activation must survive')}});assert.equal(stopped,[' ','Enter'].includes(key)?1:0)}
})
test('20px glyph contract sets both installed Material size tokens',()=>{
 const css=read('index.css');const toolbar=css.match(/\.study-toolbar md-icon-button\s*\{([^}]+)\}/)[1]
 assert.match(toolbar,/--md-icon-button-icon-size:\s*20px/)
 assert.match(toolbar,/--md-icon-size:\s*20px/)
})
test('identical reading source is memoized so metadata updates retain line annotations',()=>{
 const reading=read('components/ReadingCard.tsx')
 assert.match(reading,/createMemo\(\(\) => renderMarkdown\(props.chunk.text\)\)/)
 assert.match(reading,/innerHTML=\{bodyHtml\(\)\}/)
})
test('acceptance fixture initializes actual theme and provides reactive synthetic metadata',()=>{
 const fixture=readFileSync(new URL('./study-fixture.tsx',import.meta.url),'utf8')
 assert.match(fixture,/import \{ syncThemeColor \} from '..\/src\/theme'/)
 assert.match(fixture,/syncThemeColor\(\)/)
 for(const property of ['cardId:config().cardId','isNew:config().isNew','ord:config().ord','status:config().status','sourceVersion']) assert(fixture.includes(property),property)
 assert.match(fixture,/class="flow-actions"/)
})
test('actual App shortcut source respects dialog, reading busy, trace and review guards',async()=>{
 const {default:ts}=await import('typescript')
 const app=read('App.tsx');const block=app.slice(app.indexOf('  const isTypingTarget ='),app.indexOf('  // Window-level registration'))
 const js=ts.transpileModule(block,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
 const run=(state,key,target={tagName:'BODY'})=>{
  const calls=[];const flags={editOpen:false,addOpen:false,deleteTarget:null,segEditOpen:false,traceOpen:false,clozeOpen:false,canUndo:false,answering:false,syncOpen:false,rdBusy:false,revealed:false,...state}
  const context=Object.fromEntries(Object.entries(flags).map(([name,value])=>[name,()=>value]))
  Object.assign(context,{section:()=>state.section??'study',HTMLElement:class {},phase:()=>state.phase??'reading',rdCurrent:()=>({chunk_key:'synthetic'}),currentCard:()=>({cardId:42})})
  for(const name of ['closeTrace','openReadingAdd','openReadingCloze','undo','rdAct','setRevealedFor','answer'])context[name]=(...args)=>calls.push({name,args})
  const handle=new Function(...Object.keys(context),js+';return handleKeyDown')(...Object.values(context))
  handle({target,key,composedPath:()=>state.eventPath??[target],preventDefault(){},ctrlKey:state.ctrlKey??false,metaKey:false});return calls
 }
 for(const section of ['files','readingList']) for(const key of [' ','1','2','3','4','a','c','s','n','z']) assert.deepEqual(run({section,canUndo:true,ctrlKey:true},key),[],section+key)
 for(const guard of ['editOpen','addOpen','deleteTarget','segEditOpen','clozeOpen','syncOpen','rdBusy']) assert.deepEqual(run({[guard]:true},' '),[],guard)
 for(const target of [{tagName:'INPUT'},{tagName:'TEXTAREA'},{tagName:'DIV',isContentEditable:true}])assert.deepEqual(run({},' ',target),[])
 assert.deepEqual(run({eventPath:[{tagName:'MD-OUTLINED-TEXT-FIELD'},{tagName:'INPUT'}]},' '),[],'Material shadow input')
 assert.deepEqual(run({},' '),[{name:'rdAct',args:['complete']}])
 assert.deepEqual(run({phase:'review'},' '),[{name:'setRevealedFor',args:[42]}])
 assert.deepEqual(run({phase:'review',revealed:true},'3'),[{name:'answer',args:[3]}])
 assert.deepEqual(run({phase:'review',revealed:true,answering:true},'3'),[])
 assert.deepEqual(run({traceOpen:true},' '),[])
 assert.deepEqual(run({traceOpen:true},'Escape'),[{name:'closeTrace',args:[]}])
 assert.deepEqual(run({traceOpen:true},'c'),[{name:'openReadingCloze',args:['','']}])
 assert.deepEqual(run({traceOpen:true,clozeOpen:true},'c'),[])
 assert.deepEqual(run({canUndo:true,ctrlKey:true},'z'),[{name:'undo',args:[]}])
})
test('study cleaner restores TeX before sanitizing generated KaTeX layout',()=>{
 const card=read('components/Flashcard.tsx')
 assert.match(card,/annotation\[encoding="application\/x-tex"\]/)
 assert.match(card,/createTextNode/)
 assert.match(card,/cleanStudyHtml\(renderField/)
 assert.match(card,/ignoredClasses: \['katex'\]/)
})

test('Markdown uses the installed KaTeX renderer matching review CSS',()=>{
 const markdown=read('lib/markdown.ts')
 assert.match(markdown,/import katex from 'katex'/)
 assert.match(markdown,/use\(katexPlugin, \{ throwOnError: false, katex \}\)/)
})

test('shared math scrollers retain horizontal overflow with symmetric ink clearance',()=>{
 const css=read('index.css')
 assert.match(css,/\.study-content \.katex-display\s*\{[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;[^}]*padding-block:\s*0?\.2em;/)
 assert.match(css,/:is\(\.note-body, \.study-content\) \.katex-block\s*\{[^}]*padding:\s*0?\.2em 0;/)
})
