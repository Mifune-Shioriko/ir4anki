import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import {normalizeTargets} from '../src/lib/files-browser.ts'
const source=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const ast=ts.createSourceFile('FilesScreen.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
let code
function walk(n){if(ts.isFunctionDeclaration(n)&&n.name?.text==='command')code=n.getText(ast);ts.forEachChild(n,walk)}walk(ast)
assert.ok(code,'Actual current command handler absent')
const compiled=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const results=[]
for(const mode of ['unavailable','rejected','success']) {
 const messages=[],writes=[],path='甲/合成.md'
 const navigator=mode==='unavailable'?{}:{clipboard:{writeText:async path=>{writes.push(path);if(mode==='rejected')throw Error('synthetic denied')}}}
 const command=new Function('navigator','busy','setMessage','normalizeTargets','alive',compiled+';return command')(navigator,()=>false,m=>messages.push(m),normalizeTargets,true)
 try{
  command('copy',[{path,isDir:false}]);await new Promise(r=>setTimeout(r,0))
  assert(messages.length,mode+' gives no accessible status')
  if(mode==='success'){assert.deepEqual(writes,[path]);assert.match(messages[0],/已复制/)}
  else {assert.match(messages[0],/无法复制|手动复制/);assert.match(messages[0],/完整名称|路径|手动复制/)}
  results.push({mode,status:'PASS',messages,writes})
 }catch(e){results.push({mode,status:'FAIL',messages,writes,stack:e.stack})}
}
console.log(JSON.stringify(results,null,2));process.exitCode=results.some(r=>r.status==='FAIL')?1:0
