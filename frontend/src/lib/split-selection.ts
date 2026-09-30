// 分割文段 (segment split, round 4): map a text selection inside the
// rendered markdown chunk body back to SOURCE line numbers.
//
// Every block element rendered by lib/markdown.ts carries
// [data-src-line] (1-based first line) + [data-src-end] (1-based inclusive
// last line). A selection's start/end containers are walked up to their
// nearest tagged block.
//
// Granularity (2026-09-30, 行级映射):
//  - SOFT-WRAPPED blocks (p / li / h1-6 / blockquote): markdown-it renders
//    soft breaks as '\n' inside the block's text, so the exact line of the
//    selection start/end = block.data-src-line + (number of '\n' before the
//    caret inside the block). Selecting lines 2-4 of a 5-line paragraph now
//    cuts exactly those lines. The user's notes average ~19 chars/line, so
//    line-level ≈ sentence-level in practice.
//  - ATOMIC blocks (pre / table / .katex-block): a sub-range cut would
//    corrupt the markdown construct, so a selection touching one snaps to
//    the WHOLE block (its own tagged range). Their textContent newlines
//    (code lines, table rows, formula source) must NOT be counted as soft
//    breaks.
//  - selections spanning several blocks include everything between (the
//    split API merges touching/overlapping ranges server-side anyway).
//  - character-level (mid-line) cuts are deliberately NOT supported: the
//    segment model stores whole lines, and character offsets rot under
//    external edits. Sub-sentence extraction is what 添加挖空 is for.

export interface SplitSelection {
  start_line: number
  end_line: number
}

/** Elements whose interior line structure must not be refined — snap to
 *  the whole block instead. */
const ATOMIC_SEL = 'pre, table, .katex-block'

/** Resolve a live selection inside `body` to an inclusive source-line
 *  range. Returns null when there is no usable selection inside body. */
export function selectionToLines(
  body: HTMLElement,
  sel: Selection,
): SplitSelection | null {
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null
  const r = sel.getRangeAt(0)
  if (!body.contains(r.commonAncestorContainer)) return null
  if (!sel.toString().trim()) return null

  const blockOf = (node: Node): HTMLElement | null => {
    let n: Node | null = node
    while (n && n !== body) {
      if (n.nodeType === Node.ELEMENT_NODE) {
        const hit = (n as HTMLElement).closest<HTMLElement>('[data-src-line]')
        if (hit) return hit
      }
      n = n.parentNode
    }
    return null
  }

  let startEl = blockOf(r.startContainer)
  let endEl = blockOf(r.endContainer)

  if (!startEl || !endEl) {
    // selection not inside any tagged block (shouldn't happen for rendered
    // markdown; guard anyway) — use the first/last tagged block the range
    // actually covers
    const all = r.cloneContents().querySelectorAll<HTMLElement>('[data-src-line]')
    if (!all.length) return null
    startEl = startEl ?? all[0]
    endEl = endEl ?? all[all.length - 1]
  }

  // snap an endpoint sitting inside an atomic construct out to the whole
  // atomic block (tr → table, code → pre, katex internals → .katex-block)
  const atomicOf = (el: HTMLElement): HTMLElement | null => {
    const hit = el.closest<HTMLElement>(ATOMIC_SEL)
    // only meaningful when the atomic block itself is inside body and tagged
    return hit && body.contains(hit) && hit.dataset.srcLine ? hit : null
  }

  const blockStart = (el: HTMLElement): number =>
    parseInt(el.dataset.srcLine || '0', 10)
  const blockEnd = (el: HTMLElement): number =>
    parseInt(el.dataset.srcEnd || el.dataset.srcLine || '0', 10)

  /** Count soft-break '\n' between the block's start and an endpoint of the
   *  selection. cloneContents keeps DOM structure; toString() then yields the
   *  soft-break newlines (inline katex MathML carries none — verified against
   *  the render pipeline). atLineStart = the endpoint sits exactly after a
   *  '\n' (i.e. flush at the start of the next source line). */
  const newlineOffset = (
    el: HTMLElement,
    container: Node,
    offset: number,
  ): { n: number; atLineStart: boolean } => {
    try {
      const sub = document.createRange()
      sub.selectNodeContents(el)
      sub.setEnd(container, offset)
      const text = sub.toString()
      let n = 0
      for (let i = 0; i < text.length; i++) if (text[i] === '\n') n++
      return { n, atLineStart: text.endsWith('\n') }
    } catch {
      return { n: 0, atLineStart: false } // endpoint outside block → no refine
    }
  }

  // ---- start line ----
  let a: number
  const startAtomic = atomicOf(startEl)
  if (startAtomic) {
    a = blockStart(startAtomic)
  } else {
    // caret at the start of line k belongs to line k
    a = blockStart(startEl) + newlineOffset(startEl, r.startContainer, r.startOffset).n
  }

  // ---- end line ----
  let b: number
  const endAtomic = atomicOf(endEl)
  if (endAtomic) {
    b = blockEnd(endAtomic)
  } else {
    const { n, atLineStart } = newlineOffset(endEl, r.endContainer, r.endOffset)
    // endpoint flush at the start of line n → last selected char is on n-1
    b = blockStart(endEl) + n - (atLineStart && n > 0 ? 1 : 0)
  }

  if (!a || !b || b < a) return null
  return { start_line: a, end_line: b }
}

/** Clamp a line-range to the parent segment. The split API clamps too;
 *  this is for the UI's preview copy (将切出…). */
export function clampToSegment(
  sel: SplitSelection,
  segStart: number,
  segEnd: number,
): SplitSelection {
  return {
    start_line: Math.max(sel.start_line, segStart),
    end_line: Math.min(sel.end_line, segEnd),
  }
}

/** The text a split would actually cut (relative-line range into the chunk
 *  text) — the P4 preview: block/line expansion means the selection and the
 *  cut range can differ, show the truth before the click. */
export function previewCutText(chunkText: string, sel: SplitSelection): string {
  const lines = chunkText.split('\n')
  const cut = lines.slice(sel.start_line - 1, sel.end_line)
  return cut.join(' ⏎ ').trim()
}
