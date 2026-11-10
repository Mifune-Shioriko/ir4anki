import {resolve as baseResolve,load as baseLoad} from './solid-lifecycle-loader.mjs'
export async function load(url,c,next){if(url.endsWith('.css'))return {format:'module',shortCircuit:true,source:''};return baseLoad(url,c,next)}
export async function resolve(s,c,next){
 // Only browser PDF/MD renderer leaves are replaced. Actual panels, adapter,
 // Solid control flow, API consumers, source getters and teardown run unchanged.
 if(c.parentURL?.endsWith('/components/SourceViewer.tsx')&&['./PdfReferenceViewer','./FileViewer'].includes(s))return {url:new URL('./source-leaf-probe.mjs',import.meta.url).href,shortCircuit:true}
 return baseResolve(s,c,next)
}
