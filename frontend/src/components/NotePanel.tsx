import { Component, Show, createEffect, createSignal } from 'solid-js'
import { api } from '../api'
import type { NoteSection } from '../types'
import { FileViewer } from './FileViewer'

// Right-hand note panel (知识成体系 Phase 1, 2026-09-17).
//
// For the current card, notes-rag (:8791 via /api/note/sections) returns the
// top-k matching note SECTIONS. The panel renders the whole matched file
// (markdown-it + KaTeX) and scrolls+flashes the matched section — "一个笔记
// ≈ 一个页面, top-3 用 tab 切换" (user spec).
//
// Rendering + anchoring + flash live in the shared FileViewer (extracted
// 2026-09-19 for 渐进制卡 — the reading panel renders the same way). This
// component owns the retrieval orchestration: sections fetch, tab switch,
// stale-request guarding and the status placeholders.
//
// Fail-soft: sections fetch error → "笔记服务不可用" + retry; no results →
// "未找到对应笔记". The panel never blocks or breaks the review flow.

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

export const NotePanel: Component<Props> = (props) => {
  const [status, setStatus] = createSignal<Status>('idle')
  const [sections, setSections] = createSignal<NoteSection[]>([])
  const [selected, setSelected] = createSignal(0)
  // monotonic request token: a stale fetch (card swapped mid-flight) can
  // never overwrite the newer card's state
  let reqToken = 0

  const current = () => sections()[selected()] ?? null

  const refresh = () => {
    const noteId = props.noteId
    const token = ++reqToken
    setSections([])
    setSelected(0)
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
        setStatus('ready')
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
  }

  const tabLabel = (s: NoteSection) => {
    const t = s.heading_path[s.heading_path.length - 1] || s.title
    return t.length > 18 ? t.slice(0, 17) + '…' : t
  }

  return (
    <md-elevated-card class="note-panel">
      <div class="note-panel-inner">
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
        <FileViewer
          path={status() === 'ready' ? current()?.file ?? null : null}
          anchorLine={current()?.line_start ?? null}
          anchorToken={`${selected()}-${current()?.line_start}`}
          fetchFile={api.notesRaw}
          class="note-body md-typescale-body-medium"
          onError={() => setStatus('error')}
        />
      </Show>
      </div>
    </md-elevated-card>
  )
}
