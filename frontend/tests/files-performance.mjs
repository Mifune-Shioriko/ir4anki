import { writeFileSync } from 'node:fs'
import { buildTree } from '../src/lib/tree.ts'
import { filterTree } from '../src/lib/files-state.ts'
import { acquireDocument, invalidateDocuments } from '../src/lib/file-cache.ts'
const destination = process.argv[2]
if (!destination?.startsWith('/home/shioriko/.hermes/cache/scratch/')) throw Error('scratch artifact required')
const percentile = (values,p) => [...values].sort((a,b)=>a-b)[Math.ceil(values.length*p)-1]
const summarize = values => ({samples:values,p50:percentile(values,.5),p95:percentile(values,.95)})
const output={environment:{node:process.version,platform:process.platform,fixture:'generated in memory; zero network; not live browser timing'},trees:[],documents:[],dom:{status:'not measured: browser sandbox EPERM',samples:null}}
for(const count of [100,1000,10000]) {
 const files=Array.from({length:count},(_,i)=>({path:`目录${Math.floor(i/100)}/笔记${i}.md`,title:`笔记${i}`}))
 const times=[],search=[]
 for(let i=0;i<10;i++) {
  const start=performance.now();const tree=buildTree(files,['空目录/子']);times.push(performance.now()-start)
  const query=performance.now();filterTree(tree,'笔记91');search.push(performance.now()-query)
 }
 output.trees.push({count,build_ms:summarize(times),search_ms:summarize(search)})
}
for(const [name,text] of [['small','# 合成\n\n正文 $x^2$\n'],['32KB','# 合成\n\n'+'正文内容测试。\n\n'.repeat(1500)],['1MB','# 合成\n\n'+'Synthetic paragraph for cache measurement.\n\n'.repeat(24500)],['math200','# 合成公式\n\n'+('$\\frac{x^2+1}{y_2}$\n\n'.repeat(200))]]) {
 const cold=[],warm=[],render=[],fetch=[],requests=[]
 for(let i=0;i<5;i++) {
  invalidateDocuments();performance.clearMeasures('files-markdown-render')
  let n=0
  const fetcher=async()=>{const start=performance.now();n++;const doc={text,revision:'1'};fetch.push(performance.now()-start);return doc}
  const start=performance.now();await acquireDocument(name,'1',fetcher).promise;cold.push(performance.now()-start)
  const next=performance.now();await acquireDocument(name,'1',fetcher).promise;warm.push(performance.now()-next)
  render.push(performance.getEntriesByName('files-markdown-render')[0].duration);requests.push(n)
 }
 output.documents.push({name,utf8_bytes:Buffer.byteLength(text),cold_acquire_ms:summarize(cold),warm_acquire_ms:summarize(warm),synthetic_fetch_ms:summarize(fetch),render_ms:summarize(render),requests_per_cold_warm_pair:requests})
}
writeFileSync(destination,JSON.stringify(output,null,2)+'\n')
console.log(JSON.stringify(output,null,2))
