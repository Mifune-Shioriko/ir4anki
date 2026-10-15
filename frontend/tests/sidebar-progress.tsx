import { render } from 'solid-js/web'
import { createSignal } from 'solid-js'
import '../src/index.css'
import { NavRail, type Section } from '../src/components/NavRail'
render(() => {
  const [section, setSection] = createSignal<Section>('study')
  const [count, setCount] = createSignal(7)
  return <div class="app-shell">
    <NavRail section={section()} onNavigate={setSection} readingListSize={count()}
      onManualSync={() => {}} ring={{reading:{done:2,total:7},review:{done:3,total:9}}} />
    <main class="app"><p data-testid="section">{section()}</p>
      <button onClick={() => setCount(12)}>Change fixture count</button></main>
  </div>
}, document.getElementById('app')!)
