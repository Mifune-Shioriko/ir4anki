// Mirror of lib/cloze.ts clozeMdToHtml (post-2026-09-20 markers-first order),
// run against REAL Anki: table + bold + math clozes (both natural and
// pathological forms). Net-zero: probe note deleted at the end.
import MarkdownIt from 'markdown-it'
import katexPlugin from '@traptitech/markdown-it-katex'

const md = new MarkdownIt({ html: false, linkify: true, breaks: false }).use(katexPlugin, { throwOnError: false })
const mdInline = new MarkdownIt({ html: false, linkify: true, breaks: false })
const CLOZE_RE = /\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g
const SENT_OPEN = '\uE000', SENT_CLOSE = '\uE001'
const RESTORE_RE = /\uE000(\d+)\uE001/g

function mathToAnkiDelims(s) {
  return s.replace(/\$\$([\s\S]+?)\$\$/g, '\\[$1\\]').replace(/\$([^$\n]+?)\$/g, '\\($1\\)')
}

function rerenderClozeMarker(m) {
  CLOZE_RE.lastIndex = 0
  const g = CLOZE_RE.exec(m)
  if (!g) return m
  const content = mathToAnkiDelims(mdInline.renderInline(g[2] ?? ''))
  const hint = g[3]
  return `{{c${g[1]}::${content}${hint !== undefined ? '::' + mathToAnkiDelims(mdInline.renderInline(hint)) : ''}}}`
}

function clozeMdToHtml(text) {
  const stash = []
  const P = s => { stash.push(s); return SENT_OPEN + (stash.length - 1) + SENT_CLOSE }
  let t = text.replace(CLOZE_RE, m => P(rerenderClozeMarker(m)))
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => P(`\\[${tex}\\]`))
  t = t.replace(/\$([^$\n]+?)\$/g, (_, tex) => P(`\\(${tex}\\)`))
  let out = md.render(t)
  for (let i = 0; i < 10 && out.includes(SENT_OPEN); i++) {
    out = out.replace(RESTORE_RE, (_, j) => stash[Number(j)] ?? '')
  }
  return out
}

const CHUNK = `#### 腹膜形成的结构

| 结构 | 位置 | 临床意义 |
|---|---|---|
| 肝十二指肠韧带 | 肝门与十二指肠间 | 内含{{c1::肝固有动脉}}、胆总管 |
| 大网膜 | 胃大弯下垂 | {{c2::防御}}作用 |

肌皮神经来自 \${{c3::C_{5,6}}}$，支配**{{c4::喙肱肌}}**。

正中神经 \${{c5::C_6}}$~T1；自然写法 {{c6::$C_{5,6}$}}。`

const field = clozeMdToHtml(CHUNK)
console.log('===== STORED HTML (文字 field) =====')
console.log(field)
console.log('markers present:', (field.match(/\{\{c\d+::/g) || []).length, '(expect 6)')
console.log('has <table>:', field.includes('<table>'))
console.log('natural math cloze \\(C_{5,6}\\):', field.includes('{{c6::\\(C_{5,6}\\)}}'))
console.log('no sentinel leftover:', !field.includes(SENT_OPEN))

function anki(action, params) {
  return fetch('http://127.0.0.1:8765', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, version: 6, params }),
  }).then(r => r.json()).then(d => { if (d.error) throw new Error(`${action}: ${d.error}`); return d.result })
}

const nids = await anki('addNotes', { notes: [{
  deckName: '2026', modelName: '填空题',
  fields: { '文字': field, '背面额外': '' }, tags: ['md-cloze-probe'],
  options: { allowDuplicate: true },
}] })
const nid = Array.isArray(nids) ? nids[0] : nids
console.log('\nprobe note:', nid)
try {
  const cids = await anki('findCards', { query: `nid:${nid}` })
  console.log('cards (expect 6 — one per c1..c6):', cids.length)
  const strip = h => h.replace(/<style>[\s\S]*?<\/style>/g, '').replace(/<br\s*\/?>$/g, '').trim()
  let bad = 0
  for (const c of await anki('cardsInfo', { cards: cids })) {
    if (!c.cardId) continue
    const q = strip(c.question)
    const ok = !q.includes('No cloze') && q.includes('<table>') && q.includes('data-ordinal')
    if (!ok) { bad++; console.log('BAD CARD', c.cardId, q.slice(0, 200)) }
    const active = q.match(/data-cloze="([^"]+)" data-ordinal="(\d+)"/)
    console.log(`card ordinal ${active ? active[2] : '?'} front: cloze="${active ? active[1] : '—'}" table=${q.includes('<table>')} mathJax=${q.includes('\\(')}`)
  }
  console.log(bad === 0 ? '\nALL CARDS RENDER CORRECTLY' : `\n${bad} BAD CARDS`)
  // show one full front for eyeballing
  const one = (await anki('cardsInfo', { cards: [cids[2]] }))[0]
  console.log('\nfull FRONT of c3 card (math cloze):')
  console.log(strip(one.question).slice(0, 700))
} finally {
  await anki('deleteNotes', { notes: [nid] })
  await anki('sync')
  console.log('\nprobe note deleted + synced (net-zero)')
  console.log('remaining 填空题 cards:', await anki('findCards', { query: 'note:填空题' }))
  console.log('remaining md-cloze-probe notes:', await anki('findNotes', { query: 'tag:md-cloze-probe' }))
}
