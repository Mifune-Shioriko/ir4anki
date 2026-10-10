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
export function cleanCardHtml(html: string, presentation = true): string {
  const template = document.createElement('template')
  template.innerHTML = stripTemplateBlocks(html)
  template.content.querySelectorAll('script,style,iframe,object,embed,svg,math,link,meta,base,form,input,button').forEach(el => el.remove())
  // Presentation-only removal of retired template widgets. Never change note fields/models.
  // Installed legacy QA template uses these exact enrichment containers.
  if (presentation) template.content.querySelectorAll('#explain-section,#explain-content,#sc,#sl').forEach(el => el.remove())
  if (presentation) template.content.querySelectorAll('[id],[class],[data-stat]').forEach(el => {
    const markers = [el.id, el.className, el.getAttribute('data-stat') ?? ''].join(' ')
    if (/(?:^|[\s_-])(?:anki[-_](?:explain|rag)|fsrs(?:[-_](?:stats|info|state))?|memory[-_]state|stability|difficulty|retention|retrievability|stats[-_]card|ai[-_]explanation)(?:$|[\s_-])/i.test(markers)) el.remove()
  })
  if (presentation) template.content.querySelectorAll('.stats,.stats-grid,.stats-container,.stat-card,.stat-item,.metric-card').forEach(el => {
    if (/(?:稳定性|保持率|记忆保持率|Stability|Retention|Retrievability)/i.test(el.textContent ?? '')) el.remove()
  })
  template.content.querySelectorAll('*').forEach(el => {
    // Legacy templates also use plain metric labels without semantic ids.
    if (presentation && /^(?:稳定性|难度|保持率|记忆保持率|Stability|Difficulty|Retention|Retrievability)\s*[:：]?\s*[\d.]+\s*(?:%|天|days?)?$/i.test((el.textContent ?? '').trim())) {
      el.remove()
      return
    }

    if (presentation && el.classList.contains('cloze') && el instanceof HTMLElement) {
      const style = el.style
      style?.removeProperty('color')
      style?.removeProperty('background-color')
    }
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name) || ['srcdoc', 'srcset'].includes(attr.name)) el.removeAttribute(attr.name)
      else if (attr.name === 'style') {
        const safe = attr.value.split(';').filter(rule => /^(?:font-weight|font-style|text-decoration|color|background-color)\s*:\s*[a-z0-9#(),.%\s-]+$/i.test(rule.trim())).join(';')
        if (safe) el.setAttribute('style',safe); else el.removeAttribute('style')
      }
      else if (['src','href','xlink:href','action'].includes(attr.name) && !/^(?:https?:|\/|#|[^:]+$)/i.test(attr.value.trim())) el.removeAttribute(attr.name)
    }
  })
  return fixMedia(template.innerHTML)
}

/** Authoring must not persist presentation removals into a legacy field. */
export function cleanEditorHtml(html: string): string { return cleanCardHtml(html, false) }
