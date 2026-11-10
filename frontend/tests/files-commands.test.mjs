import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import {SerialBatch,normalizeTargets} from '../src/lib/files-browser.ts'
import {webcrypto} from 'node:crypto'
globalThis.crypto??=webcrypto
const source=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const ast=ts.createSourceFile('FilesScreen.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:v=>resolve(v)}}
function declaration(name){let code;function walk(n){if(ts.isFunctionDeclaration(n)&&n.name?.text===name)code=n.getText(ast);ts.forEachChild(n,walk)}walk(ast);assert.ok(code,'Actual handler absent: '+name);return code}
const create=(names,context,prelude='')=>new Function(...Object.keys(context),ts.transpileModule(prelude+names.map(declaration).join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+`;return {${names}}`)(...Object.values(context))
// Actual current refresh generation/loading, retaining current-folder navigation,
// selection, focus and preview rather than the removed expanded-tree model.
const queue=[],written=[],errors=[];let selection=new Set(['甲/a.md']),history=['','甲'],loading=false,folder='甲',focused='甲/a.md',preview='甲/a.md'
const context={operations:{reconcile:async()=>null},api:{filesList:()=>{const d=deferred();queue.push(d);return d.promise},filesMembership:async()=>({paths:['甲/a.md']})},setLoading:v=>loading=v,setLoadError:e=>errors.push(e),setSelected:v=>selection=typeof v==='function'?v(selection):v,setFocused:v=>focused=typeof v==='function'?v(focused):v,setHistory:v=>history=typeof v==='function'?v(history):v,setMembership(){},setFiles:v=>written.push(v),setDirs(){},mkdirPending:()=>null,currentDir:()=>folder,setCurrentDir:v=>folder=v,previewPath:()=>preview,setPreviewPath:v=>preview=v,requestDraftLeave:()=>true,remember(){}}
const {reload}=create(['reload'],context,'let generation=0;let alive=true;')
const older=reload();await Promise.resolve();const newer=reload();await Promise.resolve()
queue[0].resolve({files:[],dirs:[]});await older;assert.equal(loading,true)
queue[1].resolve({files:[{path:'甲/a.md',title:'a'}],dirs:['甲','空']});await newer
assert.deepEqual(written,[[{path:'甲/a.md',title:'a'}]]);assert.equal(loading,false);assert.deepEqual([...selection],['甲/a.md']);assert.equal(folder,'甲');assert.equal(focused,'甲/a.md');assert.equal(preview,'甲/a.md');assert.deepEqual(history,['','甲'])
const late=reload();await Promise.resolve();const latest=reload();await Promise.resolve();queue[3].resolve({files:[{path:'甲/a.md',title:'latest'}],dirs:['甲']});await latest;queue[2].resolve({files:[],dirs:[]});await late;assert.equal(written.at(-1)[0].title,'latest');assert.deepEqual(errors.filter(Boolean),[])
// The redesign shares relocation/runItems for dialog Enter and menu commands.
// Exercise both real handler bodies and the real serial controller while pending.
let working=false,renameCalls=0,dialogCloses=0,refreshes=0,focuses=0
const response=deferred(),target={path:'甲/a.md',isDir:false}
const rContext={alive:true,busy:()=>working,requestDraftLeave:()=>true,setWorking:v=>working=v,setDialog:v=>{assert.equal(v,null);dialogCloses++},setMessage(){},setDialogError(){},normalizeTargets,batch:new SerialBatch(()=>{}),operations:{run:(kind,path,value)=>{assert.equal(kind,'rename');assert.equal(path,target.path);assert.equal(value,'new.md');renameCalls++;return response.promise},hasPending:()=>false},reload:async()=>refreshes++,focusRow:()=>focuses++}
const {relocation}=create(['runItems','relocation'],rContext)
const first=relocation('rename',[target],'new.md');assert.equal(working,true);await relocation('rename',[target],'new.md');assert.equal(renameCalls,1);response.resolve({path:'甲/new.md',operationId:'fixture'});await first
assert.equal(working,false);assert.equal(dialogCloses,1);assert.equal(refreshes,1);assert.equal(focuses,1)
console.log('PASS actual refresh generation/loading and current navigation/selection/preview retention; shared rename command pending dedup')
