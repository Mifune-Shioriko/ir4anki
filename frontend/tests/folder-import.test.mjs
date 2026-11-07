import assert from 'node:assert/strict'
import { partitionImports } from '../src/lib/file-import.ts'

const entry = rel => ({ file: { name: rel.split('/').at(-1) }, rel })
const entries = [
  '金钱心理学/001-正文.md',
  '金钱心理学/_assets/a.png',
  '金钱心理学/章节/_assets/b.PNG',
  '金钱心理学/_assets/manifest.json',
  '金钱心理学/.DS_Store',
  '金钱心理学/.git/README.md',
  '金钱心理学/.obsidian/a.png',
  '金钱心理学/node_modules/example.md',
  '金钱心理学/课件.PDF',
  '金钱心理学/讲义.ppt',
  '金钱心理学/讲义.pptx',
].map(entry)
const result = partitionImports(entries, true)
assert.equal(result.supported.length, 6)
assert.equal(result.skipped.length, 5)
assert.equal(result.supported[1], entries[1], 'keep original File and relative path')
assert.ok(result.skipped.find(e => e.rel.endsWith('manifest.json')))
assert.equal(partitionImports(entries).supported.length, 3, 'legacy Markdown build must not send lecture formats')
assert.deepEqual(partitionImports([]), { supported: [], skipped: [] })
assert.equal(partitionImports([entry('manifest.json')], true).supported.length, 0)
assert.equal(partitionImports([entry('md'), entry('png')]).supported.length, 0, 'extensions need a dot')
assert.equal(partitionImports([{ file: { name: 'a.md' }, rel: 'book\\.git\\a.md' }]).supported.length, 0)
assert.deepEqual(entries[1], entry('金钱心理学/_assets/a.png'), 'do not rename the original asset folder')
console.log('folder import: mixed formats, nested assets, junk filtering and path preservation passed')
