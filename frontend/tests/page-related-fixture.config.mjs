import {defineConfig} from 'vite'
import solid from 'vite-plugin-solid'
import {resolve} from 'node:path'
import {readFileSync} from 'node:fs'
export default defineConfig({base:'/__probe__/',plugins:[solid()],resolve:{dedupe:Object.keys(JSON.parse(readFileSync('package.json','utf8')).dependencies),alias:{'@page-related-panel':resolve(process.env.PAGE_COMPONENT_SOURCE,'components/RelatedPanel.tsx')}},build:{rollupOptions:{input:resolve('tests/page-related-fixture.html')}}})
