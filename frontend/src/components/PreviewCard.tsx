import { Component, createEffect } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import type { Card } from '../types'
import { cleanCardHtml } from '../lib/clean'
import { KATEX_OPTS, textWithMath } from '../lib/math'

// Preview card (先看后考): question AND answer are shown together — this
// round is pure reading with NO grading, so there is no reveal step and no
// ease buttons. Per-card decisions live in the action area below (passed in
// as children via props callbacks): approve (放行) or defer (明天再看).
//
// MD3: md-elevated-card container, sys color tokens only, md-typescale
// classes for text. Same visual lineage as Flashcard so the app reads as
// one product; a "预览" assist chip marks the mode.
interface Props {
  card: Card
  onApprove: () => void
  onDefer: () => void
  busy: boolean
}

export const PreviewCard: Component<Props> = (props) => {
  let questionRef: HTMLDivElement | undefined
  let answerRef: HTMLDivElement | undefined

  createEffect(() => {
    props.card.cardId
    props.card.question
    props.card.answer
    requestAnimationFrame(() => {
      if (questionRef) renderMathInElement(questionRef, KATEX_OPTS)
      if (answerRef) {
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

  return (
    <div class="flashcard-wrapper">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <md-chip-set class="card-chips" aria-label="卡片信息">
              <md-assist-chip label="预览 · 只读不考" disabled />
              <md-assist-chip label={props.card.deckName} disabled />
            </md-chip-set>
          </div>

          {/* question + answer together — the whole point of preview mode */}
          <div
            class="card-content card-question"
            ref={questionRef}
            innerHTML={questionHtml()}
          />

          <md-divider class="card-divider" />
          <div class="card-content card-answer" ref={answerRef} innerHTML={answerHtml()} />

          {props.card.explanation && (
            <div class="explain-section">
              <div class="explain-title md-typescale-label-medium">
                AI 讲解 · 仅供参考
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

          <div class="card-meta md-typescale-body-small">
            {props.card.deckName} · {props.card.modelName}
          </div>
        </div>
      </md-elevated-card>

      <div class="action-area">
        <div class="ease-buttons">
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
      </div>
    </div>
  )
}
