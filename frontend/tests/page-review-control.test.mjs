// Source-extracted production control regressions; collaborators are unit fakes.
// Parent browser probe separately exercises real App/backend/native publication.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
const root=process.env.PAGE_CONTROL_SOURCE??new URL('../src/',import.meta.url)
const source=file=>readFileSync(typeof root==='string'?root+'/'+file:new URL(file,root),'utf8')
const compile=text=>ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const pdf={kind:'pdf',document_id:'d',revision_id:'r',segment_id:'s',page_start:1,page_end:20,version:1,page:4}
test('R3 note-only revealed review ignores last selected reading page in same root',async()=>{
 const text=source('components/RelatedPanel.tsx'),start=text.indexOf('  const refresh = async () =>'),end=text.indexOf('\n  createEffect(() => {',start)
 const requests=[]
 const fn=new Function('props','api','lectureRequest','sourcePage','pageRequestPath','relatedDetails','chunkIds','setCards','setCount','setSelfNote','setStatus',compile('let reqToken=0;'+text.slice(start,end)+';return refresh'))
 const refresh=fn({chunk:null,noteId:4,pageSource:{...pdf,page:3}}, {readingSource:async()=>pdf,readingCards:async()=>({cards:[]})},async path=>{requests.push(path);return {note_ids:[]}},s=>s.page??s.page_start,(s,p)=>`/${s.document_id}/revisions/${s.revision_id}/pages/${p}`,async()=>[],()=>[],()=>{},()=>{},()=>{},()=>{})
 await refresh();assert.deepEqual(requests,['/d/revisions/r/pages/4/related'])
})
test('R1 traced publication refreshes current effective version and preserves page',async()=>{
 const text=source('App.tsx')
 let chunk={...pdf,page:undefined,version:1},selected={...pdf,page:5},traceSource={...pdf,authoring_id:'io'},related=0,traceOpen=true
 const card={cardId:10,noteId:4},fresh={...pdf,effective_chunk:{...pdf,page:undefined,version:2},effective_source:{...pdf,page:4}}
 const bindings={traceChunk:()=>chunk,screenCard:()=>card,traceOpen:()=>traceOpen,traceGeneration:1,lecturePage:()=>selected,srcCache:new Map([[4,traceSource]]),setSourceGeneration:()=>{},setTraceRefreshing:()=>{},setRelatedToken:fn=>related=fn(related),resync:async()=>{},api:{readingSource:async()=>fresh},setTraceChunk:value=>chunk=typeof value==='function'?value(chunk):value,setTraceSource:value=>traceSource=typeof value==='function'?value(traceSource):value,sourcePage:s=>s.page??s.page_start,lectureRequest:async()=>fresh.effective_chunk,pageRequestPath:(s,p)=>`/${s.document_id}/revisions/${s.revision_id}/pages/${p}`}
 let body
 const declaration=text.match(/  const refreshTraceAfterPublication\s*=\s*async\s*\([^)]*\)\s*=>\s*\{/)
 if(declaration){
  const start=declaration.index,end=text.indexOf('\n  const ',start+declaration[0].length)
  body='let traceRefreshGeneration=0;'+text.slice(start,end)+';return refreshTraceAfterPublication'
 }else{
  const callback=text.match(/authoringId=\{traceSource\(\)\?\.authoring_id\}[\s\S]*?onChanged=\{(\(\)=>\{setRelatedToken\(t=>t\+1\);void resync\(\)\})\}/)
  assert.ok(callback,'actual traced callback not found');body='return '+callback[1]
 }
 const refresh=new Function(...Object.keys(bindings),compile(body))(...Object.values(bindings))
 await refresh();await new Promise(resolve=>setTimeout(resolve,0))
 assert.equal(chunk.version,2,'trace retained pre-publication version, next split would send stale expected_version')
 assert.equal(chunk.page,5,'selected trace page changed while refreshing version')
 assert.equal(traceSource.effective_chunk.version,2)
})

for(const navigation of ['leave','card','segment','same-leaf-page'])test(`R1 delayed refresh guards ${navigation}`,async()=>{
 const text=source('App.tsx'),declaration=text.match(/  const refreshTraceAfterPublication\s*=\s*async\s*\([^)]*\)\s*=>\s*\{/)
 assert.ok(declaration)
 const end=text.indexOf('\n  const ',declaration.index+declaration[0].length)
 let chunk={...pdf,page:undefined,version:1},selected={...pdf,page:5},open=true,currentCard={cardId:10,noteId:4},resyncs=0
 let resolve;const delayed=new Promise(done=>resolve=done)
 const fresh={...pdf,effective_chunk:{...pdf,segment_id:'original-note-other-leaf',version:9}}
 const bindings={traceChunk:()=>chunk,screenCard:()=>currentCard,traceOpen:()=>open,traceGeneration:1,lecturePage:()=>selected,srcCache:new Map(),setSourceGeneration:()=>{},setTraceRefreshing:()=>{},setRelatedToken:()=>{},resync:async()=>{resyncs++},api:{readingSource:async()=>{await delayed;return fresh}},setTraceChunk:value=>chunk=value,setTraceSource:()=>{},sourcePage:s=>s.page??s.page_start,lectureRequest:async()=>({...pdf,page:undefined,version:2}),pageRequestPath:(s,p)=>`/${s.document_id}/${p}`}
 const refresh=new Function(...Object.keys(bindings),compile('let traceRefreshGeneration=0;'+text.slice(declaration.index,end)+';return refreshTraceAfterPublication'))(...Object.values(bindings))
 const promise=refresh()
 if(navigation==='leave')open=false
 if(navigation==='card')currentCard={cardId:11,noteId:9}
 if(navigation==='segment')chunk={...chunk,segment_id:'new-segment'}
 if(navigation==='same-leaf-page')selected={...selected,page:6}
 resolve();await promise
 if(navigation==='same-leaf-page'){assert.equal(chunk.version,2);assert.equal(chunk.page,6)}
 else{assert.equal(chunk.version,1);if(navigation!=='segment')assert.equal(resyncs,0)}
})
