import { render } from 'solid-js/web'
import {createSignal, Show} from 'solid-js'
import '../src/md-components'
import '../src/index.css'
import {syncThemeColor} from '../src/theme'
import {NavRail} from '../src/components/NavRail'
import {ManualSyncDialog} from '../src/components/ManualSyncDialog'
syncThemeColor()
render(()=>{const [open,setOpen]=createSignal(false);return <>
 <NavRail section="study" onNavigate={()=>{}} onManualSync={()=>setOpen(true)} />
 <Show when={open()}><ManualSyncDialog onClose={()=>setOpen(false)} /></Show>
 </>},document.getElementById('app')!)
