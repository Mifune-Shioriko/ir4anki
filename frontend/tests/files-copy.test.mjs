import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
const s=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const start=s.indexOf('  const copyPath =')>=0?s.indexOf('  const copyPath ='):s.indexOf('  const command =')
const end=s.indexOf('  const applyRelocation =',start)
const code=ts.transpileModule(s.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
for(const mode of ['unavailable','rejected','success']) {
 const messages=[],writes=[]
 const navigator=mode==='unavailable'?{}:{clipboard:{writeText:async path=>{writes.push(path);if(mode==='rejected')throw Error('synthetic denied')}}}
 const command=new Function('navigator','busy','showSnack','openRename','openMove','openDelete',code+';return command')(navigator,()=>false,m=>messages.push(m),()=>{},()=>{},()=>{})
 command({path:'甲/合成.md',isDir:false},'copy')
 await new Promise(r=>setTimeout(r,0))
 assert(messages.length,mode+' gives no accessible status')
 if(mode==='success'){assert.deepEqual(writes,['甲/合成.md']);assert(messages[0].includes('已复制'))}
 else {assert(messages[0].includes('手动复制'));assert(messages[0].includes('甲/合成.md'))}
 console.log('PASS native command copy '+mode+'; synthetic path only')
}
