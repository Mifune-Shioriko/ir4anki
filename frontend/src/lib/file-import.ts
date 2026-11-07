/** Picker filtering is convenience only; the backend still validates every path. */
export interface SkippedImport { rel: string; reason: string }

export function partitionImports<T extends { file: { name: string }; rel: string }>(
  entries: readonly T[],
  documents = false,
): { supported: T[]; skipped: SkippedImport[] } {
  const supported: T[] = []
  const skipped: SkippedImport[] = []
  const protectedDirs = new Set(['.git', '.obsidian', '.trash', 'node_modules'])
  for (const entry of entries) {
    const parts = entry.rel.replace(/\\/g, '/').split('/')
    const extension = /\.([^.]+)$/.exec(entry.file.name)?.[1].toLowerCase() ?? ''
    if (parts.slice(0, -1).some(part => protectedDirs.has(part))) {
      skipped.push({ rel: entry.rel, reason: '系统或配置目录，不导入' })
    } else if (/^(md|png|jpg|jpeg|gif|webp|svg|bmp|avif)$/.test(extension)
      || (documents && /^(pdf|ppt|pptx)$/.test(extension))) {
      supported.push(entry)
    } else {
      skipped.push({ rel: entry.rel, reason: '不支持的文件类型，不导入' })
    }
  }
  return { supported, skipped }
}
