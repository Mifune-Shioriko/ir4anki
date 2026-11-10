import { render } from 'solid-js/web'
import { createSignal, Show } from 'solid-js'
import { syncThemeColor } from '../src/theme'
import '../src/index.css'
import { NavRail, type Section } from '../src/components/NavRail'
import { ReadingListScreen } from '../src/components/ReadingListScreen'
import { FilesScreen } from '../src/components/FilesScreen'

syncThemeColor()
const [section, setSection] = createSignal<Section>('readingList')
const [busy, setBusy] = createSignal(false)
const [changes, setChanges] = createSignal(0)
Object.assign(window, { fixture: { setBusy, changes } })
render(() => <div class="app-shell">
  <NavRail section={section()} onNavigate={setSection} />
  <main class="app">
    <Show when={section() === 'readingList'}>
      <div class="content reading-list-page">
        <ReadingListScreen busy={busy()} onListChange={() => setChanges(n => n + 1)} />
      </div>
    </Show>
    <Show when={section() === 'files'}>
      <div class="content files-page"><FilesScreen /></div>
    </Show>
  </main>
</div>, document.getElementById('root')!)
