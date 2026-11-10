import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {visibleEntries} from '../src/lib/files-browser.ts'
const component=readFileSync(new URL('../src/components/FilesScreen.tsx',import.meta.url),'utf8')
const css=readFileSync(new URL('../src/files.css',import.meta.url),'utf8')
assert.ok(component.includes("visibleEntries(all(),currentDir(),query(),'all')"))
assert.ok(!component.includes('setScope') && !component.includes('scope()'))
const search=component.match(/<div class="fb-search">(.*?)<\/div>/s)[1]
assert.ok(!search.includes('<select'))
assert.ok(search.includes('placeholder="在全部文件中搜索"'))
assert.match(css,/\.fb-search input\s*\{[^}]*flex:1;[^}]*border-radius:999px;/)
assert.ok(!css.includes('.fb-search select'))
const entries=[{path:'甲',isDir:true},{path:'乙',isDir:true},{path:'甲/读书.md',isDir:false},{path:'乙/读书.md',isDir:false},{path:'读书.md',isDir:false},{path:'乙/读书文件夹',isDir:true}]
for(const dir of ['', '甲', '乙']){
 const results=visibleEntries(entries,dir,'读书','all').map(e=>e.path)
 assert.equal(results.length,4)
 assert.ok(results.includes('甲/读书.md') && results.includes('乙/读书.md') && results.includes('读书.md') && results.includes('乙/读书文件夹'))
 assert.ok(visibleEntries(entries,dir,'','all').every(e=>e.path.split('/').slice(0,-1).join('/')===dir))
 assert.deepEqual(visibleEntries(entries,dir,'   ','all'),visibleEntries(entries,dir,'','all'))
}
console.log('PASS full-width global search UI, cross-directory results, current-folder browsing and blank query')
