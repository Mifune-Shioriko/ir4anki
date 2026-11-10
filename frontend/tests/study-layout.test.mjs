import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const read=f=>readFileSync(process.env.UI_SOURCE?process.env.UI_SOURCE+'/'+f:new URL('../src/'+f,import.meta.url),'utf8')
test('study dock is a layout row following the independently scrollable content',()=>{
 const s=read('components/StudyCardShell.tsx');assert.match(s,/class="study-scroll"/);assert(s.indexOf('class="study-scroll"')<s.indexOf('{props.dock}'))
 const css=read('study.css');assert.match(css,/grid-template-rows:\s*auto minmax\(0,\s*1fr\) auto/)
})
test('full PDF reference uses continuous viewer without a page-turn toolbar',()=>{
 assert.match(read('components/SourceViewer.tsx'),/PdfReferenceViewer/)
 const s=read('components/PdfReferenceViewer.tsx');assert.doesNotMatch(s,/下一页|上一页/);assert.match(s,/PageAnnotation/);assert.match(s,/ownPdfLoadingTask/)
})
test('duplicate lecture title removed and narrow reference works across formats',()=>{
 assert.doesNotMatch(read('components/LectureReadingCard.tsx'),/LECTURE \/ 阅读/)
 assert.doesNotMatch(read('App.tsx'),/when=\{activeChunk\(\)\?\.kind==='pdf'\}/)
})
