// Run from frontend: PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs \
//   node tests/card-drawer-browser.mjs
// All APIs are intercepted; this runner never connects to the real backend.
import assert from 'node:assert/strict'
import { build, preview } from 'vite'
import { fileURLToPath } from 'node:url'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const configFile = fileURLToPath(new URL('./card-drawer-fixture.config.mjs', import.meta.url))
const outDir = await mkdtemp(join(tmpdir(), 'ir4anki-drawer-fixture-'))
await build({ configFile, logLevel: 'error', build: { outDir } })
const server = await preview({ configFile, build: { outDir }, preview: { host: '127.0.0.1', port: 0 } })
const origin = server.resolvedUrls.local[0]
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH })
const output = process.env.CARD_DRAWER_OUTPUT || '/tmp/ir4anki-card-drawer-browser'
await mkdir(output, { recursive: true })
const results = []

// Two-page synthetic PDF; no user documents or document parser are involved.
function pdf() {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
    '',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
    '', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  for (const [index, label] of [[3, 'Slide one - source stays visible'], [5, 'Slide two - draft still belongs to page one']]) {
    const stream = `BT /F1 24 Tf 40 700 Td (${label}) Tj ET\n`
    objects[index] = `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`
  }
  let result = '%PDF-1.4\n', offsets = [0]
  objects.forEach((value, i) => { offsets.push(Buffer.byteLength(result)); result += `${i + 1} 0 obj\n${value}\nendobj\n` })
  const xref = Buffer.byteLength(result)
  result += `xref\n0 8\n0000000000 65535 f \n${offsets.slice(1).map(n => String(n).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`
  return Buffer.from(result)
}
async function contextFor(width, lecture = false, reducedMotion = 'reduce') {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion })
  const page = await context.newPage(), writes = [], errors = []
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url())
    const json = value => route.fulfill({ json: value })
    if (url.pathname === '/api/card/add/info') return json({ fields: url.searchParams.has('kind') ? ['文字', '补充'] : ['正面', '背面'] })
    if (url.pathname === '/api/tags') return json({ tags: [] })
    if (url.pathname === '/api/note') return json({ fields: { 正面: '原问题', 背面: '原答案' }, tags: ['ir4anki::markdown'] })
    if (['/api/card/add', '/api/note/update'].includes(url.pathname)) {
      writes.push({ path: url.pathname, body: request.postDataJSON() })
      await new Promise(resolve => setTimeout(resolve, 100))
      return json({ noteId: 123, cardIds: [123], question: 'Q', answer: 'A' })
    }
    if (lecture && url.pathname.endsWith('/pdf')) return route.fulfill({ contentType: 'application/pdf', body: pdf() })
    if (lecture && url.pathname.endsWith('/annotation')) return json({ annotation_id: 'annotation', version: 'v1', body: '', markdown_path: 'synthetic.md' })
    throw new Error(`Unexpected fixture request: ${request.method()} ${url.pathname}`)
  })
  await page.goto(`${origin}card-drawer-fixture.html${lecture ? '?lecture=1' : ''}`)
  return { context, page, writes, errors }
}
async function readerGeometry(page) {
  return page.evaluate(() => {
    const source = document.querySelector('.columns > .content')
    const pane = document.querySelector('[data-testid="supporting-pane"]')
    pane.dataset.originalPane = 'yes'
    return { ...source.getBoundingClientRect().toJSON(), pane: {
      box: pane.getBoundingClientRect().toJSON(),
      display: getComputedStyle(pane).display, visibility: getComputedStyle(pane).visibility,
    } }
  })
}
async function geometry(page, width, before) {
  const drawer = page.locator('.card-drawer')
  await drawer.waitFor()
  const result = await page.evaluate(() => {
    const drawer = document.querySelector('.card-drawer'), source = document.querySelector('.columns > .content')
    const d = drawer.getBoundingClientRect(), s = source.getBoundingClientRect()
    return { placement: drawer.dataset.placement, sourceWidth: s.width, sourceX: s.x, layout: [source.parentElement, source].map(el => ({height: getComputedStyle(el).height, maxHeight: getComputedStyle(el).maxHeight, minHeight: getComputedStyle(el).minHeight, box: el.getBoundingClientRect().toJSON()})), drawer: { x: d.x, y: d.y, right: d.right, bottom: d.bottom }, source: { right: s.right, bottom: s.bottom }, scrollWidth: document.documentElement.scrollWidth, viewport: innerWidth, modal: document.querySelector(':modal') !== null, inert: source.closest('[inert]') !== null }
  })
  const pane = await page.locator('[data-testid="supporting-pane"]').evaluate(el => ({
    box: el.getBoundingClientRect().toJSON(), display: getComputedStyle(el).display,
    visibility: getComputedStyle(el).visibility, original: el.dataset.originalPane,
  }))
  assert.equal(pane.original, 'yes', 'supporting pane must stay mounted')
  assert.equal(pane.display, before.pane.display, 'editor must not remove the supporting pane')
  assert.equal(pane.visibility, before.pane.visibility, 'editor must not hide the supporting pane')
  assert.equal(pane.box.width, before.pane.box.width, 'supporting pane width must stay unchanged')
  if (result.placement === 'side' && pane.display !== 'none') {
    assert.deepEqual(pane.box, before.pane.box, 'supporting pane must retain its geometry')
    const overlay = await page.evaluate(() => {
      const drawer = document.querySelector('.card-drawer'), pane = document.querySelector('[data-testid="supporting-pane"]')
      const d = drawer.getBoundingClientRect(), p = pane.getBoundingClientRect()
      const left = Math.max(d.left, p.left), right = Math.min(d.right, p.right)
      const top = Math.max(d.top, p.top), bottom = Math.min(d.bottom, p.bottom)
      return right > left && bottom > top && drawer.contains(document.elementFromPoint((left + right) / 2, (top + bottom) / 2))
    })
    assert.equal(overlay, true, 'editor must paint above the existing supporting pane')
  }
  assert.equal(result.modal, false, 'reader must not sit behind a modal dialog')
  assert.equal(result.inert, false)
  assert.ok(result.scrollWidth <= result.viewport + 1, 'no horizontal page overflow')
  assert.ok(Math.abs(result.sourceWidth - before.width) <= 1, 'opening editor must preserve reader width')
  assert.ok(Math.abs(result.sourceX - before.x) <= 1, 'opening editor must preserve reader position')
  if (result.placement === 'side') assert.ok(result.source.right <= result.drawer.x + 1, JSON.stringify(result))
  else assert.ok(result.source.bottom <= result.drawer.y + 1, JSON.stringify(result))
  return result
}
try {
  for (const width of [1920, 1440, 1280, 1024, 768, 600, 390]) {
    for (const mode of ['add', 'edit', 'cloze']) {
      const { context, page, writes, errors } = await contextFor(width)
      try {
        const before = await readerGeometry(page)
        await page.locator(`#open-${mode}`).click()
        await page.locator('.card-drawer .cm-content').first().waitFor()
        const bounds = await geometry(page, width, before)
        await page.locator('#source-action').click()
        assert.match(await page.locator('#source-action').innerText(), /1/)
        assert.equal(await page.locator('.card-drawer').count(), 1, 'clicking source must not dismiss the draft')
        const sourceScroll = await page.locator('.columns > .content').evaluate(el => {
          const scroll = [...el.querySelectorAll('.reading-chunk-body, .study-scroll'), el].find(node => node.scrollHeight > node.clientHeight)
          if (!scroll) return 0
          scroll.scrollTop = 100
          return scroll.scrollTop
        })
        assert.ok(sourceScroll > 0, 'source can scroll with drawer open')
        // Menu Escape should dismiss only the menu, never the draft.
        const overflow = page.locator('.card-drawer .authoring-overflow').first()
        if (await overflow.isVisible()) {
          await overflow.click()
          await page.keyboard.press('Escape')
          assert.equal(await page.locator('.card-drawer').count(), 1, 'menu Escape must not close editor')
        }
        await page.locator('.card-drawer .cm-content').first().fill(mode === 'cloze' ? '{{c1::新知识}}' : '已编辑的内容')
        await page.screenshot({ path: `${output}/${mode}-${width}.png` })
        await page.keyboard.press('Control+Enter')
        await page.keyboard.press('Control+Enter')
        await page.locator('.card-drawer').waitFor({ state: 'detached' })
        assert.equal(writes.length, 1, 'repeated save shortcut must create only one request')
        assert.equal(writes[0].path, mode === 'edit' ? '/api/note/update' : '/api/card/add')
        assert.equal(Object.values(writes[0].body.fields)[0], mode === 'cloze' ? '{{c1::新知识}}' : '已编辑的内容')
        assert.equal(await page.locator(`#open-${mode}`).evaluate(el => document.activeElement === el), true, 'close restores trigger focus')
        await page.locator(`#open-${mode}`).click()
        await page.locator('.card-drawer .cm-content').first().waitFor()
        await page.locator('.card-drawer .cm-content').first().press('Escape')
        await page.locator('.card-drawer').waitFor({ state: 'detached' })
        assert.equal(writes.length, 1, 'Escape does not save')
        assert.deepEqual(errors, [])
        if (width >= 1180) assert.equal(await page.locator('[data-testid="supporting-pane"]').isVisible(), true, 'supporting pane restores after close')
        results.push({ mode, width, bounds })
      } finally { await context.close() }
    }
  }
  if (process.env.CARD_DRAWER_LECTURE === '1') {
    for (const width of [1920, 1440, 1024, 600, 390]) {
      const { context, page, writes, errors } = await contextFor(width, true)
      try {
        await page.locator('.pdf-source .textLayer').getByText('Slide one', { exact: false }).waitFor()
        const originalCanvas = await page.locator('.pdf-source canvas').evaluate(el => { el.dataset.original = 'yes'; return el.width })
        assert.ok(originalCanvas > 0)
        const before = await readerGeometry(page)
        const pdfBefore = await page.locator('.pdf-source canvas').boundingBox()
        await page.getByRole('button', { name: '添加问答', exact: true }).click()
        await page.locator('.card-drawer .cm-content').first().waitFor()
        const bounds = await geometry(page, width, before)
        const pdfAfter = await page.locator('.pdf-source canvas').boundingBox()
        assert.ok(Math.abs(pdfAfter.width - pdfBefore.width) <= 1, 'PDF must not rescale when opening editor')
        assert.equal(await page.locator('.pdf-source canvas').isVisible(), true)
        assert.equal(await page.locator('.pdf-source canvas').getAttribute('data-original'), 'yes', 'reader must stay mounted')
        await page.locator('.card-drawer .cm-content').first().fill('关于第一页的问题')
        await page.getByRole('button', { name: '下一页', exact: true }).click()
        await page.locator('.pdf-source .textLayer').getByText('Slide two', { exact: false }).waitFor()
        assert.match(await page.locator('.card-drawer').innerText(), /来源第 1 页/)
        assert.equal(await page.locator('.card-drawer .cm-content').first().innerText(), '关于第一页的问题')
        await page.screenshot({ path: `${output}/lecture-${width}.png` })
        await page.locator('.card-drawer .cm-content').first().press('Control+Enter')
        await page.locator('.card-drawer').waitFor({ state: 'detached' })
        assert.equal(writes.length, 1)
        assert.equal(writes[0].body.reading_source.page, 1, 'paging must not silently retarget provenance')
        assert.equal(writes[0].body.fields['正面'], '关于第一页的问题')
        assert.equal(await page.getByRole('spinbutton', { name: '页码', exact: true }).inputValue(), '2', 'closing editor preserves reader page')
        assert.deepEqual(errors, [])
        results.push({ mode: 'lecture', width, bounds })
      } finally { await context.close() }
    }
  }
  // Inspect real CSS animations at deterministic times, not screenshots taken
  // with reduced motion. No sleeps or production requests are needed.
  for (const width of [1440, 600]) {
    for (const reducedMotion of ['no-preference', 'reduce']) {
      const { context, page, writes, errors } = await contextFor(width, false, reducedMotion)
      try {
        const before = await readerGeometry(page)
        await page.locator('#open-add').click()
        await page.locator('.card-drawer .cm-content').first().waitFor()
        const motion = await page.locator('.card-drawer').evaluate(el => {
          const animation = el.getAnimations().find(a => a.animationName?.startsWith('card-drawer-enter'))
          const shadow = getComputedStyle(el).boxShadow
          const scrim = document.querySelector('.card-drawer-scrim')
          const scrimAnimation = scrim.getAnimations()[0]
          const children = [...el.querySelectorAll('.card-drawer-header, .card-drawer-body, .card-drawer-actions')]
          if (!animation) return { shadow, scrimOpacity: Number(getComputedStyle(scrim).opacity), scrimAnimations: scrim.getAnimations().length, childAnimations: children.flatMap(child => child.getAnimations()).length }
          const timing = animation.effect.getTiming()
          animation.pause()
          scrimAnimation.pause()
          const frames = [0, 250, 500].map(time => {
            animation.currentTime = time
            scrimAnimation.currentTime = time
            const style = getComputedStyle(el)
            const source = document.querySelector('.columns > .content').getBoundingClientRect()
            return { y: new DOMMatrixReadOnly(style.transform).m42, opacity: Number(style.opacity), scrimOpacity: Number(getComputedStyle(scrim).opacity), sourceWidth: source.width, sourceX: source.x }
          })
          return { shadow, duration: timing.duration, easing: getComputedStyle(el).animationTimingFunction, placement: el.dataset.placement, frames }
        })
        assert.equal(motion.shadow, 'none', 'match actual segment editor: depth comes from scrim, not an edge shadow')
        if (reducedMotion === 'reduce') {
          assert.equal(motion.frames, undefined, 'reduced motion disables drawer movement')
          assert.equal(motion.childAnimations, 0, 'reduced motion disables content fades too')
          assert.equal(motion.scrimAnimations, 0)
          assert.equal(motion.scrimOpacity, 0.32)
        } else {
          assert.equal(motion.duration, 500, 'same open duration as md-dialog')
          assert.equal(motion.easing, 'cubic-bezier(0.3, 0, 0, 1)', 'same emphasized easing as md-dialog')
          assert.equal(motion.frames[0].y, motion.placement === 'side' ? -50 : 50)
          assert.equal(motion.frames[0].opacity, 0)
          assert.ok(Math.abs(motion.frames[1].y) > 0 && Math.abs(motion.frames[1].y) < 50, 'drawer slides through an intermediate position')
          assert.equal(motion.frames[2].y, 0)
          assert.equal(motion.frames[2].opacity, 1)
          assert.deepEqual(motion.frames.map(frame => frame.scrimOpacity), [0, 0.16, 0.32], 'match md-dialog linear 500ms scrim fade')
          for (const frame of motion.frames) {
            assert.equal(frame.sourceWidth, before.width, 'animation must not resize source')
            assert.equal(frame.sourceX, before.x, 'animation must not move source')
          }
        }
        await geometry(page, width, before)
        await page.locator('.card-drawer .cm-content').first().press('Escape')
        await page.locator('.card-drawer').waitFor({ state: 'detached' })
        assert.deepEqual(writes, [], 'motion tests must not save')
        assert.deepEqual(errors, [])
        results.push({ mode: 'motion', width, reducedMotion, motion })
      } finally { await context.close() }
    }
  }
  // Compare against the actual SegEditDialog, rather than guessed elevation
  // values. All document content remains synthetic and nothing is saved.
  for (const width of [1440, 600]) {
    for (const theme of ['light', 'dark']) {
      const { context, page, writes, errors } = await contextFor(width)
      try {
        await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), theme === 'dark')
        await page.locator('#open-segment').click()
        await page.locator('.seg-edit-editor .cm-content').waitFor()
        const reference = await page.locator('md-dialog.seg-edit-dialog').evaluate(async el => {
          await el.updateComplete
          await Promise.all(el.shadowRoot.getAnimations({ subtree: true }).map(a => a.finished.catch(() => {})))
          const scrim = getComputedStyle(el.shadowRoot.querySelector('.scrim'))
          return { background: scrim.backgroundColor, opacity: scrim.opacity,
            shadow: getComputedStyle(el.shadowRoot.querySelector('dialog')).boxShadow }
        })
        await page.screenshot({ path: `${output}/segment-reference-${theme}-${width}.png` })
        await page.locator('.seg-edit-editor .cm-content').press('Escape')
        await page.locator('md-dialog.seg-edit-dialog').waitFor({ state: 'detached' })
        const before = await readerGeometry(page)
        await page.locator('#open-add').click()
        await page.locator('.card-drawer .cm-content').first().waitFor()
        const actual = await page.locator('.card-drawer-scrim').evaluate(el => {
          const scrim = getComputedStyle(el)
          return { background: scrim.backgroundColor, opacity: scrim.opacity,
            shadow: getComputedStyle(document.querySelector('.card-drawer')).boxShadow }
        })
        assert.deepEqual(actual, reference, 'same visual depth as actual current-segment editor')
        assert.equal(await page.locator('.card-drawer-scrim').evaluate(el => getComputedStyle(el).pointerEvents), 'none')
        await geometry(page, width, before)
        await page.locator('#source-action').click()
        assert.match(await page.locator('#source-action').innerText(), /1/)
        assert.equal(await page.locator('.card-drawer').count(), 1, 'visual scrim must not dismiss the draft')
        await page.screenshot({ path: `${output}/scrim-${theme}-${width}.png` })
        await page.locator('.card-drawer .cm-content').first().press('Escape')
        await page.locator('.card-drawer-scrim').waitFor({ state: 'detached' })
        assert.deepEqual(writes, [])
        assert.deepEqual(errors, [])
        results.push({ mode: 'segment-depth-parity', width, theme, reference, actual })
      } finally { await context.close() }
    }
  }
  console.log(JSON.stringify({ passed: results.length, cases: results, screenshots: output }, null, 2))
} finally {
  await browser.close()
  await new Promise(resolve => server.httpServer.close(resolve))
  await rm(outDir, { recursive: true, force: true })
}
