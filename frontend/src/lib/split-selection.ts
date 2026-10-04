// 分割文段 (segment split) — LINE-HANDLE model (2026-10-01).
//
// SUPERSEDES the text-selection→line-range mapping (selectionToLines):
// guessing source lines from a caret selection (soft-break counting,
// atomic-block snapping, sticky selectionchange state) never fully matched
// the backend's whole-line segment model. Now every rendered source line
// carries a small handle button in the left gutter; the user clicks the
// first line, clicks the last line, gets an inline confirm bar with a
// head+tail preview, and confirms. The cut range is the union of the two
// clicked items — no DOM-selection heuristics at all.
//
// How lines are found (annotateLines):
//  - lib/markdown.ts tags every block element with data-src-line /
//    data-src-end (1-based, relative to the chunk text).
//  - ATOMIC blocks (pre / table / hr / .katex-block) cannot be cut
//    mid-block without corrupting the markdown construct, so each gets ONE
//    handle covering its whole line range (same semantic as the old
//    atomic snap). They are wrapped in a div.line-anchor carrying
//    data-line-start/end.
//  - SOFT blocks (p / li / h1-6 / blockquote …): markdown-it renders soft
//    breaks as '\n' text nodes, so a walk that counts '\n' maps every
//    rendered fragment to its exact source line. Each line's runs are
//    wrapped in span.line (also data-line-start/end); the FIRST span of a
//    line gets the handle. The user's notes average ~19 chars/line, so one
//    handle ≈ one sentence.
//  - Blank source lines render nothing → no handle (the split API trims
//    blank edges anyway).
//  - Nested blocks (li > ul > li, blockquote > p) are handled by resetting
//    the line counter from each tagged child's authoritative data-src-line.
//  - Inline .katex spans are treated as indivisible (their MathML
//    annotation carries the TeX source; its newlines are not soft breaks).
//
// Character-level (mid-line) cuts remain deliberately unsupported: the
// segment model stores whole lines. Sub-sentence extraction is 添加挖空.

export interface SplitSelection {
  start_line: number
  end_line: number
}

/** Blocks that must be cut whole (one handle for the entire range). */
const ATOMIC_SEL = 'pre, table, hr, .katex-block'

// annotate is idempotent per rendered content: the body element survives
// chunk swaps (only innerHTML changes), so remember what we annotated.
const annotatedToken = new WeakMap<HTMLElement, string>()

function makeHandle(start: number, end: number): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = 'line-handle'
  b.tabIndex = -1
  b.dataset.lineStart = String(start)
  b.dataset.lineEnd = String(end)
  b.title =
    start === end
      ? `选择第 ${start} 行作为分割起点/终点`
      : `选择第 ${start}–${end} 行（整块）作为分割起点/终点`
  return b
}

/** Split every text node containing '\n' into ['piece', '\n', 'piece', …]
 *  sibling text nodes so the main walk only ever sees newline-free text and
 *  standalone '\n' separators. Skips .katex interiors. */
function splitNewlines(root: Element): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const targets: Text[] = []
  let n = walker.nextNode() as Text | null
  while (n) {
    if ((n.textContent || '').includes('\n') && !n.parentElement?.closest('.katex')) {
      targets.push(n)
    }
    n = walker.nextNode() as Text | null
  }
  for (const t of targets) {
    let node: Text = t
    let idx = (node.textContent || '').indexOf('\n')
    while (idx >= 0) {
      const nl = node.splitText(idx) // nl starts with '\n'
      const rest = nl.splitText(1) // nl == '\n', rest == remainder
      node = rest
      idx = (node.textContent || '').indexOf('\n')
    }
  }
}

interface WalkState {
  line: number
}

function runHasContent(run: Node[]): boolean {
  for (const n of run) {
    if (n.nodeType === Node.ELEMENT_NODE) {
      const tag = (n as HTMLElement).tagName
      if (tag === 'IMG' || tag === 'BR') return true
    }
    if ((n.textContent || '').trim() !== '') return true
  }
  return false
}

function wrapRun(
  run: Node[],
  line: number,
  handled: Set<number>,
): void {
  const span = document.createElement('span')
  span.className = 'line'
  span.dataset.lineStart = String(line)
  span.dataset.lineEnd = String(line)
  run[0].parentNode!.insertBefore(span, run[0])
  for (const n of run) span.appendChild(n)
  if (!handled.has(line)) {
    handled.add(line)
    span.prepend(makeHandle(line, line))
  }
}

/** Wrap an atomic block in a positioned div.line-anchor with ONE handle. */
function wrapAtomic(el: HTMLElement): void {
  const start = parseInt(el.dataset.srcLine || '0', 10)
  const end = parseInt(el.dataset.srcEnd || el.dataset.srcLine || '0', 10)
  if (!start) return
  const w = document.createElement('div')
  w.className = 'line-anchor'
  w.dataset.lineStart = String(start)
  w.dataset.lineEnd = String(Math.max(end, start))
  el.replaceWith(w)
  w.appendChild(el)
  w.appendChild(makeHandle(start, Math.max(end, start)))
}

/** Recursive walk. Every tagged (data-src-line) element resets the counter
 *  authoritatively, so stray '\n' whitespace between blocks (markdown-it
 *  joins block outputs with newlines) can't corrupt the mapping: inside a
 *  soft leaf block there are no tagged children, so '\n' counting there is
 *  exact. */
function walk(parent: Element, st: WalkState, handled: Set<number>): void {
  let run: Node[] = []
  let runLine = st.line

  const flush = () => {
    if (run.length && runHasContent(run)) wrapRun(run, runLine, handled)
    run = []
  }

  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const data = node.textContent || ''
      if (data === '\n') {
        // soft break (or inter-block whitespace — corrected by the next
        // tagged sibling's data-src-line reset): ends the current line.
        run.push(node)
        flush()
        st.line += 1
        runLine = st.line
      } else {
        run.push(node)
      }
      continue
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue
    const el = node as HTMLElement

    if (el.dataset.srcLine) {
      // a tagged block: reset to its authoritative start line
      flush()
      st.line = parseInt(el.dataset.srcLine, 10)
      runLine = st.line
      if (el.matches(ATOMIC_SEL)) {
        wrapAtomic(el)
      } else {
        walk(el, st, handled)
      }
      st.line = parseInt(el.dataset.srcEnd || el.dataset.srcLine, 10) + 1
      runLine = st.line
      continue
    }

    if (el.tagName === 'BR') {
      run.push(el)
      flush()
      st.line += 1
      runLine = st.line
      continue
    }

    if (el.matches('.katex, img')) {
      run.push(el)
      continue
    }

    const text = el.textContent || ''
    if (text.includes('\n') && !el.closest('.katex')) {
      // inline element spanning a soft break (e.g. **bold over two
      // lines**) — descend so both lines get their own spans inside it
      flush()
      walk(el, st, handled)
      runLine = st.line
      continue
    }
    run.push(el)
  }
  flush()
}

/** Annotate every rendered source line inside `body` with a span.line (or
 *  div.line-anchor for atomic blocks) carrying data-line-start/end plus a
 *  gutter handle button. Idempotent per (element, token). */
export function annotateLines(body: HTMLElement, token: string): void {
  if (annotatedToken.get(body) === token) return
  splitNewlines(body)
  const handled = new Set<number>()
  const st: WalkState = { line: 1 }
  walk(body, st, handled)
  annotatedToken.set(body, token)
}

/** All annotated items in the body, as line ranges. */
export function annotatedItems(
  body: HTMLElement,
): { el: HTMLElement; start: number; end: number }[] {
  const out: { el: HTMLElement; start: number; end: number }[] = []
  for (const el of Array.from(
    body.querySelectorAll<HTMLElement>('.line, .line-anchor'),
  )) {
    const s = parseInt(el.dataset.lineStart || '0', 10)
    const e = parseInt(el.dataset.lineEnd || el.dataset.lineStart || '0', 10)
    if (s) out.push({ el, start: s, end: e })
  }
  return out
}

/** Resolve the source line at a viewport point (user spec 2026-10-05).
 *
 * Why not CSS :hover: a soft-wrapped source line spans several visual rows,
 * and the line-height gap between those rows belongs to NO inline box —
 * :hover drops out there, so sweeping the mouse down a paragraph made the
 * indicator blink on/off. This resolver treats 一行就是一个 block:
 *   1. direct hit (elementFromPoint inside a .line / .line-anchor),
 *   2. caret hit-test (caretRangeFromPoint / caretPositionFromPoint) —
 *      resolves gap points to the surrounding text, then narrows to the
 *      items inside that tagged block,
 *   3. nearest item by geometric distance, capped at HOVER_REACH px so the
 *      card's outer padding stays dark.
 * Returns null when nothing is within reach. */
const HOVER_REACH = 24

export function resolveLineAt(
  body: HTMLElement,
  x: number,
  y: number,
): { el: HTMLElement; start: number; end: number } | null {
  const items = annotatedItems(body)
  if (!items.length) return null
  // 1. direct hit
  const under = document.elementFromPoint(x, y) as HTMLElement | null
  if (under && body.contains(under)) {
    const direct = under.closest<HTMLElement>('.line, .line-anchor')
    if (direct) {
      const found = items.find(i => i.el === direct)
      if (found) return found
    }
  }
  // 2. caret hit-test → the block owning the gap
  let scope: Element | null = null
  const caretRange = (document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
  }).caretRangeFromPoint
  if (caretRange) {
    const r = caretRange.call(document, x, y)
    const n = r?.startContainer ?? null
    scope = n ? (n.nodeType === Node.ELEMENT_NODE ? (n as Element) : n.parentElement) : null
  } else {
    const caretPos = (document as Document & {
      caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node } | null
    }).caretPositionFromPoint
    const p = caretPos ? caretPos.call(document, x, y) : null
    const n = p?.offsetNode ?? null
    scope = n ? (n.nodeType === Node.ELEMENT_NODE ? (n as Element) : n.parentElement) : null
  }
  let pool = items
  if (scope && body.contains(scope)) {
    const block = scope.closest('[data-src-line]')
    if (block) {
      const inner = items.filter(i => block.contains(i.el))
      if (inner.length) pool = inner
    }
  }
  // 3. nearest by distance to the item's box (0 when inside it)
  let best: (typeof items)[number] | null = null
  let bestD = HOVER_REACH * HOVER_REACH
  for (const it of pool) {
    const r = it.el.getBoundingClientRect()
    const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0
    const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0
    const d = dx * dx + dy * dy
    if (d < bestD) {
      bestD = d
      best = it
    }
  }
  return best
}

function ellipsizeMiddle(s: string, head = 26, tail = 18): string {
  if (s.length <= head + tail + 3) return s
  return `${s.slice(0, head)} … ${s.slice(-tail)}`
}

/** Head+tail preview of the cut range (user spec 2026-10-01: the old
 *  head-only snippet couldn't confirm where the cut ENDED). Each of the
 *  first/last source lines is itself middle-ellipsized when very long, and
 *  returned raw for the title tooltip. */
export function previewHeadTail(
  chunkText: string,
  sel: SplitSelection,
): { head: string; tail: string; headFull: string; tailFull: string } {
  const lines = chunkText.split('\n')
  const headFull = (lines[sel.start_line - 1] ?? '').trim()
  const tailFull = (lines[sel.end_line - 1] ?? '').trim()
  return {
    head: ellipsizeMiddle(headFull),
    tail: ellipsizeMiddle(tailFull),
    headFull,
    tailFull,
  }
}
