import katex from 'katex'

// Shared KaTeX helpers (used by Flashcard and PreviewCard).

export const KATEX_OPTS = {
  throwOnError: false,
  delimiters: [
    { left: '$$', right: '$$', display: true },
    { left: '\\(', right: '\\)', display: false },
    { left: '\\[', right: '\\]', display: true },
  ],
}

export const escapeHtml = (s: string) =>
  s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)

/** Escape plain text but render embedded \(…\)/$$…$$ math with KaTeX. */
export function textWithMath(text: string): string {
  if (!text) return ''
  const re = /(\\\(([\s\S]*?)\\\)|\$\$([\s\S]*?)\$\$)/g
  const parts: string[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    parts.push(escapeHtml(text.slice(last, m.index)))
    const math = m[2] ?? m[3] ?? ''
    try {
      parts.push(
        katex.renderToString(math, { throwOnError: false, displayMode: m[3] !== undefined })
      )
    } catch {
      parts.push(escapeHtml(m[0]))
    }
    last = m.index + m[0].length
  }
  parts.push(escapeHtml(text.slice(last)))
  return parts.join('')
}
