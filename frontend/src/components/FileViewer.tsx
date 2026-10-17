import { Component, createEffect, createSignal, onCleanup } from 'solid-js'
import { acquireDocument, invalidateDocuments, observeDocuments } from '../lib/file-cache'
import { sourceScroll, type SourceScrollSession } from '../lib/source-scroll'

// Shared whole-file markdown viewer with source-line anchoring.
// Extracted from NotePanel (2026-09-19, 渐进制卡) so the card→note panel and
// the reading right column (whole-file context behind the current chunk)
// render + anchor + flash IDENTICALLY.
//
// Rendering: lib/markdown.ts (markdown-it + KaTeX, html:false) — headings
// carry [data-src-line] (1-based source line), so the anchor is exact for
// section starts; split parts / preamble fall back to the closest heading
// at or before anchorLine (findAnchor), then to the top.
//
// Flash: the anchor heading + following siblings up to the next
// [data-src-line] get .note-hl (2.8s primary fade) — same idiom as the
// original NotePanel.
//
// The component root is the panel scroll container (.note-body). A content
// wrapper lets its source alignment session observe asynchronous layout changes. File texts are cached module-wide (same cache NotePanel
// used) so tab/panel switches never re-fetch.

export const invalidateFileCache = invalidateDocuments

interface Props {
  /** corpus-relative path; null renders nothing */
  path: string | null
  /** 1-based source line to scroll+flash; null = no anchoring */
  anchorLine: number | null
  /** optional 1-based END line (inclusive): flash every block whose
   *  [data-src-line] falls inside [anchorLine, anchorEndLine] instead of
   *  just the anchor heading's section. Exact-provenance panel (round 4)
   *  passes the segment's full range; heading-only callers omit it. */
  anchorEndLine?: number | null
  /** bump for a new source target at the same path+line */
  anchorToken?: unknown
  /** Whether the containing note pane is selected; does not renew scroll ownership. */
  active?: boolean
  /** bump to RE-FETCH the file text for the same path (in-app segment
   *  edit rewrote the .md — the module cache alone would serve stale
   *  text; caller pairs this with invalidateFileCache). */
  reloadToken?: unknown
  /** fetcher: NotePanel → /api/reading/file (corpus-direct),
   *  reading panel → /api/reading/file */
  fetchFile: (path: string, signal?: AbortSignal) => Promise<{ text: string; revision?: string }>
  revision?: string
  class?: string
  onError?: () => void
  onReady?: (path: string) => void
}

function findAnchor(root: HTMLElement, line: number): HTMLElement | null {
  const els = Array.from(root.querySelectorAll<HTMLElement>('[data-src-line]'))
  let best: HTMLElement | null = null
  let bestLine = -1
  for (const el of els) {
    const l = parseInt(el.dataset.srcLine || '0', 10)
    if (l <= line && l > bestLine) {
      best = el
      bestLine = l
    }
  }
  if (!best) return null
  // Climb to the TOP-LEVEL tagged block (2026-09-21: every block now carries
  // data-src-line, not just headings — a nested li/td anchor would dead-end
  // the sibling walk in range mode and scroll inside its list instead of to
  // the section). The parent's data-src-line is always <= the child's, so
  // the <= line contract holds.
  let top = best
  while (top.parentElement && top.parentElement !== root) {
    const p = top.parentElement.closest<HTMLElement>('[data-src-line]')
    if (!p || p === root) break
    top = p
  }
  return top
}

export const FileViewer: Component<Props> = (props) => {
  const [html, setHtml] = createSignal('')
  const [loading, setLoading] = createSignal(false)
  const [error, setError] = createSignal('')
  const [retry, setRetry] = createSignal(0)
  const [invalidated, setInvalidated] = createSignal(0)
  let release: (() => void) | undefined
  let alive = true
  let rootRef: HTMLDivElement | undefined
  let contentRef: HTMLDivElement | undefined
  let scrollSession: SourceScrollSession | undefined
  // monotonic request token: a stale fetch (path swapped mid-flight) can
  // never overwrite the newer file's state
  let reqToken = 0

  const load = () => {
    release?.()
    const path = props.path
    const token = ++reqToken
    scrollSession?.dispose()
    scrollSession = undefined
    if (!path) {
      setError('')
      setHtml('')
      setLoading(false)
      return
    }
    setHtml('')
    setError('')
    setLoading(true)
    const request = acquireDocument(path, props.revision, props.fetchFile)
    release = request.release
    request.promise.then(d => {
      if (!alive || token !== reqToken) return
      const start = performance.now()
      setHtml(d.html)
      setLoading(false)
      requestAnimationFrame(() => {
        if (!alive || token !== reqToken) return
        // Include the DOM commit + one layout opportunity, not a live latency promise.
        rootRef?.getBoundingClientRect()
        performance.measure('files-dom-layout', { start, end: performance.now() })
        props.onReady?.(path)
      })
    }).catch(e => {
      if (!alive || token !== reqToken || e?.name === 'AbortError') return
      setLoading(false)
      setError(e?.message || '读取失败')
      props.onError?.()
    }).finally(request.release)
  }

  createEffect(() => {
    props.path // track
    props.revision
    retry()
    invalidated()
    props.reloadToken // track: in-app edit bumped it → re-fetch fresh text
    load()
  })

  // Target ownership is independent of tab visibility: only a new target or
  // rendered document starts a session. Cleanup invalidates queued layout work.
  createEffect(() => {
    const h = html()
    const line = props.anchorLine
    const endLine = props.anchorEndLine ?? null
    props.anchorToken
    if (!h || line == null) return
    const generation = reqToken
    let session: SourceScrollSession | undefined
    const frame = requestAnimationFrame(() => {
      const root = rootRef
      if (!alive || generation !== reqToken || !root || !contentRef) return
      const anchor = findAnchor(root, line)
      if (!anchor) return
      const els: HTMLElement[] = [anchor]
      let n = anchor.nextElementSibling as HTMLElement | null
      while (n) {
        if (n.hasAttribute('data-src-line')) {
          if (endLine == null || parseInt(n.dataset.srcLine || '0', 10) > endLine) break
        }
        els.push(n)
        n = n.nextElementSibling as HTMLElement | null
      }
      session = sourceScroll(root, contentRef, anchor, els, props.active ?? true)
      scrollSession = session
    })
    onCleanup(() => {
      cancelAnimationFrame(frame)
      session?.dispose()
      if (scrollSession === session) scrollSession = undefined
    })
  })
  createEffect(() => {
    const active = props.active ?? true
    scrollSession?.setVisible(active)
  })

  const unsubscribe = observeDocuments(path => {
    if (!path || props.path === path || props.path?.startsWith(path + '/')) setInvalidated(n => n + 1)
  })
  onCleanup(() => {
    alive = false
    reqToken++
    release?.()
    unsubscribe()
    scrollSession?.dispose()
  })

  return (
    <>
      {loading() && (
        <div class="file-viewer-loading">
          <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
        </div>
      )}
      {error() && <div class="file-viewer-error" role="alert">
        无法读取 {props.path}：{error()}
        <md-text-button onClick={() => setRetry(n => n + 1)}>重试</md-text-button>
      </div>}
      <div aria-busy={loading()} data-file-path={props.path ?? ''} class={props.class ?? ''} ref={rootRef}><div ref={contentRef} innerHTML={html()} /></div>
    </>
  )
}
