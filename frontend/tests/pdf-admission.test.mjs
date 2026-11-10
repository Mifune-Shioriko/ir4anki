import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const read=f=>readFileSync(process.env.UI_SOURCE?process.env.UI_SOURCE+'/'+f:new URL('../src/'+f,import.meta.url),'utf8')
test('PDF picker dispatches stable revision admission and excludes Markdown reseed',()=>{
 const s=read('components/ReadingListScreen.tsx');assert.match(s,/document_id/);assert.match(s,/revision_id/);assert.match(s,/lectureRequest/);assert.match(s,/kind\s*!==?\s*['"]pdf/)
})
test('first import is outside the responsive preview card',()=>{
 const s=read('components/FilesScreen.tsx');assert(s.indexOf('<LectureImport')>=0&&s.indexOf('<LectureImport')<s.indexOf('<div class="files-screen"'));assert.match(read('components/LectureImport.tsx'),/尚未加入阅读清单/)
})
