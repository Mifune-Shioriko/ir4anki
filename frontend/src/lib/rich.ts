// Rich-text editor round-trip helpers for Anki note fields.
//
// Anki stores: media as bare filenames, math as LaTeX delimiters (\(…\)
// inline, $$…$$ / \[…\] display — same convention as Flashcard's KaTeX
// auto-render). The editor shows: media via /media/<name>, math as atomic
// KaTeX-rendered chips. These functions convert between the two.

import katex from 'katex'
import 'katex/dist/katex.min.css'

const MATH_RE = /(\\\(([\s\S]*?)\\\)|\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\])/g

/** Build the atomic math chip shown inside the contenteditable field. */
export function makeMathChip(tex: string, display: boolean): HTMLSpanElement {
  const chip = document.createElement('span')
  chip.className = 'math-tex'
  chip.setAttribute('contenteditable', 'false')
  chip.setAttribute('data-tex', tex)
  chip.setAttribute('data-display', display ? '1' : '0')
  try {
    chip.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: display })
  } catch {
    chip.textContent = tex
  }
  return chip
}

/** True when this field's raw HTML contains any math delimiter. */
export function hasMath(html: string): boolean {
  MATH_RE.lastIndex = 0
  return MATH_RE.test(html || '')
}

/** Replace math delimiters inside one text node with rendered chips. */
function mathifyTextNode(node: Text) {
  const text = node.data
  MATH_RE.lastIndex = 0
  if (!MATH_RE.test(text)) return
  MATH_RE.lastIndex = 0
  const frag = document.createDocumentFragment()
  let last = 0
  let m: RegExpExecArray | null
  while ((m = MATH_RE.exec(text))) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)))
    const tex = m[2] ?? m[3] ?? m[4] ?? ''
    frag.appendChild(makeMathChip(tex, m[3] !== undefined || m[4] !== undefined))
    last = m.index + m[0].length
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)))
  node.replaceWith(frag)
}

/** Anki field HTML -> editor HTML (media URLs + math chips). */
export function toEditorHtml(html: string): string {
  const div = document.createElement('div')
  div.innerHTML = html || ''
  // media: bare filename -> /media/ URL, remember original via data-media
  div.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src') || ''
    if (src && !src.startsWith('http') && !src.startsWith('/media/') && !src.startsWith('data:')) {
      img.setAttribute('data-media', src)
      img.setAttribute('src', '/media/' + (src.split('/').pop() ?? src))
    }
  })
  mathifyField(div)
  return div.innerHTML
}

/** Convert math delimiters in a field's text nodes to chips (skips chips). */
export function mathifyField(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) {
    const n = walker.currentNode as Text
    if ((n.parentElement as HTMLElement | null)?.closest('.math-tex')) continue
    nodes.push(n)
  }
  nodes.forEach(mathifyTextNode)
}

/** Editor HTML -> Anki field HTML (chips -> LaTeX delimiters, /media/ -> bare). */
export function fromEditorHtml(html: string): string {
  const div = document.createElement('div')
  div.innerHTML = html || ''
  div.querySelectorAll('.math-tex').forEach(chip => {
    const tex = chip.getAttribute('data-tex') ?? ''
    const display = chip.getAttribute('data-display') === '1'
    chip.replaceWith(document.createTextNode(display ? `$$${tex}$$` : `\\(${tex}\\)`))
  })
  div.querySelectorAll('[data-media]').forEach(img => {
    img.setAttribute('src', img.getAttribute('data-media') || '')
    img.removeAttribute('data-media')
  })
  return div.innerHTML
}

/** Insert an <img> for an uploaded media file (marked for round-trip). */
export function makeMediaImg(filename: string): HTMLImageElement {
  const img = document.createElement('img')
  img.setAttribute('data-media', filename)
  img.src = '/media/' + filename
  return img
}
