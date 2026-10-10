import { renderMarkdown } from './markdown'
import { cleanCardHtml } from './clean'

export const MARKDOWN_TAG = 'ir4anki::markdown'
export const CLOZE_RE = /\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g
export interface ClozeRange { start: number; end: number; content: string; n: number; hint?: string }
// Balance braces so TeX groups and nested clozes do not prematurely close a marker.
export function clozeRanges(text: string): ClozeRange[] {
 const out: ClozeRange[] = []
 const open = /\{\{c(\d+)::/g
 let m: RegExpExecArray | null
 while ((m = open.exec(text))) {
  let depth = 0, end = -1, hintAt = -1
  for (let i = open.lastIndex; i < text.length; i++) {
   if (text[i] === '{') depth++
   else if (text[i] === '}') {
    if (depth) depth--
    else if (text[i+1] === '}') { end = i; break }
   } else if (!depth && hintAt < 0 && text.slice(i,i+2) === '::') { hintAt = i; i++ }
  }
  if (end < 0) continue
  out.push({start:m.index,end:end+2,n:Number(m[1]),content:text.slice(open.lastIndex,hintAt<0?end:hintAt),hint:hintAt<0?undefined:text.slice(hintAt+2,end)})
  open.lastIndex = end+2
 }
 return out
}
export function isMarkdownNote(tags: string[] = []): boolean { return tags.includes(MARKDOWN_TAG) }
export function renderField(field: string, tags: string[] = []): string {
 return isMarkdownNote(tags) ? renderMarkdown(field) : cleanCardHtml(field)
}
function replaceClozes(text: string, mode: 'q'|'a', ordinal?: number, markers?: [string,string], blockMarkers?: [string,string]): string {
 const ranges = clozeRanges(text)
 let out = '', pos = 0
 for (const r of ranges) {
  const active = ordinal === undefined || r.n === ordinal
  // Replace before Markdown parsing, including link targets: hidden answers
  // can never remain in a URL, image alt, code fence, or attribute.
  const value = active && mode === 'q' ? '[' + replaceClozes(r.hint ?? '…',mode,ordinal,markers,blockMarkers) + ']' : replaceClozes(r.content,mode,ordinal,markers,blockMarkers)
  const standalone = (r.start === 0 || text[r.start-1] === '\n') && (r.end === text.length || text[r.end] === '\n')
  // Standalone multiline clozes must leave heading/list/table/fence delimiters intact.
  const marked = active && markers
    ? blockMarkers && standalone && (value.includes('\n') || /^(?:#{1,6}\s|>\s?|[-+*]\s|\d+[.)]\s|`{3}|~{3}|\|| {4}|\t)/.test(value))
      ? blockMarkers[0] + '\n\n' + value + '\n\n' + blockMarkers[1]
      : markers[0] + value + markers[1]
    : value
  out += text.slice(pos,r.start) + marked
  pos = r.end
 }
 return out + text.slice(pos)
}
export function renderClozeMd(text: string, mode: 'q'|'a', ordinal?: number): string {
 let prefix = 'IR4ANKICLOZEMARK'
 while (text.includes(prefix)) prefix += 'X'
 const open = prefix + 'OPEN', close = prefix + 'CLOSE'
 // Add markup only in rendered text, never inside URL/alt attributes.
 const blockOpen = prefix + 'BLOCKOPEN', blockClose = prefix + 'BLOCKCLOSE'
 const html = renderMarkdown(replaceClozes(text,mode,ordinal,[open,close],[blockOpen,blockClose]))
  .replace(new RegExp('<p[^>]*>' + blockOpen + '</p>\\n?', 'g'), '<div class="cloze">')
  .replace(new RegExp('<p[^>]*>' + blockClose + '</p>\\n?', 'g'), '</div>')
 return html.split(/(<[^>]+>)/g).map(part =>
  part.startsWith('<') ? part.split(open).join('').split(close).join('') :
  part.split(open).join('<span class="cloze">').split(close).join('</span>')
 ).join('')
}
export function renderClozeField(field: string, mode: 'q'|'a', tags: string[] = [], ordinal?: number): string {
 return isMarkdownNote(tags) ? renderClozeMd(field,mode,ordinal) : cleanCardHtml(replaceClozes(field,mode,ordinal,['<span class="cloze">','</span>']))
}

function buildNorm(text: string): { norm: string; map: number[] } {
  let norm = ''
  const map: number[] = []
  for (let k = 0; k < text.length; k++) {
    const ch = text[k]
    if (/[*_`]/.test(ch)) continue // emphasis/code markers — invisible in render
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
  const n = needle.replace(/[*_`]/g, '').replace(/\s+/g, ' ').trim()
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
 * Wrap the selection in {{cN::}} inside the RAW chunk markdown.
 * `block` = rendered text of the block element (li/p/td/h1-6…) that
 * contained the selection — disambiguates WHICH occurrence to wrap when the
 * same word appears several times in the chunk (the selection itself is
 * context-free). Returns null when the selection can't be located.
 */
export function wrapSelectionAsCloze(
  text: string,
  sel: string,
  n = 1,
  block?: string,
): string | null {
  const wrap = (s: number, e: number) => {
    // swallow adjacent emphasis markers so the cloze content stays BALANCED:
    // selecting 髂嵴最高点 = 髂结节 out of **髂嵴最高点** = 髂结节 would
    // otherwise leave the opener outside / closer inside the marker and BOTH
    // sides render as literal ** on the card. Unbalanced leftovers (selection
    // ends mid-emphasis) degrade to visible asterisks in the live preview —
    // user-fixable, not silent.
    while (s > 0 && /[*_`]/.test(text[s - 1])) s--
    while (e < text.length && /[*_`]/.test(text[e])) e++
    return text.slice(0, s) + `{{c${n}::` + text.slice(s, e) + '}}' + text.slice(e)
  }
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
