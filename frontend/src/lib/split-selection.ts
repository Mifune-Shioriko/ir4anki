// 分割文段 (segment split, round 4): map a text selection inside the
// rendered markdown chunk body back to SOURCE line numbers.
//
// Every block element rendered by lib/markdown.ts carries
// [data-src-line] (1-based first line) + [data-src-end] (1-based inclusive
// last line). A selection's start/end containers are walked up to their
// nearest tagged block; the range = [startBlock.data-src-line,
// endBlock.data-src-end].
//
// Semantics decisions (deliberate):
//  - a PARTIAL selection inside a block still selects the WHOLE block —
//    segments tile by lines, there is no sub-line granularity, and splitting
//    mid-paragraph would corrupt markdown blocks (tables, lists, fences).
//  - selections that span several blocks select them all (the split API
//    merges touching/overlapping ranges server-side anyway).
//  - inline elements (em/code/strong) without their own map inherit the
//    enclosing block's range via closest().

export interface SplitSelection {
  start_line: number
  end_line: number
}

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

  const a = parseInt(startEl.dataset.srcLine || '0', 10)
  const b = parseInt(endEl.dataset.srcEnd || endEl.dataset.srcLine || '0', 10)
  if (!a || !b || b < a) return null
  return { start_line: a, end_line: b }
}

/** Clamp a line-range to the parent segment. The split API clamps too;
 *  this is for the UI's preview copy (选中 N 行). */
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
