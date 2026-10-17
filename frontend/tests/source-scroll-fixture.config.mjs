import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import solid from 'vite-plugin-solid'

const tests = fileURLToPath(new URL('.', import.meta.url))
const frontend = path.resolve(tests, '..')
export function sourceScrollFixture(outDir, dataFile) {
  const data = JSON.parse(readFileSync(dataFile, 'utf8'))
  if (typeof data.text !== 'string' || !Array.isArray(data.segments)) throw Error('Expected {text,segments} fixture data')
  return {
    root: tests, configFile: false, cacheDir: path.join(path.dirname(outDir), 'vite-cache'),
    plugins: [solid(), {
      name: 'source-scroll-fixture-data',
      resolveId(id) { if (id === 'virtual:source-scroll-data') return '\0source-scroll-data' },
      load(id) { if (id === '\0source-scroll-data') return 'export default ' + JSON.stringify(data) },
    }],
    resolve: { conditions: ['browser','module','production'], dedupe: ['solid-js'], alias: [
      { find: /^solid-js$/, replacement: frontend + '/node_modules/solid-js/dist/solid.js' },
      { find: /^solid-js\/web$/, replacement: frontend + '/node_modules/solid-js/web/dist/web.js' },
    ] },
    build: { outDir, emptyOutDir: true, rollupOptions: { input: tests + '/source-scroll-fixture.html' } },
  }
}
