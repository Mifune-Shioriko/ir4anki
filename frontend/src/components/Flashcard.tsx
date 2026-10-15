import { StudyCardShell, StudyToolbar } from './StudyCardShell'
import type { JSX } from 'solid-js'
import { Component, createEffect, createSignal } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import type { Card } from '../types'
import { renderField, renderClozeField, isMarkdownNote } from '../lib/cloze'
import { api } from '../api'
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
  dock?: JSX.Element
  nativeEngine?: boolean
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

// The sanitizer intentionally rejects arbitrary layout CSS. Restore generated
// KaTeX to its plain source first, then auto-render trusted KaTeX after cleaning.
// Keeping partially sanitized vlist markup loses superscript/fraction geometry.
const cleanStudyHtml = (html: string) => {
  const template = document.createElement('template')
  template.innerHTML = html
  template.content.querySelectorAll('.katex').forEach(math => {
    const tex = math.querySelector('annotation[encoding="application/x-tex"]')?.textContent
    if (tex == null) return
    const display = math.closest('.katex-display')
    const source = display ? `\\[${tex}\\]` : `\\(${tex}\\)`
    ;(display ?? math).replaceWith(document.createTextNode(source))
  })
  return cleanCardHtml(template.innerHTML)
}

export const Flashcard: Component<Props> = (props) => {
  const [rawNote, setRawNote] = createSignal<{ fields: Record<string,string>; tags: string[]; modelName: string; ord?: number; kind?: 'qa'|'cloze' } | null>(null)
  let generation = 0
  createEffect(() => {
    const id = props.card.cardId
    props.card.question; props.card.answer
    const token = ++generation
    setRawNote(null)
    if (props.card.fields && props.card.tags && Number.isInteger(props.card.ord)) return
    api.note(id).then(n => { if(token === generation) setRawNote(n) }).catch(() => {})
  })
  const source = () => {
    if(rawNote()) return rawNote()
    if (props.card.fields && props.card.tags) return {fields: Object.fromEntries(Object.entries(props.card.fields).map(([k,v]) => [k,typeof v === 'string' ? v : v.value])),tags:props.card.tags,modelName:props.card.modelName}
    return rawNote()
  }
  const markdownHtml = (mode: 'q'|'a') => {
    const note = source()
    if(!note || !isMarkdownNote(note.tags)) return null
    const values = Object.values(note.fields)
    if (props.card.kind === 'cloze' || rawNote()?.kind === 'cloze' || /cloze|填空/i.test(note.modelName)) {
      // Backend ord is zero based. Never guess from cardId: a missing
      // ordinal needs an explicit metadata response before revealing.
      const ord = props.card.ord ?? rawNote()?.ord
      const ordinal = typeof ord === 'number' && Number.isInteger(ord) && ord >= 0 ? ord + 1 : undefined
      return cleanStudyHtml(renderClozeField(values[0] ?? '',mode,note.tags,ordinal) + (mode === 'a' ? renderField(values[1] ?? '',note.tags) : ''))
    }
    return cleanStudyHtml(renderField(values[mode === 'q' ? 0 : 1] ?? '',note.tags))
  }
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
      if (questionRef) renderMathInElement(questionRef, { ...KATEX_OPTS, ignoredClasses: ['katex'] })
      if (props.revealed && answerRef) {
        renderMathInElement(answerRef, { ...KATEX_OPTS, ignoredClasses: ['katex'] })
        answerRef.querySelectorAll('audio').forEach(a => a.play().catch(() => {}))
      }
    })
  })

  // answer HTML = everything after the <hr id=answer> separator if present
  const answerHtml = () => {
    const md = markdownHtml('a'); if(md !== null) return md
    const parts = props.card.answer.split(/<hr id="?answer"?>?/i)
    return cleanStudyHtml(parts.length > 1 ? parts.slice(1).join('') : parts[0])
  }
  const questionHtml = () => markdownHtml('q') ?? cleanStudyHtml(props.card.question)

  return (
    <StudyCardShell stage="review" source={props.card.deckName} badge={props.card.isNew} dock={props.dock} toolbar={
      <StudyToolbar label="复习操作">
              <md-icon-button
                aria-label="撤销上次作答" title="撤销上次作答"
                disabled={!props.undoEnabled || props.undoBusy}
                onClick={() => props.onUndo()}
              >
                <md-icon><IconUndo /></md-icon>
              </md-icon-button>
              <md-icon-button
                aria-label="溯源：回到制卡时的原文片段"
                title={props.traceAvailable ? '溯源：回到制卡时的原文片段' : '这张卡没有阅读来源'}
                disabled={!props.traceAvailable}
                onClick={() => props.onTrace()}
              >
                <md-icon><IconFindInPage /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="添加卡片" title="添加卡片" onClick={() => props.onAdd()}>
                <md-icon><IconAdd /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="删除卡片" title="删除卡片" onClick={() => props.onDelete()}>
                <md-icon><IconDelete /></md-icon>
              </md-icon-button>
              <md-icon-button aria-label="编辑卡片" title="编辑卡片" onClick={() => props.onEdit()}>
                <md-icon><IconEdit /></md-icon>
              </md-icon-button>
      </StudyToolbar>
    }>


          {/* question — always visible */}
          <div
            class="card-content study-content card-question"
            ref={questionRef}
            innerHTML={questionHtml()}
          />

          {/* answer — only after reveal */}
          {props.revealed && (
            <>
              <md-divider class="card-divider" />
              <div class="card-content study-content card-answer" ref={answerRef} innerHTML={answerHtml()} />
            </>
          )}

          <div class="card-meta md-typescale-body-small">
            {props.card.modelName}
          </div>
    </StudyCardShell>
  )
}
