import { Component, For, Show, createEffect, createSignal, onCleanup, onMount } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import { api } from '../api'
import { cleanCardHtml } from '../lib/clean'
import { renderCloze } from '../lib/cloze'
import { renderMarkdown } from '../lib/markdown'
import { KATEX_OPTS } from '../lib/math'
import type { ReadingChunk, ReadingChunkStatus, ReadingCreatedCard } from '../types'
import { IconAdd, IconPassword } from './icons'

// Reading card (渐进制卡, user spec 2026-09-19; action redesign 2026-09-19
// round 2). One note chunk to read and turn into cards BY HAND. No reveal
// gate (this is reading, not a retrieval attempt). The whole source file
// renders in the right column (ReadingPanel) anchored at this chunk's line.
//
// Layout mirrors Flashcard/PreviewCard (user request: 和复习卡一样的左栏):
//   header  = status chips (left) + icon actions (right): 添加卡片 / 添加挖空
//   bottom  = status buttons ONLY: 结束阅读 | 无需制卡，跳过 |
//             下一张（稍后继续） | 制卡完成 (filled)
// The old 开始制卡 gate is GONE — chunks auto-mark 正在制卡 when a card or
// cloze is created from them (backend _reading_record_card), and 制卡完成 /
// 跳过 work straight from 未读.
//
// 添加挖空 captures the CURRENT text selection inside the chunk body on
// pointerdown (before the button steals it) and hands it to the cloze
// dialog; empty selection → the dialog starts from the whole chunk text.
//
// Keyboard (App-level, window listener): Space = 制卡完成,
// A = 添加卡片, C = 添加挖空, S = 跳过, N = 下一张.

const STATUS_LABEL: Record<ReadingChunkStatus, string> = {
  todo: '未读',
  active: '正在制卡',
  done: '制卡完成',
  skipped: '已跳过',
}

interface Props {
  chunk: ReadingChunk
  busy: boolean
  /** mark done + advance */
  onComplete: () => void
  /** advance WITHOUT status change (下一张, 稍后继续) */
  onNext: () => void
  /** mark skipped + advance */
  onSkip: () => void
  /** open the add-card dialog pre-linked to this chunk */
  onAdd: () => void
  /** open the cloze dialog; selText = selection inside the chunk body,
   * selBlock = text of the block element (li/p/heading…) containing it
   * ('' = no selection). The block disambiguates WHICH occurrence of a
   * repeated word the user meant when wrapping in {{c1::}}. */
  onCloze: (selText: string, selBlock: string) => void
  /** mid-round exit: untouched chunks stay todo/active */
  onExit: () => void
}

export const ReadingCard: Component<Props> = (props) => {
  let bodyRef: HTMLDivElement | undefined
  // ---- 本片段已制卡片 (user spec 2026-09-20): cards_created holds note
  // ids; fetch their content lazily and render as a 相关卡片-styled list.
  // A placeholder id 0 = card added THIS session before the fetch round-trip
  // (App.onCardAdded appends 0) — skipped; the real id arrives via refetch
  // on chunk reload. Fail-soft: dead backend → count line only.
  const [madeCards, setMadeCards] = createSignal<ReadingCreatedCard[] | null>(null)
  let madeRef: HTMLDivElement | undefined
  // render \(…\)/$$…$$ math in the fetched card HTML (same pattern as
  // Flashcard's innerHTML zones)
  createEffect(() => {
    madeCards()
    requestAnimationFrame(() => {
      if (madeRef) renderMathInElement(madeRef, KATEX_OPTS)
    })
  })
  let fetchToken = 0
  createEffect(() => {
    const ids = props.chunk.cards_created.filter(id => id > 0)
    const token = ++fetchToken
    if (ids.length === 0) {
      setMadeCards(null)
      return
    }
    setMadeCards(null)
    api.readingCards(ids).then(res => {
      if (token !== fetchToken) return // chunk moved on
      setMadeCards(res.cards && res.cards.length > 0 ? res.cards : null)
    }).catch(() => {
      if (token === fetchToken) setMadeCards(null)
    })
  })
  // first field's content per note kind: qa → 正面, cloze → cloze-rendered 文字
  const cardQuestion = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      const clozeField = fields['文字'] ?? Object.values(fields)[0] ?? ''
      return renderCloze(clozeField, 'q')
    }
    return cleanCardHtml(fields['正面'] ?? Object.values(fields)[0] ?? '')
  }
  const cardAnswer = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      const clozeField = fields['文字'] ?? Object.values(fields)[0] ?? ''
      return renderCloze(clozeField, 'a')
    }
    const back = fields['背面'] ?? ''
    return back ? cleanCardHtml(back) : ''
  }
  // last non-empty selection INSIDE the chunk body + its containing block
  // element's text (disambiguation for cloze wrapping). Tracked via
  // selectionchange because clicking the toolbar button clears the live
  // selection before onClick fires (the same problem EditDialog's toolbar
  // solves with pointerdown+preventDefault — but tracking also survives
  // keyboard activation of the icon button).
  let lastSel = ''
  let lastSelBlock = ''
  const onSelChange = () => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !sel.rangeCount || !bodyRef) return
    const r = sel.getRangeAt(0)
    if (!bodyRef.contains(r.commonAncestorContainer)) return
    const text = sel.toString().trim()
    if (!text) return
    lastSel = text
    // nearest block ancestor of the selection START (li/p/h1-6/td/blockquote)
    let node: Node | null = r.startContainer
    let block = ''
    while (node && node !== bodyRef) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as HTMLElement
        if (/^(LI|P|H[1-6]|TD|TH|BLOCKQUOTE)$/.test(el.tagName)) {
          block = (el.textContent || '').replace(/\s+/g, ' ').trim()
          break
        }
      }
      node = node.parentNode
    }
    lastSelBlock = block
  }
  onMount(() => document.addEventListener('selectionchange', onSelChange))
  onCleanup(() => document.removeEventListener('selectionchange', onSelChange))
  // a stale selection must never leak into the NEXT chunk's cloze dialog
  createEffect(() => {
    props.chunk.chunk_key
    props.chunk.path
    lastSel = ''
    lastSelBlock = ''
  })

  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '').replace(/\.md$/, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} › ${heads}` : file
  }
  const fileProgress = () => {
    const done = props.chunk.file_done + props.chunk.file_skipped
    return `${done} / ${props.chunk.file_chunks}`
  }

  return (
    <div class="flashcard-wrapper">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <md-chip-set class="card-chips" aria-label="片段信息">
              <md-assist-chip
                label={STATUS_LABEL[props.chunk.status]}
                class={`reading-status-chip reading-status-${props.chunk.status}`}
                disabled
              />
              <md-assist-chip label={`本文件进度 ${fileProgress()}`} disabled />
            </md-chip-set>
            <div class="card-header-actions">
              <md-icon-button
                aria-label="添加卡片"
                disabled={props.busy}
                onClick={() => props.onAdd()}
              >
                <md-icon><IconAdd /></md-icon>
              </md-icon-button>
              <md-icon-button
                aria-label="添加挖空"
                disabled={props.busy}
                onClick={() => props.onCloze(lastSel, lastSelBlock)}
              >
                <md-icon><IconPassword /></md-icon>
              </md-icon-button>
            </div>
          </div>

          <div class="reading-crumb md-typescale-label-small">{crumb()}</div>

          <div
            class="card-content reading-chunk-body note-body md-typescale-body-medium"
            ref={bodyRef}
            innerHTML={renderMarkdown(props.chunk.text)}
          />

          {/* 本片段已制卡片 (user spec 2026-09-20): styled like the review
              UI's 相关卡片 — flat inline list, not collapsible. Count line
              doubles as the fetch fallback (dead AnkiConnect / placeholder
              ids only). */}
          <Show when={props.chunk.cards_created.length > 0}>
            <div class="reading-cards-made md-typescale-label-small">
              已从本片段制卡 {props.chunk.cards_created.length} 张
            </div>
            <Show when={madeCards()}>
              <div class="similar-section reading-made-section" ref={madeRef}>
                <div class="similar-title md-typescale-label-medium">已制卡片</div>
                <For each={madeCards() ?? []}>
                  {c => (
                    <div class="similar-item reading-made-item">
                      <div class="similar-q md-typescale-body-medium">
                        <span innerHTML={cardQuestion(c)} />
                        <span class="similar-score md-typescale-label-small">
                          {c.kind === 'cloze' ? `挖空 ×${c.numCards}` : '问答'}
                        </span>
                      </div>
                      <Show when={cardAnswer(c)}>
                        <div
                          class="similar-a md-typescale-body-small"
                          innerHTML={cardAnswer(c)}
                        />
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
            </Show>
          </Show>
        </div>
      </md-elevated-card>

      <div class="action-area">
        <div class="ease-buttons">
          <md-text-button onClick={() => props.onExit()} disabled={props.busy}>
            结束阅读
          </md-text-button>
          <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>
            {props.chunk.status === 'active' ? '跳过' : '无需制卡，跳过'}
          </md-text-button>
          <md-text-button onClick={() => props.onNext()} disabled={props.busy}>
            下一张（稍后继续）
          </md-text-button>
          <md-filled-button onClick={() => props.onComplete()} disabled={props.busy}>
            制卡完成
          </md-filled-button>
        </div>
      </div>
    </div>
  )
}
