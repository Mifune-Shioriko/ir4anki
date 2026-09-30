import { createEffect, createSignal, onCleanup, onMount } from 'solid-js'

// Shared chunk-body selection tracking (三栏重构, 2026-09-21).
//
// ONE consumer since 2026-10-01:
//   添加挖空 → the selected TEXT + its containing block's text (so a repeated
//              word wraps the right occurrence in {{c1::}})
//
// 分割文段 no longer uses the DOM selection — it runs on per-line handle
// buttons (lib/split-selection.ts annotateLines), which map 1:1 onto the
// backend's whole-line segment model instead of guessing line ranges from a
// caret selection. The selLines/selectionToLines path was removed for that
// reason (it never fully matched the backend's logic).
//
// Tracking goes through `selectionchange` because clicking a toolbar icon
// button clears the live selection before onClick fires (the same problem
// EditDialog's toolbar solves with pointerdown+preventDefault — but tracking
// also survives keyboard activation of the icon button).

export interface ChunkSelection {
  /** last non-empty selected text inside the body ('' = none) */
  selText: () => string
  /** text of the block element containing the selection (disambiguates
   *  repeated words when wrapping a cloze) */
  selBlock: () => string
  /** reset (chunk swap) — a stale selection must never leak into the next
   *  chunk's cloze dialog */
  reset: () => void
}

export function trackChunkSelection(
  getBody: () => HTMLElement | undefined,
  /** tracked deps that identify the chunk; a change resets the selection */
  deps: () => unknown,
): ChunkSelection {
  const [selText, setSelText] = createSignal('')
  const [selBlock, setSelBlock] = createSignal('')

  const reset = () => {
    setSelText('')
    setSelBlock('')
  }

  const onSelChange = () => {
    const sel = window.getSelection()
    const body = getBody()
    if (!sel || !sel.rangeCount || !body) return
    const r = sel.getRangeAt(0)
    if (!body.contains(r.commonAncestorContainer)) {
      // selection moved OUT of the card body: a non-empty outside selection
      // clears the tracked state so 挖空 can't seed from stale text (the
      // 2026-09-30 stickiness bug — back then 分割 also rode this state).
      // Collapsed selections (a mere click — including clicking a toolbar or
      // line-handle button, which collapses the in-body selection before
      // onClick fires) are ignored, preserving the cloze dialog's seed text.
      if (!sel.isCollapsed && sel.toString().trim()) {
        reset()
      }
      return
    }
    if (sel.isCollapsed) return
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
  }

  onMount(() => document.addEventListener('selectionchange', onSelChange))
  onCleanup(() => document.removeEventListener('selectionchange', onSelChange))
  createEffect(() => {
    deps()
    reset()
  })

  return { selText, selBlock, reset }
}
