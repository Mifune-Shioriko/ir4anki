import { Component, createEffect, createSignal, onCleanup } from 'solid-js'
import { renderMarkdown } from '../lib/markdown'

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
// The component is a CONTENT div (not a scroll container): whichever
// ancestor scrolls (.note-body inside the flat side panels) receives the
// scrollIntoView. File texts are cached module-wide (same cache NotePanel
// used) so tab/panel switches never re-fetch.

const fileCache = new Map<string, string>()

/** Drop one file (or everything) from the module-wide text cache.
 *  Called after an in-app segment edit (/api/reading/edit rewrites the
 *  .md): without this the right column would keep rendering the stale
 *  pre-edit text. */
export function invalidateFileCache(path?: string) {
  if (path) fileCache.delete(path)
  else fileCache.clear()
}

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
  /** bump to re-fire the anchor effect for the same path+line (tab switch) */
  anchorToken?: unknown
  /** bump to RE-FETCH the file text for the same path (in-app segment
   *  edit rewrote the .md — the module cache alone would serve stale
   *  text; caller pairs this with invalidateFileCache). */
  reloadToken?: unknown
  /** fetcher: NotePanel → /api/reading/file (corpus-direct),
   *  reading panel → /api/reading/file */
  fetchFile: (path: string) => Promise<{ text: string }>
  class?: string
  onError?: () => void
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
  let rootRef: HTMLDivElement | undefined
  // monotonic request token: a stale fetch (path swapped mid-flight) can
  // never overwrite the newer file's state
  let reqToken = 0
  let flashTimer: ReturnType<typeof setTimeout> | null = null

  const load = () => {
    const path = props.path
    const token = ++reqToken
    if (flashTimer) {
      clearTimeout(flashTimer)
      flashTimer = null
    }
    if (!path) {
      setHtml('')
      setLoading(false)
      return
    }
    const cached = fileCache.get(path)
    if (cached != null) {
      setHtml(renderMarkdown(cached))
      setLoading(false)
      return
    }
    setLoading(true)
    props
      .fetchFile(path)
      .then(d => {
        if (token !== reqToken) return
        fileCache.set(path, d.text)
        setHtml(renderMarkdown(d.text))
        setLoading(false)
      })
      .catch(() => {
        if (token !== reqToken) return
        setLoading(false)
        setHtml('')
        props.onError?.()
      })
  }

  createEffect(() => {
    props.path // track
    props.reloadToken // track: in-app edit bumped it → re-fetch fresh text
    load()
  })

  // scroll + flash the anchored section after (re)render. Tracks html() and
  // anchorToken: the same file+line must re-flash on a tab/panel swap.
  createEffect(() => {
    const h = html()
    const line = props.anchorLine
    const endLine = props.anchorEndLine ?? null
    const tok = props.anchorToken
    void tok
    if (!h || line == null) return
    requestAnimationFrame(() => {
      const root = rootRef
      if (!root) return
      root.querySelectorAll('.note-hl').forEach(el => el.classList.remove('note-hl'))
      const anchor = findAnchor(root, line)
      if (!anchor) return
      anchor.scrollIntoView({ behavior: 'smooth', block: 'start' })
      const els: HTMLElement[] = []
      if (endLine == null) {
        // section mode: the anchor heading + following siblings up to the
        // next [data-src-line] (original NotePanel idiom)
        els.push(anchor)
        let n = anchor.nextElementSibling as HTMLElement | null
        while (n && !n.hasAttribute('data-src-line')) {
          els.push(n)
          n = n.nextElementSibling as HTMLElement | null
        }
      } else {
        // range mode (exact provenance, round 4): anchor heading + siblings
        // until a heading PAST endLine — only headings carry [data-src-line],
        // so body blocks belong to their preceding heading. A segment that
        // starts mid-section (split child) flashes from the closest heading
        // at-or-before line_start, same fallback as the scroll anchor.
        els.push(anchor)
        let n = anchor.nextElementSibling as HTMLElement | null
        while (n) {
          if (n.hasAttribute('data-src-line')) {
            const l = parseInt(n.dataset.srcLine || '0', 10)
            if (l > endLine) break
          }
          els.push(n)
          n = n.nextElementSibling as HTMLElement | null
        }
      }
      els.forEach(el => el.classList.add('note-hl'))
      if (flashTimer) clearTimeout(flashTimer)
      flashTimer = setTimeout(() => {
        els.forEach(el => el.classList.remove('note-hl'))
      }, 2800)
    })
  })

  onCleanup(() => {
    if (flashTimer) clearTimeout(flashTimer)
  })

  return (
    <>
      {loading() && (
        <div class="file-viewer-loading">
          <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
        </div>
      )}
      <div class={props.class ?? ''} ref={rootRef} innerHTML={html()} />
    </>
  )
}
