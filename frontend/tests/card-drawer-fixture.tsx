// Synthetic source and intercepted APIs only; never connect this fixture to Anki.
import { render } from 'solid-js/web'
import { createSignal, Show } from 'solid-js'
import { syncThemeColor } from '../src/theme'
import '../src/md-components'
import '../src/index.css'
import '../src/study.css'
import { LectureReadingCard } from '../src/components/LectureReadingCard'
import type { PageSource } from '../src/lib/lecture-api'
import { SegEditDialog } from '../src/components/SegEditDialog'
import { EditDialog } from '../src/components/EditDialog'
import { ClozeDialog } from '../src/components/ClozeDialog'
import { ReadingCard } from '../src/components/ReadingCard'
import { NavRail } from '../src/components/NavRail'
import type { ReadingChunk } from '../src/types'

syncThemeColor()
const [mode, setMode] = createSignal('')
const [sourceClicks, setSourceClicks] = createSignal(0)
const close = () => setMode('')
const lecture = new URLSearchParams(location.search).has('lecture')
const source: PageSource = {kind:'pdf',document_id:'synthetic',revision_id:'r1',segment_id:'seg1',page_start:1,page_end:2,version:1,path:'synthetic.pdf',chunk_key:'seg1',status:'active'}
const chunk: ReadingChunk = {
  path: 'synthetic.md', chunk_key: 'synthetic', seg_id: 1, parent_seg_id: null,
  title: '不被编辑器遮挡的原文', heading_path: [], line_start: 1, line_end: 100,
  text: '# 左侧原文\n\n' + Array.from({ length: 80 }, (_, i) => `原文段落 ${i}：编辑时仍然可以阅读和选择。`).join('\n\n'),
  status: 'active', cards_created: [], ancestor_cards: [], file_chunks: 1, file_done: 0, file_skipped: 0,
}
render(() => <div class="app-shell app-shell--study">
  <NavRail section="study" onNavigate={() => {}} readingListSize={1} />
  <main class="app"><div class="columns"><div class="content">
    <div style={{ display: 'flex', 'flex-wrap': 'wrap', gap: '4px' }}>
      <button id="open-add" onClick={() => setMode('add')}>添加</button>
      <button id="open-edit" onClick={() => setMode('edit')}>编辑</button>
      <button id="open-cloze" onClick={() => setMode('cloze')}>挖空</button>
      <button id="open-segment" onClick={() => setMode('segment')}>编辑当前片段</button>
      <button id="source-action" onClick={() => setSourceClicks(v => v + 1)}>原文操作 {sourceClicks()}</button>
    </div>
    <Show when={lecture} fallback={<ReadingCard chunk={chunk} busy={false} onComplete={() => {}} onNext={() => {}} onSkip={() => {}}
      onAdd={() => setMode('add')} onCloze={() => setMode('cloze')} onSplit={() => {}} onEdit={() => {}} onExit={() => {}} />}>
      <LectureReadingCard source={source} busy={false} onComplete={() => {}} onNext={() => {}} onSkip={() => {}} onExit={() => {}} onChanged={() => {}} />
    </Show>
  </div><aside class="note-column" data-testid="supporting-pane">原有辅助栏</aside></div></main>
  <Show when={mode() === 'add' || mode() === 'edit'}>
    <EditDialog mode={mode() === 'add' ? 'add' : 'edit'} cardId={1} onClose={close} />
  </Show>
  <Show when={mode() === 'segment'}><SegEditDialog chunk={chunk} onClose={close} onSaved={close} /></Show>
  <Show when={mode() === 'cloze'}><ClozeDialog initialText="{{c1::要记忆的知识}}" onClose={close} /></Show>
</div>, document.getElementById('root')!)
