import MarkdownIt from 'markdown-it'
// @traptitech/markdown-it-katex: $…$ inline + $$…$$ block math, the same
// delimiters the notes corpus uses. Code-fence content is NOT math-parsed
// (verified against the bash-heavy Helium note), so `$MOUNT` in shell
// snippets survives.
import katexPlugin from '@traptitech/markdown-it-katex'

const md = new MarkdownIt({
  html: false, // notes are trusted-ish, but rendering raw HTML buys nothing
  linkify: true,
  breaks: false,
}).use(katexPlugin, { throwOnError: false })

// Tag EVERY block-level element with its SOURCE line range (markdown-it
// tokens carry `map = [startLine, endLine]`, 0-based, endLine EXCLUSIVE).
//   data-src-line = map[0] + 1   (1-based first line)
//   data-src-end  = map[1]       (1-based inclusive last line)
//
// Two consumers:
//  1. FileViewer / NotePanel scroll+flash — the panel scrolls to
//     [data-src-line] == chunk.line_start and flashes every block whose
//     start falls inside the segment range. Headings start exactly at their
//     line, so this stays an exact anchor; body blocks now anchor too, so a
//     split child starting mid-section snaps to its own paragraph instead of
//     the nearest heading above (design decision 2026-09-17, refined
//     2026-09-21 for 分割文段).
//  2. 分割文段 (segment split, round 4): the reading card maps a text
//     selection back to source line numbers by walking up to the nearest
//     [data-src-line]/[data-src-end] block, then POSTs the range to
//     /api/reading/split. Without per-block tagging a selection could not be
//     turned into the line range the split API needs.
//
// renderToken is the documented extension point that renders every block
// open/close token (headings included), so one override replaces the old
// heading-only rule. The @types signature is stale (missing env/self) —
// cast through unknown.
type TokenLike = { map: [number, number] | null; nesting: number; attrSet: (k: string, v: string) => void }
const baseRenderToken = md.renderer.renderToken.bind(md.renderer) as unknown as (
  tokens: TokenLike[], idx: number, options: unknown, env: unknown, self: unknown,
) => string
md.renderer.renderToken = ((tokens: TokenLike[], idx: number, options: unknown, env: unknown, self: unknown) => {
  const t = tokens[idx]
  if (t.map && t.nesting === 1) {
    t.attrSet('data-src-line', String(t.map[0] + 1))
    t.attrSet('data-src-end', String(t.map[1]))
  }
  return baseRenderToken(tokens, idx, options, env, self)
}) as typeof md.renderer.renderToken

export function renderMarkdown(src: string): string {
  return md.render(src)
}
