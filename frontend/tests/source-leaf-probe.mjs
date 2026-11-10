import {createEffect,onCleanup} from 'solid-js'
import {createElement} from './solid-lifecycle-renderer.mjs'
export const events=[]
export function PdfReferenceViewer(props){
 const host=createElement('pdf-leaf');host.props.manualPosition=0
 createEffect(()=>{const s=props.source;host.props.kind=s.kind;host.props.path=s.path;host.props.document=s.document_id;host.props.revision=s.revision_id;host.props.segment=s.segment_id})
 onCleanup(()=>{const s=props.source;events.push({kind:s.kind,path:s.path,document:s.document_id,disposed:host})})
 return host
}
export function FileViewer(props){
 const host=createElement('md-leaf')
 createEffect(()=>{host.props.path=props.path;props.anchorLine;props.anchorEndLine;props.anchorToken})
 onCleanup(()=>{events.push({path:props.path,disposed:host})})
 return host
}
