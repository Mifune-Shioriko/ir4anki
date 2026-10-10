import assert from 'node:assert/strict'
import { renderClozeMd } from '../src/lib/cloze.ts'
const q = renderClozeMd('**{{c1::SECRET::hint}}** [{{c2::other}}](https://example.org)', 'q', 1)
assert(!q.includes('SECRET'))
assert(q.includes('other'), 'other ordinals must remain visible')
assert(q.includes('<strong>'))
const a = renderClozeMd('{{c1::$x_{2}$}}\n\n| A | B |\n|---|---|\n| x | y |\n\n- ![](image.png)\n\n```js\ncode\n```','a',1)
for (const token of ['katex','<table','<li','<img','<pre']) assert(a.includes(token), token)
console.log('Markdown ordinal and rich content tests passed')
const { clozeRanges, isMarkdownNote, MARKDOWN_TAG, renderField } = await import('../src/lib/cloze.ts')
assert.equal(clozeRanges('{{c1::$C_{5,6}$::hint}}')[0].content, '$C_{5,6}$')
assert.equal(clozeRanges('{{c1::outer {{c2::nested}} end}}')[0].content, 'outer {{c2::nested}} end')
assert(isMarkdownNote(['user-tag',MARKDOWN_TAG]))
const mixed = renderField('**bold** <br> <img src=x onerror=alert(1)>', [MARKDOWN_TAG])
assert(mixed.includes('<strong>bold</strong>'))
assert(!mixed.includes('<img src=x'))
for (const source of [
 '[label](https://example.org/{{c1::SECRET}})',
 '![]({{c1::SECRET}})',
 '```\n{{c1::SECRET}}\n```',
 '<img title="{{c1::SECRET}}">',
 '{{c1::outer {{c2::SECRET}}}}',
 '*{{c1::SECRET::**hint**}}*',
]) assert(!renderClozeMd(source, 'q',1).includes('SECRET'), source)
assert(renderClozeMd('{{c1::SECRET::**hint**}}','q',1).includes('<strong>hint</strong>'))
assert(renderClozeMd('{{c1::**answer**}}','a',1).includes('<strong>answer</strong>'))
assert(!renderClozeMd('{{c1::secret::<img src=x onerror=alert(1)>}}','q',1).includes('<img src=x'))
assert(renderClozeMd('$C_{5,6}$ {{c1::$x_{2}$}}','a',1).includes('katex'))
console.log('PASS nested/TeX braces, hidden URL/alt/code/HTML, hints, explicit routing, XSS exclusion')

for (const mode of ['q','a']) {
 const html=renderClozeMd('**{{c3::answer::hint}}** {{c7::visible}}',mode,3)
 assert(html.includes('<span class="cloze">'), 'active cloze must retain visible styling')
 assert(html.includes('visible'))
 assert(mode==='a' || !html.includes('answer'))
}
const collision=renderClozeMd('IR4ANKICLOZEMARKOPEN {{c5::value}}','a',5)
assert(collision.includes('IR4ANKICLOZEMARKOPEN'))
assert(collision.includes('<span class="cloze">value</span>'))
console.log('PASS front/back cloze styling, non-contiguous ordinals and marker collision')

assert(renderClozeMd('{{c1::outer {{c3::target}}}}','q',3).includes('<span class="cloze">[…]</span>'))
console.log('PASS nested active ordinal retains visible styling')

for (const [source, tag] of [
 ['{{c3::# heading}}','<h1'],
 ['{{c3::# heading\n\n- **one**\n- two}}','<h1'],
 ['{{c3::```js\nconst x = 1\n```}}','<pre'],
 ['{{c3::| A | B |\n|---|---|\n| one | two |}}','<table'],
]) assert(renderClozeMd(source,'a',3).includes(tag), 'cloze must preserve Markdown block '+tag)
console.log('PASS cloze heading/list/fence/table block preservation')
