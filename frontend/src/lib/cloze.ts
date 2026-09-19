import { textWithMath } from './math'

// Anki-native cloze marker parsing, shared by ClozeDialog (editor preview)
// and ReadingCard (本片段已制卡片 list). Markers: {{cN::content::hint}}.
export const CLOZE_RE = /\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g

export interface ClozeRange { start: number; end: number; content: string; n: number }

export function clozeRanges(text: string): ClozeRange[] {
  const out: ClozeRange[] = []
  CLOZE_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = CLOZE_RE.exec(text))) {
    out.push({ start: m.index, end: m.index + m[0].length, content: m[2] ?? '', n: parseInt(m[1]) })
  }
  return out
}

/** mode 'q' = 正面 (cloze hidden as […]/hint), 'a' = 背面 (content shown). */
export function renderCloze(text: string, mode: 'q' | 'a'): string {
  let out = ''
  let last = 0
  CLOZE_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = CLOZE_RE.exec(text))) {
    out += textWithMath(text.slice(last, m.index))
    const content = m[2] ?? ''
    const hint = m[3]
    out += mode === 'q'
      ? `<span class="cloze-q">[${hint ? textWithMath(hint) : '…'}]</span>`
      : `<span class="cloze-a">${textWithMath(content)}</span>`
    last = m.index + m[0].length
  }
  out += textWithMath(text.slice(last))
  return out
}

// ---- chunk text → cloze seed (user fix 2026-09-20) -------------------------
// 添加挖空 used to seed the dialog with JUST the selection (`{{c1::选中}}`),
// dropping all surrounding context — the card front became a bare "[…]"
// with nothing to recall from. The seed is now the WHOLE chunk converted
// from markdown to plain text (Anki renders card fields literally, so ** /
// ## would show up as junk) with the selection auto-wrapped in {{c1::…}}
// IN PLACE, keeping its sentence context.

export function mdToClozeText(md: string): string {
  return md
    .split('\n')
    .map(line =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/, '')   // ATX heading markers
        .replace(/^\s{0,3}>\s?/, '')         // blockquote markers
        .replace(/^\s*[-*+]\s+/, '')         // unordered list markers
        .replace(/^\s*\d+[.、)]\s+/, '')     // ordered list markers
        .replace(/\*\*([^*]+)\*\*/g, '$1')   // bold **x**
        .replace(/__([^_]+)__/g, '$1'),      // bold __x__
    )
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Whitespace-insensitive search; returns raw [start, end) or null. */
function buildNorm(text: string): { norm: string; map: number[] } {
  let norm = ''
  const map: number[] = []
  for (let k = 0; k < text.length; k++) {
    const ch = text[k]
    if (/\s/.test(ch)) {
      // collapse whitespace runs to a single space (like rendered HTML)
      if (norm.length && !norm.endsWith(' ')) {
        norm += ' '
        map.push(k)
      }
    } else {
      norm += ch
      map.push(k)
    }
  }
  return { norm, map }
}

function normFind(text: string, needle: string): [number, number] | null {
  const n = needle.replace(/\s+/g, ' ').trim()
  if (!n) return null
  const { norm, map } = buildNorm(text)
  const j = norm.indexOf(n)
  if (j < 0) return null
  const start = map[j]
  const lastIdx = j + n.length - 1
  const end = lastIdx < map.length ? map[lastIdx] + 1 : text.length
  return [start, end]
}

/**
 * Wrap the selection in {{cN::}} inside `text` (the converted chunk).
 * `block` = rendered text of the block element (li/p/heading…) that
 * contained the selection — disambiguates WHICH occurrence to wrap when
 * the same word appears several times in the chunk (the selection itself
 * is context-free). Returns null when the selection can't be located.
 */
export function wrapSelectionAsCloze(
  text: string,
  sel: string,
  n = 1,
  block?: string,
): string | null {
  const wrap = (s: number, e: number) =>
    text.slice(0, s) + `{{c${n}::` + text.slice(s, e) + '}}' + text.slice(e)
  if (block) {
    const br = normFind(text, block)
    if (br) {
      const inner = text.slice(br[0], br[1])
      const sr = normFind(inner, sel)
      if (sr) return wrap(br[0] + sr[0], br[0] + sr[1])
    }
  }
  const sr = normFind(text, sel)
  return sr ? wrap(sr[0], sr[1]) : null
}
