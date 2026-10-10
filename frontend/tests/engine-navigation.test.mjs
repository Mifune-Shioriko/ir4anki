import assert from 'node:assert/strict'
import { readFile, access } from 'node:fs/promises'
import test from 'node:test'

const source = path => readFile(new URL(path, import.meta.url), 'utf8')

test('navigation exposes only study, files and reading list', async () => {
  const nav = await source('../src/components/NavRail.tsx')
  assert.deepEqual([...nav.matchAll(/key: '([^']+)', label:/g)].map(m => m[1]),
    ['study', 'files', 'readingList'])
  assert.match(nav, /export type Section = 'study' \| 'files' \| 'readingList'\s*\n/)
  assert.doesNotMatch(nav, /engine|引擎管理|settings|设置/i)
})

test('App has no engine management import or render, preserving native study wiring', async () => {
  const app = await source('../src/App.tsx')
  assert.doesNotMatch(app, /EnginePanel|section\(\) === 'engine'/)
  const rail = app.match(/<NavRail\b[\s\S]*?\/>/)[0]
  assert.doesNotMatch(rail, /nativeEngine/)
  assert.match(app, /const \[nativeEngine, setNativeEngine\] = createSignal\(false\)/)
  assert.match(app, /api\.backendStatus\(\)/)
  assert.match(app, /nativeEngine=\{nativeEngine\(\)\}/)
  assert.match(app, /nextIntervals=\{nativeEngine\(\) \? currentCard\(\)\?\.next_intervals : null\}/)
})

test('unused engine management component and styles are removed', async () => {
  await assert.rejects(access(new URL('../src/components/EnginePanel.tsx', import.meta.url)), { code: 'ENOENT' })
  assert.doesNotMatch(await source('../src/index.css'), /\.engine-(?:panel|counts|bars)\b/)
})
