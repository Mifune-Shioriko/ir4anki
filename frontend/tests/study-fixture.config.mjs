import {defineConfig} from 'vite'
import solid from 'vite-plugin-solid'
import {fileURLToPath} from 'node:url'
export default defineConfig({root:fileURLToPath(new URL('.',import.meta.url)),plugins:[solid()],resolve:{conditions:['browser','module','production'],dedupe:['solid-js'],alias:[{find:/^solid-js$/,replacement:fileURLToPath(new URL('../node_modules/solid-js/dist/solid.js',import.meta.url))},{find:/^solid-js\/web$/,replacement:fileURLToPath(new URL('../node_modules/solid-js/web/dist/web.js',import.meta.url))}]},build:{rollupOptions:{input:fileURLToPath(new URL('./study-fixture.html',import.meta.url))}}})
