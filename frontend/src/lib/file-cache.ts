import { renderMarkdown } from './markdown'
const RENDER_VERSION = 'source-lines-katex-v1'
type Document = { text: string; html: string; revision?: string }
type Fetcher = (path: string, signal?: AbortSignal) => Promise<{ text: string; revision?: string }>
const cache = new Map<string, Document>()
const pending = new Map<string, { controller: AbortController; refs: number; promise: Promise<Document> }>()
const listeners = new Set<(path?: string) => void>()
let bytes = 0
const size = (d: Document) => 2 * (d.text.length + d.html.length)
const keyFor = (path: string, revision?: string) => JSON.stringify([path, revision ?? '', RENDER_VERSION])
export function invalidateDocuments(path?: string) {
  for (const [key, d] of cache) {
    const p = JSON.parse(key)[0] as string
    if (!path || p === path || p.startsWith(path + '/')) { cache.delete(key); bytes -= size(d) }
  }
  for (const [key, request] of pending) {
    const p = JSON.parse(key)[0] as string
    if (!path || p === path || p.startsWith(path + '/')) { request.controller.abort(); pending.delete(key) }
  }
  listeners.forEach(fn => fn(path))
}
export function observeDocuments(fn: (path?: string) => void) { listeners.add(fn); return () => { listeners.delete(fn) } }
export function acquireDocument(path: string, revision: string | undefined, fetcher: Fetcher) {
  const key = keyFor(path, revision)
  const hit = cache.get(key)
  if (hit) { cache.delete(key); cache.set(key, hit); return { promise: Promise.resolve(hit), release() {} } }
  let request = pending.get(key)
  if (!request) {
    const controller = new AbortController()
    request = { controller, refs: 0, promise: Promise.resolve({text:'',html:''}) }
    const current = request
    current.promise = fetcher(path, controller.signal).then(data => {
      if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError')
      const start = performance.now()
      const doc = { ...data, html: renderMarkdown(data.text) }
      performance.measure('files-markdown-render', { start, end: performance.now() })
      if (size(doc) <= 8 * 1024 * 1024) {
        cache.set(key, doc); bytes += size(doc)
        while (cache.size > 24 || bytes > 8 * 1024 * 1024) {
          const first = cache.keys().next().value!
          bytes -= size(cache.get(first)!); cache.delete(first)
        }
      }
      return doc
    }).finally(() => { if (pending.get(key) === current) pending.delete(key) })
    pending.set(key, current)
  }
  request.refs++
  let released = false
  return { promise: request.promise, release() {
    if (released) return
    released = true
    request!.refs--
    queueMicrotask(() => {
      if (!request!.refs && pending.get(key) === request) { pending.delete(key); request!.controller.abort() }
    })
  } }
}
