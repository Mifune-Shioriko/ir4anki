import { Component, Show, createEffect, createSignal, onCleanup } from 'solid-js'
import { api } from '../api'
import type { NoteSection } from '../types'
import { renderMarkdown } from '../lib/markdown'

// Right-hand note panel (知识成体系 Phase 1, 2026-09-17).
//
// For the current card, notes-rag (:8791 via /api/note/sections) returns the
// top-k matching note SECTIONS. The panel renders the whole matched file
// (markdown-it + KaTeX) and scrolls+flashes the matched section — "一个笔记
// ≈ 一个页面, top-3 用 tab 切换" (user spec).
//
// Section anchor: markdown.ts tags every heading with data-src-line (source
// line number); chunk line_start is exactly the heading line, so the anchor
// is exact. Split parts of a long section and preamble chunks fall back to
// the closest heading at or before line_start (findAnchor), then to the top.
//
// Fail-soft: sections fetch error → "笔记服务不可用" + retry; no results →
// "未找到对应笔记". The panel never blocks or breaks the review flow.

const fileCache = new Map<string, string>()

interface Props {
  noteId: number | null | undefined
  /** changes force a full reset even when noteId is unchanged (card swap) */
  cardKey: number | null
  /** card is on screen but face-down: hold everything back until reveal
   *  (answer-leak guard, user spec 2026-09-17) — the matched note section
   *  usually contains the answer, so no fetch/scroll/jump may happen before
   *  显示背面. App passes noteId=null while blocked; this flag only drives
   *  the placeholder copy. */
  blocked?: boolean
}

type Status = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

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
  return best
}

export const NotePanel: Component<Props> = (props) => {
  const [status, setStatus] = createSignal<Status>('idle')
  const [sections, setSections] = createSignal<NoteSection[]>([])
  const [selected, setSelected] = createSignal(0)
  const [html, setHtml] = createSignal('')
  let panelRef: HTMLDivElement | undefined
  let bodyRef: HTMLDivElement | undefined
  // monotonic request token: a stale fetch (card swapped mid-flight) can
  // never overwrite the newer card's state
  let reqToken = 0
  let flashTimer: ReturnType<typeof setTimeout> | null = null

  const current = () => sections()[selected()] ?? null

  const loadFile = async (path: string, token: number) => {
    let text = fileCache.get(path)
    if (text == null) {
      const d = await api.notesRaw(path)
      text = d.text
      fileCache.set(path, text)
    }
    if (token !== reqToken) return
    setHtml(renderMarkdown(text))
    setStatus('ready')
  }

  const refresh = () => {
    const noteId = props.noteId
    const token = ++reqToken
    if (flashTimer) {
      clearTimeout(flashTimer)
      flashTimer = null
    }
    setSections([])
    setSelected(0)
    setHtml('')
    if (!noteId) {
      setStatus('idle')
      return
    }
    setStatus('loading')
    api
      .noteSections(noteId, 3)
      .then(d => {
        if (token !== reqToken) return
        const secs = d.sections ?? []
        if (secs.length === 0) {
          setStatus('empty')
          return
        }
        setSections(secs)
        return loadFile(secs[0].file, token)
      })
      .catch(() => {
        if (token !== reqToken) return
        setStatus('error')
      })
  }

  createEffect(() => {
    props.noteId // track
    props.cardKey // track: a new card always resets (even for the same note)
    refresh()
  })

  const onTabChange = (e: Event) => {
    // @material/web md-tabs exposes activeTabIndex (selectedIndex does NOT
    // exist — verified 2026-09-17; reading it gave undefined → the handler
    // silently no-op'd on every click)
    const tabs = e.currentTarget as HTMLElement & { activeTabIndex?: number }
    const i = tabs.activeTabIndex ?? 0
    if (i === selected()) return
    setSelected(i)
    const sec = sections()[i]
    if (!sec) return
    const token = reqToken
    setStatus('loading')
    loadFile(sec.file, token).catch(() => {
      if (token === reqToken) setStatus('error')
    })
  }

  // scroll + flash the matched section after (re)render. Tracks html() AND
  // current(): two sections in the SAME file render identical html, so the
  // tab switch would not re-fire on html alone.
  createEffect(() => {
    const h = html()
    const sec = current()
    if (!h || !sec || status() !== 'ready') return
    requestAnimationFrame(() => {
      if (!bodyRef || !panelRef) return
      bodyRef.querySelectorAll('.note-hl').forEach(el => el.classList.remove('note-hl'))
      const anchor = findAnchor(bodyRef, sec.line_start)
      if (!anchor) {
        panelRef.scrollTop = 0
        return
      }
      anchor.scrollIntoView({ behavior: 'smooth', block: 'start' })
      const els: HTMLElement[] = [anchor]
      let n = anchor.nextElementSibling as HTMLElement | null
      while (n && !n.hasAttribute('data-src-line')) {
        els.push(n)
        n = n.nextElementSibling as HTMLElement | null
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

  const tabLabel = (s: NoteSection) => {
    const t = s.heading_path[s.heading_path.length - 1] || s.title
    return t.length > 18 ? t.slice(0, 17) + '…' : t
  }

  return (
    <md-elevated-card class="note-panel">
      <div class="note-panel-inner" ref={panelRef}>
      <div class="note-panel-header">
        <span class="note-panel-title md-typescale-title-small">笔记</span>
        <Show when={status() === 'ready' && current()}>
          <span class="note-panel-score md-typescale-label-small">
            匹配 {Math.round(current()!.score * 100)}%
          </span>
        </Show>
      </div>

      <Show when={status() === 'loading'}>
        <div class="note-panel-state">
          <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
        </div>
      </Show>

      <Show when={status() === 'idle'}>
        <div class="note-panel-state md-typescale-body-medium">
          {props.blocked ? (
            <>
              先想一想
              <span class="note-panel-hint md-typescale-body-small">
                显示背面后，这里会定位到对应笔记
              </span>
            </>
          ) : (
            '开始复习后，这里会显示卡片对应的笔记'
          )}
        </div>
      </Show>

      <Show when={status() === 'empty'}>
        <div class="note-panel-state note-panel-empty md-typescale-body-medium">
          未找到对应笔记
          <span class="note-panel-hint md-typescale-body-small">
            这张卡可能不是从笔记制的
          </span>
        </div>
      </Show>

      <Show when={status() === 'error'}>
        <div class="note-panel-state md-typescale-body-medium">
          笔记服务不可用
          <md-text-button onClick={() => refresh()}>重试</md-text-button>
        </div>
      </Show>

      <Show when={sections().length > 0}>
        <md-tabs class="note-tabs" onChange={onTabChange}>
          {sections().map((s, i) => (
            <md-primary-tab selected={i === selected()}>
              <span class="note-tab-label">{tabLabel(s)}</span>
            </md-primary-tab>
          ))}
        </md-tabs>
        <Show when={status() === 'ready' && current()}>
          <div class="note-crumb md-typescale-label-small">
            {current()!.file.replace(/^\d{4}\//, '')} · {current()!.heading_path.join(' › ')}
          </div>
        </Show>
        <Show when={status() === 'ready'}>
          <div class="note-body md-typescale-body-medium" ref={bodyRef} innerHTML={html()} />
        </Show>
      </Show>
      </div>
    </md-elevated-card>
  )
}
