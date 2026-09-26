import { Component, createEffect } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import type { Card } from '../types'
import { cleanCardHtml } from '../lib/clean'
import { KATEX_OPTS } from '../lib/math'
import { IconAdd, IconDelete, IconEdit, IconFindInPage, IconUndo } from './icons'

// The study card. Uses <md-elevated-card> (labs/card) — the REAL card
// component. The previous version misused <md-elevation> (a decorative
// shadow element) as a container, which auto-added aria-hidden and broke
// the a11y tree.
//
// Card HTML goes through cleanCardHtml(): the note-type template's <style>
// (hardcoded white/black CSS) and <script> (desktop-only localhost fetches)
// blocks are stripped so only field content renders.
//
// Math: \(…\) / $$…$$ in card content is rendered with KaTeX (auto-render
// on the innerHTML-owned zone).
//
// 三栏重构 (user spec 2026-09-21): the AI-explanation (anki-explain) and
// similar-cards (anki-rag) blocks are GONE from under the answer — 相关卡片
// (same-segment, exact provenance) lives in the LEFT column (RelatedPanel),
// the note source in the RIGHT column (NotePanel). The header icon row gains
// 溯源: jump to the card's source segment as a temporary reading detour
// (disabled for orphan cards — 404 on /api/reading/source).
interface Props {
  card: Card
  revealed: boolean
  /** card has reading provenance (resolved by the App via reading/source) —
   *  false greys out 溯源 (user spec: 置灰, not hidden) */
  traceAvailable: boolean
  onTrace: () => void
  onEdit: () => void
  onUndo: () => void
  undoEnabled: boolean
  undoBusy: boolean
  onAdd: () => void
  onDelete: () => void
}

export const Flashcard: Component<Props> = (props) => {
  let questionRef: HTMLDivElement | undefined
  let answerRef: HTMLDivElement | undefined

  // auto-play audio + render math after content (re)mounts
  createEffect(() => {
    // track: re-run when the card or reveal state changes — question/answer
    // are tracked too so math added via the edit dialog re-renders after save
    props.card.cardId
    props.card.question
    props.card.answer
    props.revealed
    requestAnimationFrame(() => {
      if (questionRef) renderMathInElement(questionRef, KATEX_OPTS)
      if (props.revealed && answerRef) {
        renderMathInElement(answerRef, KATEX_OPTS)
        answerRef.querySelectorAll('audio').forEach(a => a.play().catch(() => {}))
      }
    })
  })

  // answer HTML = everything after the <hr id=answer> separator if present
  const answerHtml = () => {
    const parts = props.card.answer.split(/<hr id="?answer"?>?/i)
    return cleanCardHtml(parts.length > 1 ? parts.slice(1).join('') : parts[0])
  }
  const questionHtml = () => cleanCardHtml(props.card.question)

  return (
    <div class="flashcard-wrapper">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <md-chip-set class="card-chips" aria-label="卡片信息">
              <md-assist-chip label={props.card.deckName} disabled />
              {props.card.isNew && <md-assist-chip label="新卡" disabled />}
            </md-chip-set>
            <div class="card-header-actions">
              <md-icon-button
                aria-label="撤销上次作答"
                disabled={!props.undoEnabled || props.undoBusy}
                onClick={() => props.onUndo()}
              >
                <md-icon><IconUndo /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="编辑卡片" onClick={() => props.onEdit()}>
                <md-icon><IconEdit /></md-icon>
              </md-icon-button>
              <md-icon-button
                aria-label="溯源：回到制卡时的原文片段"
                title={props.traceAvailable ? '溯源：回到制卡时的原文片段' : '这张卡没有阅读来源'}
                disabled={!props.traceAvailable}
                onClick={() => props.onTrace()}
              >
                <md-icon><IconFindInPage /></md-icon>
              </md-icon-button>
              <div class="header-actions-sep" aria-hidden="true" />
              <md-icon-button aria-label="添加卡片" onClick={() => props.onAdd()}>
                <md-icon><IconAdd /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="删除卡片" onClick={() => props.onDelete()}>
                <md-icon><IconDelete /></md-icon>
              </md-icon-button>
            </div>
          </div>

          {/* question — always visible */}
          <div
            class="card-content card-question"
            ref={questionRef}
            innerHTML={questionHtml()}
          />

          {/* answer — only after reveal */}
          {props.revealed && (
            <>
              <md-divider class="card-divider" />
              <div class="card-content card-answer" ref={answerRef} innerHTML={answerHtml()} />
            </>
          )}

          <div class="card-meta md-typescale-body-small">
            {props.card.deckName} · {props.card.modelName}
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
