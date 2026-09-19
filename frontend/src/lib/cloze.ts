import MarkdownIt from 'markdown-it'
import { renderMarkdown } from './markdown'

// Anki-native cloze marker parsing + markdown-aware rendering.
//
// STORAGE MODEL (user spec 2026-09-20, "卡片也渲染 md 格式，看起来就是直接在
// chunk 上挖空"): Anki fields render HTML natively (verified against the live
// collection: a <table> with {{c1::}} inside a cell renders correctly and
// each cloze ordinal spawns its own card). So the 填空题 文字 field stores
// MARKDOWN-CONVERTED HTML with the {{cN::}} markers left verbatim inside it
// (clozeMdToHtml below) — Anki's cloze engine parses the markers out of the
// HTML itself. The editor textarea keeps the readable MARKDOWN source
// (tables/bold/$math$ visible while editing); conversion happens on save.
//
// Math: $…$/$$…$$ in the textarea become Anki-native \(…\)/\[…\] delimiters
// in the stored HTML (desktop MathJax reads them; the web app's KaTeX
// auto-render KATEX_OPTS includes both delimiters).
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

// ---- markdown → stored HTML (markers protected through the conversion) ----

const SENT_OPEN = '\uE000'
const SENT_CLOSE = '\uE001'
const RESTORE_RE = /\uE000(\d+)\uE001/g

// inline-only renderer for cloze CONTENT: the content sits inside the
// surrounding block structure (table cell, <p>, <li>…), so it must never
// gain block wrappers. DELIBERATELY WITHOUT the KaTeX plugin: math inside a
// cloze must reach the stored field as Anki-native \(…\) (desktop MathJax),
// not browser-only KaTeX HTML — the conversion runs AFTER renderInline so
// markdown-it's escape handling never eats the backslashes (\( → literal
// "(" — bug found in the live round-trip probe 2026-09-20).
const mdInline = new MarkdownIt({ html: false, linkify: true, breaks: false })

/** Convert Obsidian-style math ($…$/$$…$$) inside a string to Anki-native
 * \(…\)/\[…\] delimiters. Used for cloze CONTENT/HINT (the block-level pass
 * in clozeMdToHtml never sees inside a protected marker). */
function mathToAnkiDelims(s: string): string {
  return s
    .replace(/\$\$([\s\S]+?)\$\$/g, '\\[$1\\]')
    .replace(/\$([^$\n]+?)\$/g, '\\($1\\)')
}

/** Rebuild one protected cloze marker with its content inline-rendered
 * ({{c1::**髂结节**}} → {{c1::<strong>髂结节</strong>}}) so markdown the
 * user wrote INSIDE a cloze still renders on the card back. Math inside the
 * content becomes \(…\) FIRST (Anki MathJax; the KaTeX plugin must not eat
 * it). Markers run BEFORE the global math pass in clozeMdToHtml so the
 * natural form {{c1::$x$}} can't produce the }}} sequence that breaks
 * Anki's own marker parser. Unbalanced emphasis markers left over from a
 * selection that straddled ** pairs render as literal asterisks — visible in
 * the live preview, user-fixable. */
function rerenderClozeMarker(m: string): string {
  CLOZE_RE.lastIndex = 0
  const g = CLOZE_RE.exec(m)
  if (!g) return m
  // renderInline FIRST (markdown in the content), math conversion AFTER
  // (the produced \(/\[ backslashes must not pass through markdown-it)
  const content = mathToAnkiDelims(mdInline.renderInline(g[2] ?? ''))
  const hint = g[3]
  return `{{c${g[1]}::${content}${hint !== undefined ? '::' + mathToAnkiDelims(mdInline.renderInline(hint)) : ''}}}`
}

function makeProtector(stash: string[]) {
  return (s: string) => {
    stash.push(s)
    return SENT_OPEN + (stash.length - 1) + SENT_CLOSE
  }
}

function restore(html: string, stash: string[]): string {
  // iterative: a restored value can itself contain sentinels (nested
  // protection — e.g. $…{{c1::x}}…$ math around a marker); loop until no
  // sentinels remain (bounded to avoid a pathological infinite loop)
  let out = html
  for (let i = 0; i < 10 && out.includes(SENT_OPEN); i++) {
    out = out.replace(RESTORE_RE, (_, j) => stash[Number(j)] ?? '')
  }
  return out
}

/**
 * Convert the editor's markdown source to the HTML stored in the Anki
 * field. {{cN::…::hint}} markers pass through VERBATIM (protected by
 * private-use-area sentinels so markdown-it never mangles them — a `*`
 * inside cloze content would otherwise pair across markers). $-math becomes
 * Anki-native \(…\)/\[…\].
 *
 * Known limits (accepted): markers inside a link URL get percent-encoded by
 * markdown-it (don't cloze URLs); block-level content inside a marker may
 * split across <p>s. Both are exotic in the notes corpus.
 */
export function clozeMdToHtml(text: string): string {
  const stash: string[] = []
  const P = makeProtector(stash)
  // 1. cloze markers FIRST (content's own $math$ → \(…\) inside the marker):
  //    a marker inside math (${{c1::x}}$) is pathological — Anki's parser
  //    chokes on the resulting }}} when the math ends with `}`. Protecting
  //    markers first keeps the natural form {{c1::$x$}} unambiguous.
  let t = text.replace(CLOZE_RE, m => P(rerenderClozeMarker(m)))
  // 2. remaining math OUTSIDE markers → Anki-native delimiters ($$…$$ first
  //    so its content isn't eaten by the $…$ pass)
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => P(`\\[${tex}\\]`))
  t = t.replace(/\$([^$\n]+?)\$/g, (_, tex) => P(`\\(${tex}\\)`))
  return restore(renderMarkdown(t), stash)
}

/**
 * Editor/preview renderer: markdown source → HTML with the cloze markers
 * swapped for visual spans. mode 'q' = 正面 ([…]/hint), 'a' = 背面 (content
 * revealed, rendered live so bold/$math$ inside a cloze still work).
 */
export function renderClozeMd(text: string, mode: 'q' | 'a'): string {
  const stash: string[] = []
  const P = makeProtector(stash)
  const t = text.replace(CLOZE_RE, (_m, _n, content: string, hint?: string) =>
    mode === 'q'
      ? P(`<span class="cloze-q">[${hint ? mdInline.renderInline(hint) : '…'}]</span>`)
      : P('<span class="cloze-a">') + (content ?? '') + P('</span>'),
  )
  return restore(renderMarkdown(t), stash)
}

/** Stored fields: new cards hold HTML (clozeMdToHtml), cards made before
 * 2026-09-20 hold plain text/markdown — sniff and take the right path. */
export function isFieldHtml(s: string): boolean {
  return /<[a-z][\s\S]*>/i.test(s)
}

/**
 * Render a STORED 填空题 field for display (已制卡片 list etc.): markers →
 * cloze-q/cloze-a spans. HTML fields swap markers in place (content is
 * already HTML — \(…\) math inside is handled by the caller's KaTeX
 * auto-render effect); legacy plain/md fields go through renderClozeMd.
 */
export function renderClozeField(field: string, mode: 'q' | 'a'): string {
  if (!field) return ''
  if (!isFieldHtml(field)) return renderClozeMd(field, mode)
  CLOZE_RE.lastIndex = 0
  // stored markers hold ALREADY-RENDERED HTML (rerenderClozeMarker ran on
  // save) — inject content/hint raw, escaping would double-encode it
  return field.replace(CLOZE_RE, (_m, _n, content: string, hint?: string) =>
    mode === 'q'
      ? `<span class="cloze-q">[${hint || '…'}]</span>`
      : `<span class="cloze-a">${content ?? ''}</span>`,
  )
}

// ---- chunk text → cloze seed (user fix 2026-09-20) -------------------------
// 添加挖空 seeds the dialog from the WHOLE chunk with the selection wrapped
// in {{c1::…}} IN PLACE (the old selection-only seed produced bare-"[…]"
// cards with no recall context). The seed keeps the chunk's RAW MARKDOWN —
// the field is HTML-converted on save, so tables/bold survive into the card
// (user spec: 卡片看起来就是直接在 chunk 上挖空).

/** Whitespace- and emphasis-marker-insensitive search; returns raw [start,
 * end) or null. `* _ \`` are dropped from BOTH sides (rendered selection
 * text never contains markup asterisks, raw markdown does — "**髂结节**"
 * must match a "髂结节" selection and map back to the RAW positions inside
 * the bold). */
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
