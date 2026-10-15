import assert from 'node:assert/strict'
import { buildTree } from '../src/lib/tree.ts'
import { remapPath, validMove, filterTree } from '../src/lib/files-state.ts'
assert.equal(remapPath('甲/子/文.md','甲','乙'),'乙/子/文.md')
assert.equal(remapPath('甲乙/文.md','甲','乙'),'甲乙/文.md')
assert.equal(remapPath(null,'甲','乙'),null)
const tree=buildTree([{path:'甲/文.md',title:'文'},{path:'乙/文.md',title:'文'}],['空/深'])
assert.equal(tree.find(n=>n.path==='空').dirs[0].path,'空/深')
assert.equal(filterTree(tree,'甲/文')[0].files[0].path,'甲/文.md')
assert.equal(filterTree(tree,'空/深')[0].dirs[0].path,'空/深')
assert.equal(validMove('甲',true,'甲/子'),false)
assert.equal(validMove('甲/文.md',false,'甲'),false)
assert.equal(validMove('甲/文.md',false,''),true)
console.log('PASS: prefix boundary, null, empty ancestry, path search, descendant/same-parent/root moves')
