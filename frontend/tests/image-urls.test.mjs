import assert from 'node:assert/strict'
import { renderMarkdown } from '../src/lib/markdown.ts'
export function imageSources(source) {
 return [...renderMarkdown(source).matchAll(/<img[^>]* src="([^"]*)"/g)].map(m => m[1].replaceAll('&amp;', '&'))
}
if (process.argv.includes('--render')) {
 let input='';for await (const chunk of process.stdin) input+=chunk
 process.stdout.write(JSON.stringify(JSON.parse(input).map(imageSources)))
} else {
 const cases = [
 ['a-x%20%281%29.png', '/api/reading/media/a-x%20(1).png'],
 ['dir/中文%20图.png', '/api/reading/media/%E4%B8%AD%E6%96%87%20%E5%9B%BE.png'],
 ['percent%25.png', '/api/reading/media/percent%25.png'],
 ['encoded%3F%23.png?size=2&v=%25#part', '/api/reading/media/encoded%3F%23.png?size=2&v=%25#part'],
 ['normal.png?size=2#part', '/api/reading/media/normal.png?size=2#part'],
 ['bad%ZZ.png', '/api/reading/media/bad%25ZZ.png'],
 ['bad%.png', '/api/reading/media/bad%25.png'],
 ['bad%FF.png', '/api/reading/media/bad%25FF.png'],
 ['a%2Fb.png', 'a%2Fb.png'],
 ['a%5Cb.png', 'a%5Cb.png'],
 ['https://example.org/a%20b.png?size=2#part', 'https://example.org/a%20b.png?size=2#part'],
 ['//example.org/a.png', '//example.org/a.png'],
 ['data:image/png;base64,aGVsbG8=', 'data:image/png;base64,aGVsbG8='],
 ]
 for (const [src, expected] of cases) assert.deepEqual(imageSources(`![image](<${src}>)`), [expected], src)
 assert(!renderMarkdown('![](javascript:alert(1))').includes('<img'))
 console.log('PASS actual renderMarkdown path encoding once, suffixes, Unicode/percent/malformed paths, separator safety, ordinary/external/data URLs')
}
