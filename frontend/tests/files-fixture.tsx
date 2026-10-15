import { startSourceFixture } from 'virtual:app-source-fixture'
import { render } from 'solid-js/web'
import { createSignal, Show } from 'solid-js'
import { syncThemeColor } from '../src/theme'
import '../src/index.css'
import 'katex/dist/katex.min.css'
import '../src/md-components'
import { FilesScreen } from '../src/components/FilesScreen'
import { FileViewer, invalidateFileCache } from '../src/components/FileViewer'
syncThemeColor()
const [mounted, setMounted] = createSignal(true)
const [pair, setPair] = createSignal(false)
const [second, setSecond] = createSignal(true)
const pending: { path: string; signal?: AbortSignal; resolve: (value: { text: string }) => void }[] = []
const delayed = (path: string, signal?: AbortSignal): Promise<{ text: string }> => new Promise(resolve => pending.push({ path, signal, resolve }))
const [path, setPath] = createSignal<string|null>('甲/A.md')
Object.assign(window, { fixture: { startSourceFixture, setMounted, setPair, setSecond, setPath, invalidateFileCache, requests: () => pending.map(p => ({path:p.path,aborted:p.signal?.aborted})), resolve: (index: number, text: string) => pending[index].resolve({text}) } })
render(() => <div style={{padding:'12px', height:'calc(100dvh - 24px)',display:'flex'}}>
  <Show when={mounted()}><FilesScreen /></Show>
  <Show when={pair()}><FileViewer path={path()} anchorLine={1} fetchFile={delayed}/><Show when={second()}><FileViewer path={path()} anchorLine={1} fetchFile={delayed}/></Show></Show>
</div>, document.getElementById('root')!)
