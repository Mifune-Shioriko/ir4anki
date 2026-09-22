import { Component, Show, createSignal, onCleanup, onMount } from 'solid-js'
import { api } from '../api'
import { renderMarkdown } from '../lib/markdown'
import type { ReadingChunk } from '../types'
import type { Cm6Handle } from '../lib/cm6-editor'
import {
  IconFormatBold, IconFormatItalic, IconImage, IconGrid,
} from './icons'

// 片段编辑器 (user spec 2026-09-23): 方案2 宽弹窗 — 左栏 CodeMirror 6
// 源码模式编辑，右栏实时预览（debounce 150ms，复用 renderMarkdown：
// KaTeX/GFM 表格/图片路由全部生效）。两栏各自等于三栏布局的一栏宽
// （--content-max-width），总宽 ≈2 栏 + 间距。
//
// CM6 是 LAZY chunk（await import）——主 bundle 零负担，打开弹窗才拉。
// 源码模式而非 WYSIWYG：文档始终是 .md 原文，写回 diff 只有用户敲的
// 行，行号/指纹体系不被序列化规范化破坏（Obsidian 同款内核选择）。
//
// 保存 → POST /api/reading/edit：后端在同一事务里替换行区间、按 delta
// 重锚所有 segments、原子写文件、更新 file_sha —— 不触发 drift 保险丝。
// Mod-Enter = 保存。图片按钮 → /api/reading/media/upload（存 _assets/）
// → 光标处插入 ![](filename)；表格按钮插入 GFM 模板。

interface Props {
  chunk: ReadingChunk
  onClose: () => void
  /** saved: caller replaces the on-screen chunk with the fresh payload */
  onSaved: (chunk: ReadingChunk | null) => void
}

const TABLE_TEMPLATE = '\n| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n|  |  |  |\n'

export const SegEditDialog: Component<Props> = (props) => {
  let editorHostRef: HTMLDivElement | undefined
  let previewRef: HTMLDivElement | undefined
  let imgFileRef: HTMLInputElement | undefined
  let cm: Cm6Handle | null = null
  let previewTimer: ReturnType<typeof setTimeout> | null = null
  let syncing = false

  const [busy, setBusy] = createSignal(true) // CM6 lazy-import in flight
  const [saving, setSaving] = createSignal(false)
  const [error, setError] = createSignal('')
  const [previewHtml, setPreviewHtml] = createSignal('')
  const dark = () => document.documentElement.classList.contains('dark')

  const renderPreview = (text: string) => {
    setPreviewHtml(renderMarkdown(text))
    // keep the preview's scroll position roughly stable across re-renders
    requestAnimationFrame(() => {
      if (!previewRef || syncing) return
      const el = previewRef
      const max = el.scrollHeight - el.clientHeight
      if (max > 0 && el.dataset.frac) {
        el.scrollTop = parseFloat(el.dataset.frac) * max
      }
    })
  }

  const schedulePreview = () => {
    if (previewTimer) clearTimeout(previewTimer)
    previewTimer = setTimeout(() => {
      if (cm) renderPreview(cm.getText())
    }, 150)
  }

  const syncPreviewScroll = (frac: number) => {
    if (!previewRef) return
    syncing = true
    const el = previewRef
    el.dataset.frac = String(frac)
    const max = el.scrollHeight - el.clientHeight
    if (max > 0) el.scrollTop = frac * max
    // release the guard after the rAF restore above settles
    setTimeout(() => { syncing = false }, 0)
  }

  onMount(async () => {
    renderPreview(props.chunk.text)
    try {
      const mod = await import('../lib/cm6-editor')
      if (!editorHostRef) return
      cm = mod.createMdEditor(editorHostRef, {
        initial: props.chunk.text,
        dark: dark(),
        onUpdate: schedulePreview,
        onScroll: syncPreviewScroll,
        onSave: () => { void save() },
      })
      cm.focus()
      setBusy(false)
    } catch (e) {
      setError('编辑器加载失败：' + (e as Error).message)
      setBusy(false)
    }
  })

  onCleanup(() => {
    if (previewTimer) clearTimeout(previewTimer)
    cm?.destroy()
    cm = null
  })

  // ---- toolbar ----
  const insertTable = () => cm?.insertAtCursor(TABLE_TEMPLATE)
  const bold = () => cm?.wrapSelection('**')
  const italic = () => cm?.wrapSelection('*')

  const onImgPicked = async (e: Event) => {
    const input = e.currentTarget as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || !cm) return
    setBusy(true)
    try {
      const { filename } = await api.readingMediaUpload(file)
      cm.insertAtCursor(`\n![](${filename})\n`)
      schedulePreview()
    } catch (err) {
      setError('图片上传失败：' + (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // ---- save ----
  const save = async () => {
    if (!cm || saving()) return
    const text = cm.getText()
    if (text === props.chunk.text) {
      props.onClose()
      return
    }
    if (props.chunk.seg_id == null) {
      setError('该片段没有 seg_id（旧数据），无法保存')
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await api.readingEdit(props.chunk.path, props.chunk.seg_id, text)
      props.onSaved(res.chunk)
    } catch (e) {
      setError('保存失败：' + (e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <md-dialog
      class="seg-edit-dialog"
      open
      onClose={() => props.onClose()}
      ref={el => {
        // keep the default center-scaled dialog motion (NOT the bottom-sheet
        // animation EditDialog uses — this is a wide workstation dialog)
        void el
      }}
    >
      <div slot="headline">编辑片段</div>

      <div slot="content" class="seg-edit-content">
        <Show when={error()}>
          <div class="edit-error md-typescale-body-medium">{error()}</div>
        </Show>

        <div class="seg-edit-toolbar" role="toolbar" aria-label="编辑工具栏">
          <md-icon-button aria-label="加粗 (**)" onPointerDown={e => { e.preventDefault(); bold() }}>
            <md-icon><IconFormatBold /></md-icon>
          </md-icon-button>
          <md-icon-button aria-label="斜体 (*)" onPointerDown={e => { e.preventDefault(); italic() }}>
            <md-icon><IconFormatItalic /></md-icon>
          </md-icon-button>
          <span class="tool-sep" />
          <md-icon-button aria-label="插入表格" onPointerDown={e => { e.preventDefault(); insertTable() }}>
            <md-icon><IconGrid /></md-icon>
          </md-icon-button>
          <md-icon-button aria-label="插入图片" disabled={busy()}
            onPointerDown={e => { e.preventDefault(); imgFileRef?.click() }}>
            <md-icon><IconImage /></md-icon>
          </md-icon-button>
          <input type="file" ref={imgFileRef} accept="image/*" style="display:none" onChange={onImgPicked} />
          <span class="seg-edit-hint md-typescale-label-small">
            Ctrl+Enter 保存 · 图片存入 _assets · 引用用文件名
          </span>
        </div>

        <div class="seg-edit-panes">
          <div class="seg-edit-pane seg-edit-pane--editor">
            <div class="seg-edit-pane-head md-typescale-label-medium">Markdown 源码</div>
            {/* The CM6 host MUST stay an empty div with no reactive
                siblings: Solid's Show teardown would otherwise remove the
                imperatively-mounted .cm-editor together with the loading
                placeholder (debugged 2026-09-23: host emptied after
                setBusy(false)). Loading indicator lives OUTSIDE the host. */}
            <Show when={busy() && !cm}>
              <div class="seg-edit-loading edit-loading md-typescale-body-medium">加载编辑器…</div>
            </Show>
            <div class="seg-edit-editor" ref={editorHostRef} />
          </div>
          <div class="seg-edit-pane seg-edit-pane--preview">
            <div class="seg-edit-pane-head md-typescale-label-medium">实时预览</div>
            <div
              class="seg-edit-preview note-body md-typescale-body-medium"
              ref={previewRef}
              innerHTML={previewHtml()}
            />
          </div>
        </div>
      </div>

      <div slot="actions">
        <md-text-button onClick={() => props.onClose()} disabled={saving()}>取消</md-text-button>
        <md-filled-button onClick={() => void save()} disabled={saving() || busy()}>
          {saving() ? '保存中…' : '保存'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
