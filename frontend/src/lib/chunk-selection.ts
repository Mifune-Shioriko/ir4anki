import { createEffect, createSignal, onCleanup, onMount } from 'solid-js'
import type { SplitSelection } from './split-selection'
import { selectionToLines } from './split-selection'

// Shared chunk-body selection tracking (三栏重构, 2026-09-21).
//
// Two consumers of a text selection inside the rendered chunk markdown:
//   添加挖空 → the selected TEXT + its containing block's text (so a repeated
//              word wraps the right occurrence in {{c1::}})
//   分割文段 → the selected SOURCE LINE RANGE (block-granular, see
//              lib/split-selection.ts) to POST to /api/reading/split
//
// Tracking goes through `selectionchange` because clicking a toolbar icon
// button clears the live selection before onClick fires (the same problem
// EditDialog's toolbar solves with pointerdown+preventDefault — but tracking
// also survives keyboard activation of the icon button).
//
// Line numbers returned are RELATIVE to the chunk's own text (renderMarkdown
// is fed chunk.text, so data-src-line is 1-based within the chunk). Callers
// add `chunk.line_start - 1` to get file-level lines.

export interface ChunkSelection {
  /** last non-empty selected text inside the body ('' = none) */
  selText: () => string
  /** text of the block element containing the selection (disambiguates
   *  repeated words when wrapping a cloze) */
  selBlock: () => string
  /** selected source-line range relative to the chunk text, or null */
  selLines: () => SplitSelection | null
  /** reset (chunk swap) — a stale selection must never leak into the next
   *  chunk's cloze dialog or a split request */
  reset: () => void
}

export function trackChunkSelection(
  getBody: () => HTMLElement | undefined,
  /** tracked deps that identify the chunk; a change resets the selection */
  deps: () => unknown,
): ChunkSelection {
  const [selText, setSelText] = createSignal('')
  const [selBlock, setSelBlock] = createSignal('')
  const [selLines, setSelLines] = createSignal<SplitSelection | null>(null)

  const reset = () => {
    setSelText('')
    setSelBlock('')
    setSelLines(null)
  }

  const onSelChange = () => {
    const sel = window.getSelection()
    const body = getBody()
    if (!sel || sel.isCollapsed || !sel.rangeCount || !body) return
    const r = sel.getRangeAt(0)
    if (!body.contains(r.commonAncestorContainer)) return
    const text = sel.toString().trim()
    if (!text) return
    setSelText(text)

    // nearest block ancestor of the selection START (li/p/h1-6/td/blockquote)
    let node: Node | null = r.startContainer
    let block = ''
    while (node && node !== body) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as HTMLElement
        if (/^(LI|P|H[1-6]|TD|TH|BLOCKQUOTE)$/.test(el.tagName)) {
          block = (el.textContent || '').replace(/\s+/g, ' ').trim()
          break
        }
      }
      node = node.parentNode
    }
    setSelBlock(block)
    setSelLines(selectionToLines(body, sel))
  }

  onMount(() => document.addEventListener('selectionchange', onSelChange))
  onCleanup(() => document.removeEventListener('selectionchange', onSelChange))

  createEffect(() => {
    deps()
    reset()
  })

  return { selText, selBlock, selLines, reset }
}
