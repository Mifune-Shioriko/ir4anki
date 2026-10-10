export async function resolve(specifier, context, next) {
 try { return await next(specifier,context) } catch(e) {
 if(specifier.startsWith('.')) return next(specifier+'.ts',context)
 throw e
 }
}

// Node 20 also runs the offline tests; no native type-stripping dependency.
export async function load(url, context, next) {
 if (!url.endsWith('.ts')) return next(url, context)
 const { readFile } = await import('node:fs/promises')
 const ts = await import('typescript')
 const source = await readFile(new URL(url), 'utf8')
 return {format:'module',shortCircuit:true,source:ts.default.transpileModule(source,{compilerOptions:{target:ts.default.ScriptTarget.ES2022,module:ts.default.ModuleKind.ESNext}}).outputText}
}
