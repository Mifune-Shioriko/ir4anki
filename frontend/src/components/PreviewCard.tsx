import { Component, createEffect } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import type { Card } from '../types'
import { cleanCardHtml } from '../lib/clean'
import { KATEX_OPTS, textWithMath } from '../lib/math'
import { IconAdd, IconDelete, IconEdit, IconUndo } from './icons'

// Preview card (先看后考, 2026-09-07 redesign): the question shows first and
// the answer stays HIDDEN until the user reveals it — a low-stakes retrieval
// attempt (test-potentiated learning) instead of pure reading. There is
// still NO grading. Per-card decisions: approve (放行 — released into the
// study queue the NEXT day, see backend release_yesterday_approved) or
// defer (明天再看). Approve is only enabled after reveal: "已看完" must
// mean the card was actually seen.
//
// MD3: md-elevated-card container, sys color tokens only, md-typescale
// classes for text. Same visual lineage as Flashcard so the app reads as
// one product; a "预览" assist chip marks the mode.
interface Props {
  card: Card
  revealed: boolean
  onReveal: () => void
  onApprove: () => void
  onDefer: () => void
  onUndo: () => void
  onEdit: () => void
  onAdd: () => void
  onDelete: () => void
  /** mid-round exit (2026-09-06): untouched cards stay suspended in the pool */
  onExit: () => void
  undoEnabled: boolean
  undoBusy: boolean
  busy: boolean
}

export const PreviewCard: Component<Props> = (props) => {
  let questionRef: HTMLDivElement | undefined
  let answerRef: HTMLDivElement | undefined

  createEffect(() => {
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

  const answerHtml = () => {
    const parts = props.card.answer.split(/<hr id="?answer"?>?/i)
    return cleanCardHtml(parts.length > 1 ? parts.slice(1).join('') : props.card.answer)
  }
  const questionHtml = () => cleanCardHtml(props.card.question)
  const sims = () => props.card.similar || []
  // flat string list from anki-prior-knowledge; defensive filter in case a
  // malformed payload ever sneaks a non-string in
  const priors = () => (props.card.priorKnowledge || []).filter(x => typeof x === 'string' && x.trim())

  return (
    <div class="flashcard-wrapper">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <md-chip-set class="card-chips" aria-label="卡片信息">
              <md-assist-chip label="预览 · 先想后看" disabled />
              <md-assist-chip label={props.card.deckName} disabled />
            </md-chip-set>
            <div class="card-header-actions">
              <md-icon-button
                aria-label="撤销上一步"
                disabled={!props.undoEnabled || props.undoBusy || props.busy}
                onClick={() => props.onUndo()}
              >
                <md-icon><IconUndo /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="编辑卡片" onClick={() => props.onEdit()}>
                <md-icon><IconEdit /></md-icon>
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

          {/* question — always visible; answer only after reveal (2026-09-07) */}
          <div
            class="card-content card-question"
            ref={questionRef}
            innerHTML={questionHtml()}
          />

          {props.revealed && (
            <>
              <md-divider class="card-divider" />
              <div class="card-content card-answer" ref={answerRef} innerHTML={answerHtml()} />

              {priors().length > 0 && (
                <div class="prior-section">
                  <div class="prior-title md-typescale-label-medium">
                    前置知识
                  </div>
                  <ul class="prior-list md-typescale-body-medium">
                    {priors().map(item => (
                      <li innerHTML={textWithMath(item)} />
                    ))}
                  </ul>
                </div>
              )}

              {props.card.explanation && (
                <div class="explain-section">
                  <div class="explain-title md-typescale-label-medium">
                    AI 讲解
                  </div>
                  <div
                    class="explain-text md-typescale-body-medium"
                    innerHTML={textWithMath(props.card.explanation)}
                  />
                </div>
              )}

              {sims().length > 0 && (
                <div class="similar-section">
                  <div class="similar-title md-typescale-label-medium">相关卡片</div>
                  {sims().map(s => (
                    <div class="similar-item">
                      <div class="similar-q md-typescale-body-medium">
                        <span innerHTML={textWithMath(s.question)} />
                        <span class="similar-score md-typescale-label-small">
                          相似 {Math.round(s.score * 100)}%
                        </span>
                      </div>
                      {s.answer && (
                        <div
                          class="similar-a md-typescale-body-small"
                          innerHTML={textWithMath(s.answer)}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          <div class="card-meta md-typescale-body-small">
            {props.card.deckName} · {props.card.modelName}
          </div>
        </div>
      </md-elevated-card>

      <div class="action-area">
        {!props.revealed ? (
          <md-filled-tonal-button onClick={() => props.onReveal()} disabled={props.busy}>
            先想一想，再看答案
          </md-filled-tonal-button>
        ) : (
          <div class="ease-buttons">
            <md-outlined-button
              onClick={() => props.onExit()}
              disabled={props.busy}
            >
              结束预览
            </md-outlined-button>
            <md-outlined-button
              onClick={() => props.onDefer()}
              disabled={props.busy}
            >
              明天再看
            </md-outlined-button>
            <md-filled-button
              class="preview-approve"
              onClick={() => props.onApprove()}
              disabled={props.busy}
            >
              已看完，放行
            </md-filled-button>
          </div>
        )}
      </div>
    </div>
  )
}
