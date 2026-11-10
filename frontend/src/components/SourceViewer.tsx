import {Show,type ComponentProps} from 'solid-js'
import {FileViewer} from './FileViewer'
import {PdfReferenceViewer} from './PdfReferenceViewer'
import type {PageSource} from '../lib/lecture-api'
export function SourceViewer(props:ComponentProps<typeof FileViewer>&{source?:PageSource}){
 return <Show when={props.source?.kind==='pdf'} fallback={<FileViewer {...props}/>}><PdfReferenceViewer source={props.source!} active={props.active}/></Show>
}
