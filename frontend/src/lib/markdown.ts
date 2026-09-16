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

// Tag every heading with its SOURCE line number (markdown-it tokens carry
// `map = [startLine, endLine]`, 0-based). The note panel scrolls to
// [data-src-line] == chunk.line_start — chunks start exactly at their
// heading line, so this is an exact anchor (design decision 2026-09-17).
md.renderer.rules.heading_open = (tokens, idx, options, _env, self) => {
  const t = tokens[idx]
  if (t.map) t.attrSet('data-src-line', String(t.map[0] + 1))
  return self.renderToken(tokens, idx, options, _env)
}

export function renderMarkdown(src: string): string {
  return md.render(src)
}
