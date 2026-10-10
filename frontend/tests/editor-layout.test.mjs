import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const css=readFileSync(process.env.EDITOR_LAYOUT_CSS || new URL('../src/index.css',import.meta.url),'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const rules = selector => [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[1].trim()===selector).map(m=>m[2]).join(';')
test('empty, first typing and cleared preview have the same reserved geometry',()=>{
 for(const selector of ['.edit-dialog .card-md-preview','.edit-dialog .cloze-preview-box']) {
  const r=rules(selector)
  assert.match(r,/\bheight:\s*96px/,`${selector} must reserve a fixed preview slot`)
  assert.match(r,/\bmin-height:\s*96px/,`${selector} must not collapse below the reserved slot`)
  assert.match(r,/\bflex:\s*none/,`${selector} must override note-body's content-dependent flex basis`)
  assert.match(r,/overflow(?:-y)?:\s*auto/)
 }
 const ordinals=rules('.edit-dialog .cloze-ordinals')
 for(const property of ['height','min-height','max-height']) assert.match(ordinals,new RegExp(`${property}:\\s*40px`))
 assert.match(ordinals,/flex:\s*none/)
 assert.match(ordinals,/flex-wrap:\s*nowrap/)
})
test('short source editors stay compact and scroll',()=>{
 for(const selector of ['.edit-dialog .card-md-editor','.edit-dialog .rich-field']) assert.match(rules(selector),/height:\s*clamp\(88px, 12vh, 120px\)/)
 assert.match(rules('.edit-dialog .rich-field'),/min-height:\s*88px/)
 assert.match(css,/\.edit-fields \{ grid-template-columns: minmax\(0, 1fr\); \}/)
})
test('previews are mounted unconditionally in Markdown and legacy paths',()=>{
 const md=readFileSync(new URL('../src/components/MarkdownField.tsx',import.meta.url),'utf8')
 const edit=readFileSync(new URL('../src/components/EditDialog.tsx',import.meta.url),'utf8')
 assert.match(md,/<div class="note-body card-md-preview"/)
 assert.match(edit,/<div class="note-body card-md-preview"/)
 assert.doesNotMatch(md,/<Show when=\{text\(/)
})

test('authoring uses grouped SVG icon actions and labelled outlined preview regions',()=>{
 const md=readFileSync(new URL('../src/components/MarkdownField.tsx',import.meta.url),'utf8')
 const toolbar=readFileSync(new URL('../src/components/AuthoringToolbar.tsx',import.meta.url),'utf8')
 assert.match(toolbar,/role="group" aria-label="文字格式"/)
 assert.match(toolbar,/role="group" aria-label="更多格式"/)
 assert.match(toolbar,/<md-menu/)
 for(const label of ['加粗','斜体','插入公式','插入图片','插入表格']) assert.ok(md.includes(`label: '${label}'`))
 assert.match(rules('.edit-dialog .edit-toolbar md-icon-button'),/width:\s*48px/)
 assert.match(rules('.edit-dialog .edit-toolbar md-icon-button'),/height:\s*48px/)
 assert.doesNotMatch(rules('.edit-dialog .edit-toolbar'),/overflow-x:\s*auto/)
 assert.match(css,/@container authoring-tools \(max-width: 399px\)/)
 assert.match(rules('.edit-dialog .authoring-secondary'),/display:\s*none/)
 assert.match(toolbar,/captureSelection/)
 assert.match(toolbar,/restoreSelection/)
 assert.match(toolbar,/positioning="popover"/)
 assert.doesNotMatch(md,/<md-text-button/)
 assert.match(md,/IconFormatBold/)
 assert.match(md,/role="region" aria-label=/)
 assert.match(md,/预览<\/div>/)
 assert.match(rules('.edit-dialog .authoring-preview'),/border:\s*1px solid var\(--md-sys-color-outline\)/)
 assert.match(rules('.edit-dialog .markdown-field'),/gap:\s*8px/)
})

test('overflow selects once for click/Enter/Space and only dismisses on Escape',()=>{
 const toolbar=readFileSync(new URL('../src/components/AuthoringToolbar.tsx',import.meta.url),'utf8')
 const body=toolbar.match(/on:close-menu=\{\(event: CloseMenuEvent\) => \{([\s\S]*?)\n          \}\}>/)?.[1]
 assert.ok(body,'use the installed Material selection event')
 const handle=new Function('event','action','CloseReason','runFromMenu',body)
 for(const [reason,expected] of [
  [{kind:'click-selection'},1], [{kind:'keydown',key:'Enter'},1],
  [{kind:'keydown',key:'Space'},1], [{kind:'keydown',key:'Escape'},0],
  [{kind:'keydown',key:'ArrowDown'},0],
 ]) {
  let executed=0,stopped=0
  const action={label:'format'}
  handle({detail:{reason},stopPropagation(){stopped++}},action,
   {CLICK_SELECTION:'click-selection',KEYDOWN:'keydown'},actual=>{assert.equal(actual,action);executed++})
  assert.equal(executed,expected)
  assert.equal(stopped,expected)
 }
})
