import { Component, For, Show, createEffect, createSignal } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import { api } from '../api'
import { cleanCardHtml } from '../lib/clean'
import { renderClozeField } from '../lib/cloze'
import { KATEX_OPTS } from '../lib/math'
import type { ReadingChunk, ReadingCreatedCard } from '../types'

// 左栏「相关卡片」(三栏重构, user spec 2026-09-21).
//
// NOT anki-rag similarity (that project is retired — 匹配准确度达不到要求).
// 相关卡片 here means: every card made from the SAME note segment — exact
// provenance. Two feeding modes:
//   reading round  → the current chunk's cards_created + ancestor_cards
//                    (a split container keeps pre-cut provenance forever)
//   review/preview → the current card's note → GET /api/reading/source →
//                    the segment's cards_created merged with every ancestor
//                    crumb's (same pre-split rule); 404 = 无来源 orphan
// The current card (review/preview mode) is highlighted in the list.
//
// Shell: md-elevated-card + .note-panel-inner, IDENTICAL to NotePanel /
// ReadingPanel so all three columns read as one product (user spec: 长宽
// 一致, 圆角阴影 md3 风格).
//
// Fail-soft like every enrichment panel: dead backend → count line only.

interface Props {
  /** reading-round mode: the chunk on screen (mutually exclusive with noteId) */
  chunk?: ReadingChunk | null
  /** review/preview mode: resolve provenance for this note. Gated on
   *  reveal by the App (noteCard()) — face-down cards pass null so the
   *  list can't leak which segment the answer sits in. */
  noteId?: number | null
  /** forces a full reset even when noteId repeats (card swap) */
  cardKey?: number | null
  /** noteId given but card face-down: show the 先想一想 placeholder */
  blocked?: boolean
  /** bump to refetch after a card add (optimistic ids may be placeholders) */
  refreshToken?: number
}

type Status = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export const RelatedPanel: Component<Props> = (props) => {
  const [status, setStatus] = createSignal<Status>('idle')
  const [cards, setCards] = createSignal<ReadingCreatedCard[]>([])
  const [count, setCount] = createSignal<number | null>(null)
  // note id of the card currently on screen (highlight in review/preview)
  const [selfNote, setSelfNote] = createSignal<number | null>(null)
  let listRef: HTMLDivElement | undefined
  let reqToken = 0

  // merge chunk.cards_created + ancestor_cards (reading mode) — dedup, own
  // cards first (provenance order = creation order)
  const chunkIds = (): number[] => {
    const c = props.chunk
    if (!c) return []
    const own = (c.cards_created || []).filter(id => id > 0)
    const anc = (c.ancestor_cards || []).filter(id => id > 0 && !own.includes(id))
    return [...own, ...anc]
  }

  const refresh = () => {
    const token = ++reqToken
    setCards([])
    setCount(null)
    setSelfNote(null)

    const chunk = props.chunk
    if (chunk) {
      const ids = chunkIds()
      setCount(ids.length)
      if (ids.length === 0) {
        setStatus('ready')
        return
      }
      setStatus('loading')
      api.readingCards(ids).then(res => {
        if (token !== reqToken) return
        setCards(res.cards || [])
        setStatus('ready')
      }).catch(() => {
        if (token === reqToken) setStatus('error')
      })
      return
    }

    const noteId = props.noteId
    if (!noteId) {
      setStatus('idle')
      return
    }
    setStatus('loading')
    api.readingSource(noteId).then(src => {
      if (token !== reqToken) return
      if (src === null) {
        setStatus('empty')
        return
      }
      setSelfNote(noteId)
      // same-segment cards = this segment's + every ancestor crumb's (a card
      // made before a split stays on the container — it covers this content
      // too, which is exactly the duplicate-guard the left column exists for)
      const ids: number[] = []
      for (const list of [src.cards_created, ...src.breadcrumb.map(b => b.cards_created)]) {
        for (const id of list || []) {
          if (id > 0 && !ids.includes(id)) ids.push(id)
        }
      }
      setCount(ids.length)
      if (ids.length === 0) {
        setStatus('ready')
        return
      }
      return api.readingCards(ids).then(res => {
        if (token !== reqToken) return
        setCards(res.cards || [])
        setStatus('ready')
      })
    }).catch(() => {
      if (token === reqToken) setStatus('error')
    })
  }

  createEffect(() => {
    props.chunk?.chunk_key // track
    props.chunk?.cards_created.length // track (optimistic add bumps it)
    props.noteId // track
    props.cardKey // track (card swap with the same note still resets)
    props.refreshToken // track
    refresh()
  })

  // math in the fetched card HTML (same pattern as Flashcard)
  createEffect(() => {
    cards()
    requestAnimationFrame(() => {
      if (listRef) renderMathInElement(listRef, KATEX_OPTS)
    })
  })

  // first field per kind: qa → 正面, cloze → cloze-rendered 文字
  const cardQuestion = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      return renderClozeField(fields['文字'] ?? Object.values(fields)[0] ?? '', 'q')
    }
    return cleanCardHtml(fields['正面'] ?? Object.values(fields)[0] ?? '')
  }
  const cardAnswer = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      return renderClozeField(fields['文字'] ?? Object.values(fields)[0] ?? '', 'a')
    }
    const back = fields['背面'] ?? ''
    return back ? cleanCardHtml(back) : ''
  }

  const title = () => (props.chunk ? '本片段已制卡片' : '相关卡片')

  return (
    <md-elevated-card class="note-panel related-panel">
      <div class="note-panel-inner">
        <div class="note-panel-header">
          <span class="note-panel-title md-typescale-title-small">{title()}</span>
          <Show when={count() != null && (count() ?? 0) > 0}>
            <span class="note-panel-score md-typescale-label-small">{count()} 张</span>
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
                  显示背面后，这里会列出同一笔记片段制的其他卡片
                </span>
              </>
            ) : (
              '开始复习后，这里会列出同一笔记片段制的其他卡片'
            )}
          </div>
        </Show>

        <Show when={status() === 'empty'}>
          <div class="note-panel-state note-panel-empty md-typescale-body-medium">
            无来源
            <span class="note-panel-hint md-typescale-body-small">
              这张卡不是从阅读模式制的，没有同片段卡片
            </span>
          </div>
        </Show>

        <Show when={status() === 'error'}>
          <div class="note-panel-state md-typescale-body-medium">
            {(count() ?? 0) > 0
              ? `已制 ${count()} 张（内容加载失败）`
              : '服务不可用'}
            <md-text-button onClick={() => refresh()}>重试</md-text-button>
          </div>
        </Show>

        <Show when={status() === 'ready'}>
          <Show when={cards().length === 0} fallback={
            <div class="related-list" ref={listRef}>
              <For each={cards()}>
                {c => (
                  <div
                    class={`similar-item related-item${c.noteId === selfNote() ? ' related-item--self' : ''}`}
                  >
                    <div class="similar-q md-typescale-body-medium">
                      <span innerHTML={cardQuestion(c)} />
                      <span class="similar-score md-typescale-label-small">
                        {c.kind === 'cloze' ? `挖空 ×${c.numCards}` : '问答'}
                      </span>
                    </div>
                    <Show when={cardAnswer(c)}>
                      <div class="similar-a md-typescale-body-small" innerHTML={cardAnswer(c)} />
                    </Show>
                    <Show when={c.tags.length > 0}>
                      <div class="reading-made-tags md-typescale-label-small">
                        {c.tags.map(t => `#${t}`).join(' ')}
                      </div>
                    </Show>
                  </div>
                )}
              </For>
            </div>
          }>
            <div class="note-panel-state md-typescale-body-medium">
              还没有从{props.chunk ? '本片段' : '这个片段'}制过卡片
            </div>
          </Show>
        </Show>
      </div>
    </md-elevated-card>
  )
}
