import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { buildTree, allDirPaths } from '../src/lib/tree.ts'
const source=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const transpile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:v=>resolve(v)}}
const create=(body,context,name)=>new Function(...Object.keys(context),transpile(body)+`;return ${name}`)(...Object.values(context))
// Exercise the actual refresh handler with synthetic out-of-order API responses.
const queue=[],written=[];let loading=false,expanded=new Set(['甲']),selected='甲/a.md',folder='甲',focused='甲/a.md'
const context={operations:{reconcile:async()=>null},api:{filesList:()=>{const d=deferred();queue.push(d);return d.promise},filesMembership:async()=>({paths:['甲/a.md']})},document:{activeElement:null},requestAnimationFrame:fn=>fn(),focusRow(){},setLoading:v=>loading=v,setLoadError(){},setMembership(){},setFiles:v=>written.push(v),setDirs(){},buildTree,allDirPaths,setExpanded:fn=>expanded=fn(expanded),currentDir:()=>folder,setCurrentDir:v=>folder=v,selected:()=>selected,setSelected:v=>selected=v,focused:()=>focused,setFocused:v=>focused=v}
const start=source.indexOf('  const reload = async () => {'),end=source.indexOf('  onMount(reload)',start)
const reload=create('let refreshGeneration=0;let alive=true;'+source.slice(start,end),context,'reload')
const older=reload();await Promise.resolve();const newer=reload();await Promise.resolve()
queue[0].resolve({files:[],dirs:[]});await older;assert.equal(loading,true)
queue[1].resolve({files:[{path:'甲/a.md',title:'a'}],dirs:['甲','空']});await newer
assert.deepEqual(written,[[{path:'甲/a.md',title:'a'}]]);assert.equal(loading,false);assert(expanded.has('甲'));assert.equal(selected,'甲/a.md');assert.equal(folder,'甲');assert.equal(focused,'甲/a.md')
const late=reload();await Promise.resolve();const latest=reload();await Promise.resolve();queue[3].resolve({files:[{path:'甲/a.md',title:'latest'}],dirs:['甲']});await latest;queue[2].resolve({files:[],dirs:[]});await late;assert.equal(written.at(-1)[0].title,'latest')
// Exercise the real rename handler: two Enter submissions while pending issue one API call.
let renameBusy=false,renameCalls=0;const response=deferred(),target={path:'甲/a.md',isDir:false}
const rContext={renameTarget:()=>target,renameBusy:()=>renameBusy,renameValue:()=> 'new',setRenameBusy:v=>renameBusy=v,operations:{run:()=>{renameCalls++;return response.promise},hasPending:()=>false},setRenameTarget(){},showSnack(){},applyRelocation(){},setUndoId(){},reload:async()=>{},requestAnimationFrame:fn=>fn(),focusRow(){},focused:()=> '甲/new.md'}
const ra=source.indexOf('  const submitRename = async () => {'),rb=source.indexOf('  // ---- move dialog',ra)
const rename=create(source.slice(ra,rb),rContext,'submitRename')
const first=rename();await rename();assert.equal(renameCalls,1);response.resolve({path:'甲/new.md',operationId:'fixture'});await first;assert.equal(renameBusy,false)
console.log('PASS actual refresh generation/loading and preserved selection/focus/expansion; repeated rename Enter dedup')
