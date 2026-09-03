import { Component, createEffect } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import type { Card } from '../types'
import { cleanCardHtml } from '../lib/clean'
import { KATEX_OPTS, textWithMath } from '../lib/math'

// The study card. Uses <md-elevated-card> (labs/card) — the REAL card
// component. The previous version misused <md-elevation> (a decorative
// shadow element) as a container, which auto-added aria-hidden and broke
// the a11y tree.
//
// Card HTML goes through cleanCardHtml(): the note-type template's <style>
// (hardcoded white/black CSS) and <script> (desktop-only localhost fetches)
// blocks are stripped so only field content renders.
//
// Math: \(…\) / $$…$$ in card content and similar-card answers are rendered
// with KaTeX (auto-render on the innerHTML-owned zones, renderToString for
// the Solid-managed similar-card nodes).
//
// Similar cards: flat inline list under the answer, hidden until reveal,
// NOT collapsible (user spec).
interface Props {
  card: Card
  revealed: boolean
  onEdit: () => void
  onUndo: () => void
  undoEnabled: boolean
  undoBusy: boolean
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
              <md-assist-chip label={props.card.deckName} disabled />
              {props.card.isNew && <md-assist-chip label="新卡" disabled />}
            </md-chip-set>
            <div class="card-header-actions">
              <md-icon-button
                aria-label="撤销上次作答"
                disabled={!props.undoEnabled || props.undoBusy}
                onClick={() => props.onUndo()}
              >
                <md-icon>
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                    <path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z" />
                  </svg>
                </md-icon>
              </md-icon-button>
              <md-icon-button aria-label="编辑卡片" onClick={() => props.onEdit()}>
                <md-icon>
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                    <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                  </svg>
                </md-icon>
              </md-icon-button>
            </div>
          </div>

          {/* question — always visible */}
          <div
            class="card-content card-question"
            ref={questionRef}
            innerHTML={questionHtml()}
          />

          {/* answer + explanation + similar cards — only after reveal */}
          {props.revealed && (
            <>
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
