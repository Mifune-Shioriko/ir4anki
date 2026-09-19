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
