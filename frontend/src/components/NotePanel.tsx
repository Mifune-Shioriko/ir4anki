import { Component, Show, createEffect, createSignal } from 'solid-js'
import { api } from '../api'
import type { ReadingSource } from '../types'
import { FileViewer } from './FileViewer'

// Right-hand note panel — EXACT provenance (round 4, 2026-09-21).
//
// For the current card, /api/reading/source returns the TRUE source segment
// recorded when the card was made (rcards path+seg_id — exact history, no
// similarity guess). The panel renders the whole source file (markdown-it +
// KaTeX via the shared FileViewer, corpus-direct /api/reading/file) and
// scrolls+flashes the segment's [line_start, line_end] range. The breadcrumb
// walks parent_seg_id up to the seeded root, so a card made from a deep cut
// still shows its heading lineage.
//
// Replaces the notes-rag RAG panel (/api/note/sections, top-3 tabs + 匹配度):
// a 404 here means the card has NO reading provenance (pre-round-4, made in
// desktop Anki, or source orphaned) — shown as 无来源, deliberately no
// similarity fallback (「不应该为了历史遗留问题迁就更好的设计」).
//
// Fail-soft: fetch error → "来源服务不可用" + retry; 404 → "无来源". The
// panel never blocks or breaks the review flow.

interface Props {
  noteId: number | null | undefined
  /** changes force a full reset even when noteId is unchanged (card swap) */
  cardKey: number | null
  /** card is on screen but face-down: hold everything back until reveal
   *  (answer-leak guard, user spec 2026-09-17) — the source segment
   *  usually contains the answer, so no fetch/scroll/jump may happen before
   *  显示背面. App passes noteId=null while blocked; this flag only drives
   *  the placeholder copy. */
  blocked?: boolean
}

type Status = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export const NotePanel: Component<Props> = (props) => {
  const [status, setStatus] = createSignal<Status>('idle')
  const [source, setSource] = createSignal<ReadingSource | null>(null)
  // monotonic request token: a stale fetch (card swapped mid-flight) can
  // never overwrite the newer card's state
  let reqToken = 0

  const refresh = () => {
    const noteId = props.noteId
    const token = ++reqToken
    setSource(null)
    if (!noteId) {
      setStatus('idle')
      return
    }
    setStatus('loading')
    api
      .readingSource(noteId)
      .then(s => {
        if (token !== reqToken) return
        if (s === null) {
          setStatus('empty')
          return
        }
        setSource(s)
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

  // 文件 · 根标题 › … › 片段标题 (breadcrumb = seeded root → this segment)
  const crumb = () => {
    const s = source()
    if (!s) return ''
    const file = s.path.replace(/^\d{4}\//, '')
    const titles = s.breadcrumb
      .map(b => b.title)
      .filter(t => t && t.length > 0)
      // consecutive duplicates (a cut child often keeps the parent's heading)
      .filter((t, i, arr) => i === 0 || t !== arr[i - 1])
    return titles.length > 0 ? `${file} · ${titles.join(' › ')}` : file
  }

  return (
    <md-elevated-card class="note-panel">
      <div class="note-panel-inner">
      <div class="note-panel-header">
        <span class="note-panel-title md-typescale-title-small">笔记来源</span>
        <Show when={status() === 'ready' && source()?.stale}>
          <span class="note-panel-score md-typescale-label-small" title="源文件被外部编辑过，片段按指纹重锚失败，范围可能不精确">
            来源已过期
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
                显示背面后，这里会定位到制卡时的原文片段
              </span>
            </>
          ) : (
            '开始复习后，这里会显示卡片对应的笔记'
          )}
        </div>
      </Show>

      <Show when={status() === 'empty'}>
        <div class="note-panel-state note-panel-empty md-typescale-body-medium">
          无来源
          <span class="note-panel-hint md-typescale-body-small">
            这张卡不是从阅读模式制的
          </span>
        </div>
      </Show>

      <Show when={status() === 'error'}>
        <div class="note-panel-state md-typescale-body-medium">
          来源服务不可用
          <md-text-button onClick={() => refresh()}>重试</md-text-button>
        </div>
      </Show>

      <Show when={status() === 'ready' && source()}>
        <div class="note-crumb md-typescale-label-small">{crumb()}</div>
        <FileViewer
          path={source()!.path}
          anchorLine={source()!.line_start}
          anchorEndLine={source()!.line_end}
          anchorToken={source()!.seg_id}
          fetchFile={api.readingFile}
          class="note-body md-typescale-body-medium"
          onError={() => setStatus('error')}
        />
      </Show>
      </div>
    </md-elevated-card>
  )
}
