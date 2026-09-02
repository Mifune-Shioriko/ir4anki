// Cleaning for Anki-rendered card HTML.
//
// AnkiConnect returns fully-rendered templates: the field content wrapped in
// the note type's template, which includes a <style> block (card CSS) and
// <script> blocks (desktop-only fetches to localhost enrichment services).
// In this web app:
//   - scripts inserted via innerHTML never execute anyway, and they point at
//     endpoints that only matter inside the Anki GUI → strip them
//   - the template CSS hardcodes white background/black text and would fight
//     the MD3 theme (and leak raw text if ever shown) → strip it too
//   - media references are bare filenames → rewrite to /media/<name>

export function stripTemplateBlocks(html: string): string {
  if (!html) return ''
  return html
    .replace(/<style\b[\s\S]*?<\/style>/gi, '')
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
}

export function fixMedia(html: string): string {
  if (!html) return ''
  return html.replace(/(src|href)=(["'])([^"']+)\2/gi, (match, attr, quote, value) => {
    if (/^(https?:|data:|\/)/i.test(value)) return match
    return `${attr}=${quote}/media/${value}${quote}`
  })
}

/** Full pipeline for rendering card HTML in this app. */
export function cleanCardHtml(html: string): string {
  return fixMedia(stripTemplateBlocks(html))
}
