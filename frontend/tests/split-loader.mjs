import {readFile} from 'node:fs/promises'
import ts from 'typescript'
import {transformAsync} from '@babel/core'
import solid from 'babel-preset-solid'
// Universal host has no browser custom-element registry; Material side-effect registration is host-only.
export async function resolve(s,c,next){if(process.env.SPLIT_UNIT_PDF==='1'&&/\/(PdfSourceViewer|OcclusionEditor|LectureQuestionAnswer)$/.test(s)){const name=s.split('/').pop();return {url:'data:text/javascript,export function '+name+'(){return null}',shortCircuit:true}};if(s.startsWith('@material/web/'))return {url:'data:text/javascript,export const CloseReason={CLICK_SELECTION:1,KEYDOWN:2}',shortCircuit:true};try{return await next(s,c)}catch(e){if(s.startsWith('.'))for(const ext of ['.ts','.tsx'])try{return await next(s+ext,c)}catch{};throw e}}
export async function load(url,c,next){
 if(!/\.tsx?$/.test(url))return next(url,c)
 let source=ts.transpileModule(await readFile(new URL(url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText
 if(url.endsWith('.tsx'))source=(await transformAsync(source,{filename:url,babelrc:false,configFile:false,presets:[[solid,{generate:'universal',moduleName:new URL('./split-renderer.mjs',import.meta.url).href}]]})).code
 return {format:'module',shortCircuit:true,source}
}
