// First gate: actual production App build, synthetic intercepted APIs.
// Native collection/corpus/admission setup is shared with --seed-only. No server.
import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync} from 'node:child_process'
import {createRequire} from 'node:module'
import {createHash,randomUUID} from 'node:crypto'
import assert from 'node:assert/strict'
const require=createRequire(import.meta.url)
const {chromium}=require('/home/shioriko/.hermes/hermes-agent/node_modules/playwright')
const run=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..')
const configPath=process.env.FILES_CANDIDATE_CONFIG??path.join(run,'first-render.json')
const config=JSON.parse(fs.readFileSync(configPath,'utf8'))
const dist=config.build,hash=b=>createHash('sha256').update(b).digest('hex')
const manifest=JSON.parse(fs.readFileSync(config.manifest,'utf8'))
for(const [name,expected] of Object.entries(manifest))assert.equal(hash(fs.readFileSync(path.join(dist,name))),expected,'Frozen build drift: '+name)
const sourceManifestPath=config.source_manifest??config.product_manifest
const sourceManifest=JSON.parse(fs.readFileSync(sourceManifestPath,'utf8'))
for(const [name,expected] of Object.entries(sourceManifest))assert.equal(hash(fs.readFileSync(path.join(run,'candidate',name))),expected,'Frozen source drift: '+name)
const canonicalIndex=fs.readFileSync(path.join(dist,'index.html'),'utf8'),indexAssets=Array.from(canonicalIndex.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g),m=>m[1])
assert.ok(indexAssets.some(x=>x.endsWith('.js')));for(const asset of indexAssets){const name=asset.replace(/^\//,'');assert.ok(manifest[name], 'Index asset absent from manifest: '+name)}
const out=path.join(run,'first-render-evidence',Date.now()+'-'+randomUUID().slice(0,8));fs.mkdirSync(out,{recursive:true})
const seedFile=path.join(out,'seed.json'),tmp='/home/shioriko/.hermes/cache/scratch/fmi';fs.mkdirSync(tmp,{recursive:true})
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.startsWith('ANKI_')&&k!=='ANKICONNECT_URL'))
Object.assign(env,{NATIVE_FIXTURE_ROOT:path.join(out,'setup'),TMPDIR:tmp,PYTHONDONTWRITEBYTECODE:'1'})
const python='/home/shioriko/.local/share/ir4anki/releases/lecture-v08-20261024T164539/.venv/bin/python'
const candidate=path.join(run,'candidate')
const setupArgs=[path.join(candidate,'scripts/lecture_test_driver.py'),path.join(candidate,'scripts/files_first_render_seed.py'),'--seed-only','--root',env.NATIVE_FIXTURE_ROOT,'--output',seedFile]
const setup=spawnSync(python,setupArgs,{env,cwd:candidate,encoding:'utf8',timeout:45000})
fs.writeFileSync(path.join(out,'setup.log'),(setup.stdout??'')+(setup.stderr??''))
const ledger={scope:'Actual compiled production App; synthetic HTTP routes from disposable native-seeded DTO snapshot. Not native backend HTTP acceptance.',generation:config.generation,configPath,build:dist,manifest:config.manifest,manifestSha256:hash(fs.readFileSync(config.manifest)),sourceManifest:sourceManifestPath,sourceManifestSha256:hash(fs.readFileSync(sourceManifestPath)),indexSha256:hash(Buffer.from(canonicalIndex)),indexAssets:indexAssets.map(url=>({url,sha256:manifest[url.replace(/^\//,'')]})),servedAssets:[],status:'RUNNING',output:out,setup:{argv:[python,...setupArgs],exit_status:setup.status},cases:[],errors:[],requests:[],denied:[]}
const save=()=>fs.writeFileSync(path.join(out,'ledger.json'),JSON.stringify(ledger,null,2)+'\n')
save()
try{assert.equal(setup.status,0,'Seed setup failed: '+path.join(out,'setup.log'))
const seed=JSON.parse(fs.readFileSync(seedFile,'utf8'))
assert.equal(seed.admission.length,2);assert.equal(seed.state.flow.reading_slots,seed.admission.flatMap(x=>x.segments).length)
assert.ok(seed.listing.files.some(f=>f.path===seed.long_path));assert.ok(seed.listing.files.length>40)
assert.ok(seed.paths.collection.startsWith(env.NATIVE_FIXTURE_ROOT+'/'));assert.ok(fs.existsSync(config.chrome))}catch(e){ledger.status='SETUP_FAIL';ledger.error=String(e);ledger.stack=e.stack;save();console.error(JSON.stringify({status:ledger.status,output:out,error:String(e)}));process.exit(1)}
const seed=JSON.parse(fs.readFileSync(seedFile,'utf8'))
if(process.argv.includes('--seed-only')){ledger.status='SETUP_PASS_BROWSER_NOT_RUN';save();console.log(JSON.stringify({status:ledger.status,generation:config.generation,build:dist,output:out,files:seed.listing.files.length,admitted:seed.admission.length}));process.exit(0)}
let browser,page
let current
async function stage(name,fn){current={name,stage:name,width:1280,height:577,theme:'dark',status:'RUNNING'};ledger.cases.push(current);save();try{await fn(current);current.status='PASS';save();console.log('PASS '+name)}catch(e){current.status='FAIL';current.error=String(e);current.stack=e.stack;save();try{await page?.screenshot({path:path.join(out,name+'-failure.png')})}catch{};throw e}}
const origin='http://localhost:48763'
try{
 // Parent only. Never disable sandbox or use a real/user browser profile.
 browser=await chromium.launch({executablePath:config.chrome,headless:true,chromiumSandbox:true,env:{...env,TMPDIR:tmp,TMP:tmp,TEMP:tmp}})
 const context=await browser.newContext({viewport:{width:1280,height:577},colorScheme:'dark',reducedMotion:'reduce',serviceWorkers:'block'})
 await context.routeWebSocket('**/*',socket=>{ledger.denied.push('websocket');save();socket.close()})
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());ledger.requests.push({method:req.method(),path:u.pathname});save()
  if(u.origin!==origin){ledger.denied.push(req.url());save();return route.abort()}
  if(u.pathname.startsWith('/api/')){
   let dto
   if(req.method()==='GET'&&u.pathname==='/api/status')dto={anki_backend:'pylib'}
   else if(req.method()==='GET'&&u.pathname==='/api/flow/state')dto=seed.state
   else if(req.method()==='GET'&&u.pathname==='/api/files/list')dto=seed.listing
   else if(req.method()==='GET'&&u.pathname==='/api/files/membership')dto=seed.membership
   else if(req.method()==='GET'&&u.pathname==='/api/files/raw'){
    const p=u.searchParams.get('path');assert.ok(Object.hasOwn(seed.raw,p),'Unexpected raw path '+p);dto={path:p,text:seed.raw[p]}
   }else{ledger.denied.push(req.method()+' '+u.pathname);save();return route.abort()}
   return route.fulfill({json:dto})
  }
  if(u.pathname==='/favicon.ico')return route.fulfill({status:204,body:''})
  const file=path.resolve(dist,'.'+(u.pathname==='/'?'/index.html':decodeURIComponent(u.pathname)))
  if(!file.startsWith(dist+'/')||!fs.existsSync(file)||!fs.statSync(file).isFile()){ledger.denied.push('asset '+u.pathname);save();return route.abort()}
  const mime={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf'}
  ledger.servedAssets.push({path:u.pathname,sha256:hash(fs.readFileSync(file)),expected:manifest[path.relative(dist,file)]});save();assert.equal(hash(fs.readFileSync(file)),manifest[path.relative(dist,file)]);await route.fulfill({contentType:mime[path.extname(file)]??'application/octet-stream',body:fs.readFileSync(file)})
 })
 page=await context.newPage();page.setDefaultTimeout(8000);page.setDefaultNavigationTimeout(15000);page.on('pageerror',e=>{ledger.errors.push(e.stack??String(e));save()})
 const findRow=p=>page.locator('.fb-row[data-entry='+JSON.stringify(p)+']')
 const geometry=()=>page.evaluate(()=>{const el=document.querySelector('.fb-list'),row=el.querySelector('.fb-row');if(!el||!row)throw Error('Mounted list/row absent');const a=el.getBoundingClientRect(),b=row.getBoundingClientRect();return {listTop:a.top,listHeight:a.height,rowTop:b.top,rowHeight:b.height,scrollTop:el.scrollTop}})
 await stage('actual-App-first-render',async c=>{
  await page.goto(origin+'/');await page.getByRole('navigation',{name:'主导航'}).waitFor();await page.getByRole('progressbar',{name:'复习',exact:true}).waitFor()
  await page.getByRole('button',{name:'文件',exact:true}).click();await page.locator('.fb-row').first().waitFor()
  for(const selector of ['.app-shell--files','.fb-workspace','.fb-sidebar','.fb-main','.fb-list','.fb-command-slot','.fb-footer','.nav-rail__footer'])assert.equal(await page.locator(selector).count(),1,'Required mounted '+selector)
  assert.equal(await page.locator('.progress-ring').count(),2);assert.equal(await page.locator('.fb-preview').count(),0)
  assert.equal(await page.locator('.fb-sidebar .fb-row').count(),0);assert.ok(await page.locator('.fb-row').count()>40)
  assert.equal(await page.getByRole('progressbar',{name:'阅读',exact:true}).getAttribute('aria-valuemax'),String(seed.state.flow.reading_slots))
  c.source=seed.scope;await page.screenshot({path:path.join(out,c.name+'.png')})
 })
 await stage('selection-fixed-slot-and-real-dblclick',async c=>{
  const folder=findRow('笔记');await folder.scrollIntoViewIfNeeded();c.before=await geometry()
  await folder.click();c.single=await geometry();await folder.click({modifiers:['Control']});c.clear=await geometry()
  await folder.click();await page.keyboard.press('Control+a');c.multi=await geometry()
  await page.getByRole('button',{name:'取消选择',exact:true}).click();c.after=await geometry()
  for(const state of ['single','clear','multi','after'])for(const key of Object.keys(c.before))assert.ok(Math.abs(c[state][key]-c.before[key])<.6,`${state} ${key}: ${JSON.stringify(c)}`)
  assert.equal(await folder.getAttribute('aria-selected'),'false');await folder.dblclick()
  await findRow('笔记/入门.md').waitFor();assert.match(await page.locator('.fb-breadcrumb').innerText(),/笔记/)
  const long=findRow(seed.long_path);await long.scrollIntoViewIfNeeded();c.long=await long.locator('.fb-basename').evaluate(e=>({text:e.textContent,height:e.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(e).lineHeight),width:e.getBoundingClientRect().width,scrollWidth:e.scrollWidth}))
  assert.equal(c.long.text,seed.long_path.split('/').at(-1));assert.ok(c.long.height>c.long.lineHeight);assert.ok(c.long.scrollWidth<=c.long.width+1)
  await page.getByRole('button',{name:'上一级',exact:true}).click();await findRow('欢迎.md').waitFor()
 })
 async function layout(c){
  c.metrics=await page.evaluate(()=>{
   const box=s=>{const e=document.querySelector(s);if(!e)throw Error('Required mounted '+s);const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,overflowY:getComputedStyle(e).overflowY}}
   return {width:innerWidth,height:innerHeight,docWidth:document.documentElement.scrollWidth,docHeight:document.documentElement.scrollHeight,scrollY,list:box('.fb-list'),main:box('.fb-main'),footer:box('.fb-footer'),rings:box('.nav-rail__footer'),app:box('.app'),ringBoxes:Array.from(document.querySelectorAll('.progress-ring')).map(e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom}})}
  });save();const m=c.metrics
  assert.ok(m.docWidth<=m.width+1,'Document horizontal overflow');assert.ok(m.docHeight<=m.height+1,'Document vertical overflow');assert.equal(m.scrollY,0)
  assert.equal(m.list.overflowY,'auto');assert.ok(m.list.clientHeight>60);assert.ok(m.list.scrollHeight>m.list.clientHeight,'Short height list must scroll independently');assert.ok(m.list.scrollWidth<=m.list.clientWidth+1)
  assert.ok(m.app.scrollHeight<=m.app.clientHeight+1,'App must not be scroll owner')
  for(const b of [m.footer,m.rings,...m.ringBoxes])assert.ok(b.top>=0&&b.bottom<=m.height+1,'Footer/ring outside viewport')
  c.reach=await page.locator('.fb-list').evaluate(e=>{e.scrollTop=e.scrollHeight;const r=e.querySelector('.fb-row:last-child').getBoundingClientRect(),b=e.getBoundingClientRect();return {bottom:r.bottom,listBottom:b.bottom,scrollTop:e.scrollTop,max:e.scrollHeight-e.clientHeight}})
  assert.ok(Math.abs(c.reach.scrollTop-c.reach.max)<=1);assert.ok(c.reach.bottom<=c.reach.listBottom+1);assert.equal(await page.evaluate(()=>scrollY),0)
  await page.screenshot({path:path.join(out,c.name+'.png')});await page.locator('.fb-list').evaluate(e=>e.scrollTop=0)
 }
 await stage('short-height-list-scroll-owner-preview-closed',layout)
 await stage('preview-opens-only-on-explicit-Space',async c=>{
  const file=findRow('欢迎.md');await file.click();assert.equal(await page.locator('.fb-preview').count(),0)
  await file.focus();await page.keyboard.press('Space');await page.getByRole('region',{name:'文件预览',exact:true}).waitFor();await page.locator('.fb-preview .note-body h1').waitFor();assert.match(await page.locator('.fb-preview-body').innerText(),/欢迎/);await layout(c)
  await page.getByRole('button',{name:'关闭预览',exact:true}).click();assert.equal(await page.locator('.fb-preview').count(),0)
 })
 await stage('Files-keyboard-no-study-mutations',async()=>{
  const file=findRow('欢迎.md');await file.click();await file.focus()
  await page.keyboard.press('F2');await page.locator('dialog[open]').waitFor();await page.keyboard.press('Escape');assert.equal(await page.locator('dialog[open]').count(),0);await file.focus()
  for(const key of ['1','2','3','4','Control+z','Enter','Space'])await page.keyboard.press(key)
  await page.getByRole('searchbox',{name:'按名称搜索'}).fill('');await page.getByRole('searchbox',{name:'按名称搜索'}).press('Space')
  assert.equal(await page.locator('.fb-preview').count(),0,'Typing Space must not open preview')
  assert.equal(ledger.requests.filter(r=>r.method!=='GET').length,0,'No study/mutation request authorized by Files keys')
  assert.deepEqual(ledger.errors,[]);assert.deepEqual(ledger.denied,[])
 })
 ledger.status='PASS';save();console.log(JSON.stringify({status:ledger.status,cases:ledger.cases.length,generation:ledger.generation,output:out}))
}catch(e){ledger.status=page?'FAIL':'BLOCKED';ledger.error=String(e);ledger.stack=e.stack;save();console.error(JSON.stringify({status:ledger.status,error:String(e),output:out}));process.exitCode=1}
finally{await browser?.close();save()}
