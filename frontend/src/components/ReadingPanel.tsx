import { Component } from 'solid-js'
import { api } from '../api'
import type { ReadingChunk } from '../types'
import { FileViewer } from './FileViewer'

// Reading right column (渐进制卡, user spec 2026-09-19): the WHOLE source
// file behind the current chunk, anchored + flashed at the chunk's section —
// "右边回溯到整个笔记的对应部分，这样还能查看一下上下文". Flat panel on the
// tinted .note-column (card shell dropped 2026-09-27); rendering lives in
// the shared FileViewer. Corpus-direct fetch (/api/reading/file), so it
// works even when the notes-rag service is down.

interface Props {
  chunk: ReadingChunk
  /** bump to force the whole-file viewer to re-fetch (in-app segment edit
   *  rewrote the .md; the module cache + a stable path would serve stale
   *  text otherwise). Paired with invalidateFileCache() in App. */
  reloadToken?: unknown
}

export const ReadingPanel: Component<Props> = (props) => {
  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} · ${heads}` : file
  }

  return (
    <div class="note-panel">
      <div class="note-panel-header">
        <span class="note-panel-title md-typescale-title-small">笔记</span>
      </div>
      <div class="note-crumb md-typescale-label-small">{crumb()}</div>
      <FileViewer
        path={props.chunk.path}
        anchorLine={props.chunk.line_start}
        anchorEndLine={props.chunk.line_end}
        anchorToken={props.chunk.chunk_key}
        reloadToken={props.reloadToken}
        fetchFile={api.readingFile}
        class="note-body md-typescale-body-medium"
      />
    </div>
  )
}
