import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'
import {readdirSync,readFileSync} from 'node:fs'
import {dirname,join} from 'node:path'
import {createRequire} from 'node:module'
const require=createRequire(import.meta.url)
const pdfAssets=()=>({name:'local-pdf-assets',generateBundle(this: {emitFile:(v:unknown)=>void}){
 const root=dirname(require.resolve('pdfjs-dist/package.json'))
 for(const folder of ['cmaps','standard_fonts','wasm'])for(const name of readdirSync(join(root,folder))){
  if(name.endsWith('.map'))continue
  this.emitFile({type:'asset',fileName:`assets/pdfjs/${folder}/${name}`,source:readFileSync(join(root,folder,name))})
 }
}})

export default defineConfig({
  plugins: [solid(),pdfAssets()],
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8901',
      '/media': 'http://localhost:8901',
    },
  },
})
