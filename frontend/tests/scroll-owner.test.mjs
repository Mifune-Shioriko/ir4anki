// Supplemental structure guard. Actual wheel/trace/return is acceptance-enforced in candidate_browser.mjs.
import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {containmentProblems} from './study-containment-browser.mjs'
const read=n=>readFileSync(new URL('../src/'+n,import.meta.url),'utf8')
test('PDF.js measurement canvas cannot create a body line box',()=>{
 assert.match(read('study.css'),/body\s*>\s*canvas\.hiddenCanvasElement\s*\{[^}]*display:\s*none/)
})
test('review explicitly registers its own scroll element and restores only the same revealed card',()=>{
 assert.match(read('components/StudyCardShell.tsx'),/onScrollElement/)
 assert.match(read('components/Flashcard.tsx'),/onScrollElement=\{props.onScrollElement\}/)
 const app=read('App.tsx')
 assert.match(app,/scroll:reviewScroll\?\.scrollTop/)
 assert.match(app,/reviewScroll\?\.isConnected/)
 assert.doesNotMatch(app,/querySelector\('\.content'\)\?\.scroll/)
})

test('parent v2 extra 22px remains an acceptance failure',()=>{
 assert(containmentProblems({viewportHeight:1000,documentHeight:1022,outerScrollY:0}).includes('outer document exceeds viewport'))
})
