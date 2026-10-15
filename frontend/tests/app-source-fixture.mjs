// Build-time extraction of the actual App handlers/effect. No App startup,
// collection, scheduling, network or copied approximation of source logic.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
export function appSourceFixture() {
  const id='virtual:app-source-fixture'
  return {name:'actual-app-source-fixture',resolveId:s=>s===id?'\0'+id:undefined,load(s){
    if(s!=='\0'+id)return
    const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
    const start=source.indexOf('  const [sourceGeneration, setSourceGeneration]')
    const end=source.indexOf('  const sourceToChunk',start)
    const effectStart=source.lastIndexOf('  createEffect(() => {',source.indexOf('    const generation = sourceGeneration'))
    const effectEnd=source.indexOf('  const traceAvailable',effectStart)
    if([start,end,effectStart,effectEnd].some(n=>n<0))throw new Error('App source fixture extraction contract changed')
    const code=`import {createRoot,createSignal,createEffect,onMount,onCleanup} from 'solid-js';
import {remapPath} from ${JSON.stringify(fileURLToPath(new URL('../src/lib/files-state.ts',import.meta.url)))};
export function startSourceFixture() { return createRoot(dispose=>{
const card={noteId:42,identity:'same-card'},screenCard=()=>card,readingMode=()=>true,srcCache=new Map();
const [traceSource,setTraceSource]=createSignal(null),[rdChunks,setRdChunks]=createSignal([{path:'a/note.md',identity:'same-chunk'}]);
const [traceChunk,setTraceChunk]=createSignal(null),[addSource,setAddSource]=createSignal(null),[segEditChunk,setSegEditChunk]=createSignal(null);
const [listedPaths,setListedPaths]=createSignal(new Set(['a/note.md'])),[traceOpen,setTraceOpen]=createSignal(false);
const requests=[];const api={readingSource:noteId=>new Promise(resolve=>requests.push({noteId,resolve}))};
${source.slice(start,end)}
${source.slice(effectStart,effectEnd)}
return {dispose,requests:()=>requests.map(r=>({noteId:r.noteId})),resolve:(i,value)=>requests[i].resolve(value),
snapshot:()=>({source:traceSource(),chunks:rdChunks(),card,listed:[...listedPaths()]}),
relocate:(oldPath,newPath)=>window.dispatchEvent(new CustomEvent('files-relocated',{detail:{oldPath,newPath}}))};
});}`
    return ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
  }}
}
