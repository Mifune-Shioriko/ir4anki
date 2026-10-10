// Browser-only fixture: API requests are intercepted by the regression runner.
import { render } from 'solid-js/web'
import { syncThemeColor } from '../src/theme'
import '../src/md-components'
import '../src/index.css'
import { EditDialog } from '../src/components/EditDialog'
import { ClozeDialog } from '../src/components/ClozeDialog'
syncThemeColor()
const mode = new URLSearchParams(location.search).get('mode')
render(() => mode === 'cloze'
 ? <ClozeDialog initialText="" onClose={() => {}} />
 : <EditDialog mode={mode === 'add' ? 'add' : 'edit'} cardId={1} onClose={() => {}} />, document.getElementById('app')!)
