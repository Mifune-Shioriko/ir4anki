import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const css=readFileSync(new URL('../src/index.css',import.meta.url),'utf8')
const nav=readFileSync(new URL('../src/components/NavRail.tsx',import.meta.url),'utf8')
test('progress footer stays in left rail flow, stacked and bottom aligned',()=>{
 const rules=[...css.matchAll(/\.nav-rail__footer\s*\{([^}]+)\}/g)].map(m=>m[1]).join('\n')
 assert.doesNotMatch(rules,/position:\s*fixed|\bright:|\bbottom:/)
 assert.match(rules,/margin-top:\s*auto/)
 assert.match(rules,/flex-direction:\s*column/)
 assert.match(css,/\.nav-rail\s*\{[^}]*overflow-y:\s*auto/s)
 const footer=nav.slice(nav.indexOf('<div class="nav-rail__footer">'))
 assert.ok(footer.indexOf('kind="reading"')<footer.indexOf('kind="review"'))
 assert.equal((nav.match(/<ProgressRing /g)||[]).length,2)
})
test('sidebar reading label retains dynamic count and navigation key',()=>{
 assert.match(nav,/key: 'readingList', label: '阅读'/)
 assert.match(nav,/` \(\$\{props.readingListSize\}\)`/)
 assert.match(nav,/onClick=\{\(\) => props.onNavigate\(item.key\)\}/)
})
