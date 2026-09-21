import { Component } from 'solid-js'
import { api } from '../api'
import type { ReadingChunk } from '../types'
import { FileViewer } from './FileViewer'

// Reading right column (渐进制卡, user spec 2026-09-19): the WHOLE source
// file behind the current chunk, anchored + flashed at the chunk's section —
// "右边回溯到整个笔记的对应部分，这样还能查看一下上下文". Same elevated-card
// shell and typography as NotePanel; rendering lives in the shared
// FileViewer. Corpus-direct fetch (/api/reading/file), so it works even when
// the notes-rag service is down.

interface Props {
  chunk: ReadingChunk
}

export const ReadingPanel: Component<Props> = (props) => {
  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} · ${heads}` : file
  }

  return (
    <md-elevated-card class="note-panel">
      <div class="note-panel-inner">
        <div class="note-panel-header">
          <span class="note-panel-title md-typescale-title-small">原文上下文</span>
        </div>
        <div class="note-crumb md-typescale-label-small">{crumb()}</div>
        <FileViewer
          path={props.chunk.path}
          anchorLine={props.chunk.line_start}
          anchorEndLine={props.chunk.line_end}
          anchorToken={props.chunk.chunk_key}
          fetchFile={api.readingFile}
          class="note-body md-typescale-body-medium"
        />
      </div>
    </md-elevated-card>
  )
}
