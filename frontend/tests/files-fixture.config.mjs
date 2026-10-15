import {defineConfig} from 'vite'
import solid from 'vite-plugin-solid'
import {fileURLToPath} from 'node:url'
import {appSourceFixture} from './app-source-fixture.mjs'
// Opt-in diagnostic transform only for this disposable browser fixture.
// Production builds do not load this config or emit diagnostic events.
function commandDiagnostics() {
  return {name:'files-command-diagnostics',enforce:'pre',transform(code,id) {
    if(!id.split('?')[0].endsWith('/components/FilesScreen.tsx'))return
    const needle="    if (busy() || !target.path) return"
    if(!code.includes(needle))throw Error('Files command diagnostic extraction changed')
    return code.replace(needle,`    window.dispatchEvent(new CustomEvent('fixture-files-command', {detail: {
      path:target.path, action, busy:busy(), loading:loading(), rename:renameBusy(),
      move:moveBusy(), deletion:deleteBusy(), upload:uploading(), undo:undoBusy(),
      operation:operations.isBusy(), pending:operations.hasPending()
    }}));
${needle}`)
  }}
}
export default defineConfig({root:fileURLToPath(new URL('.',import.meta.url)),plugins:[...(process.env.FILES_COMMAND_DIAGNOSTICS==='1'?[commandDiagnostics()]:[]),solid(),appSourceFixture()],resolve:{conditions:['browser','module','production'],dedupe:['solid-js'],alias:[{find:/^solid-js$/,replacement:fileURLToPath(new URL('../node_modules/solid-js/dist/solid.js',import.meta.url))},{find:/^solid-js\/web$/,replacement:fileURLToPath(new URL('../node_modules/solid-js/web/dist/web.js',import.meta.url))}]},build:{rollupOptions:{input:fileURLToPath(new URL('./files-fixture.html',import.meta.url))}}})
