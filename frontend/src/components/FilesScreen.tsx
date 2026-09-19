import { Component, For, Show, createSignal, onMount } from 'solid-js'
import { api } from '../api'
import { FileViewer } from './FileViewer'
import { buildTree, allDirPaths } from '../lib/tree'
import type { TreeNode, TreeFile } from '../lib/tree'
import { IconChevronRight, IconDescription, IconFolder, IconRestartAlt } from './icons'

// 文件 section (user spec 2026-09-19 round 3): read-only browse of the
// ~/anki-notes corpus. Left = folder tree navigation (same TreeNode shape
// as the reading-list picker, shared via lib/tree.ts), right = the whole
// file rendered by the shared FileViewer (markdown + KaTeX). No editing —
// the corpus stays git-managed and the app stays read-only (Phase-B
// decision). The list endpoint (/api/files/list) is NOT reading-gated, so
// browsing works with the reading feature flag off too.

interface Props {
  /** reading-list membership: render an 已在清单 badge on listed files */
  listedPaths?: () => Set<string>
}

export const FilesScreen: Component<Props> = (props) => {
  const [files, setFiles] = createSignal<TreeFile[]>([])
  const [loading, setLoading] = createSignal(true)
  const [loadError, setLoadError] = createSignal('')
  const [selected, setSelected] = createSignal<string | null>(null)
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set())

  const reload = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const d = await api.filesList()
      setFiles(d.files)
      // corpus is small — pre-expand every folder (same UX as the picker)
      setExpanded(allDirPaths(buildTree(d.files)))
    } catch (e) {
      setLoadError((e as Error).message || '加载失败')
    } finally {
      setLoading(false)
    }
  }
  onMount(reload)

  const tree = () => buildTree(files())
  const isOpen = (path: string) => expanded().has(path)
  const toggleDir = (path: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  const isListed = (path: string) => props.listedPaths?.().has(path) ?? false
  const crumb = () => (selected() ?? '').replace(/\.md$/, '')

  const TreeRows: Component<{ nodes: TreeNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node =>
        node.kind === 'dir' ? (
          <>
            <div
              class="files-tree-dir"
              style={{ 'padding-left': `${tp.depth * 16 + 8}px` }}
              onClick={() => toggleDir(node.path)}
            >
              <span
                class={`files-tree-chevron${isOpen(node.path) ? ' files-tree-chevron--open' : ''}`}
              >
                <IconChevronRight size={18} />
              </span>
              <md-icon class="files-tree-icon"><IconFolder /></md-icon>
              <span class="md-typescale-label-large">{node.name}</span>
            </div>
            <Show when={isOpen(node.path)}>
              <TreeRows nodes={node.dirs} depth={tp.depth + 1} />
              <TreeRows nodes={node.files} depth={tp.depth + 1} />
            </Show>
          </>
        ) : (
          <div
            class="files-tree-file"
            classList={{ 'files-tree-file--selected': selected() === node.path }}
            style={{ 'padding-left': `${tp.depth * 16 + 30}px` }}
            onClick={() => setSelected(node.path)}
          >
            <md-icon class="files-tree-icon"><IconDescription /></md-icon>
            <span class="files-tree-file__name md-typescale-body-medium">
              {node.name}
            </span>
            <Show when={isListed(node.path)}>
              <span class="files-tree-badge md-typescale-label-small">清单</span>
            </Show>
          </div>
        )
      }
    </For>
  )

  return (
    <div class="files-screen">
      <md-elevated-card class="files-tree-card">
        <div class="files-tree-inner">
          <div class="files-tree-head">
            <span class="md-typescale-title-small">笔记</span>
            <md-icon-button aria-label="刷新" onClick={reload}>
              <md-icon>
                <IconRestartAlt size={18} />
              </md-icon>
            </md-icon-button>
          </div>
          <Show when={loading()}>
            <div class="files-tree-loading">
              <md-circular-progress
                indeterminate
                style="--md-circular-progress-size:28px"
              />
            </div>
          </Show>
          <Show when={!loading() && loadError()}>
            <div class="files-tree-error md-typescale-body-small">
              加载失败：{loadError()}
              <md-text-button onClick={reload}>重试</md-text-button>
            </div>
          </Show>
          <Show when={!loading() && !loadError() && files().length === 0}>
            <div class="files-tree-empty md-typescale-body-small">
              语料库里没有 .md 文件。
            </div>
          </Show>
          <div class="files-tree">
            <TreeRows nodes={tree()} depth={0} />
          </div>
        </div>
      </md-elevated-card>

      <md-elevated-card class="files-viewer-card">
        <div class="files-viewer-inner">
          <Show
            when={selected()}
            fallback={
              <div class="files-viewer-empty md-typescale-body-medium">
                从左边选择一个文件查看内容（只读）。
              </div>
            }
          >
            <div class="files-viewer-crumb md-typescale-label-small">
              {crumb()}
            </div>
            <FileViewer
              path={selected()}
              anchorLine={null}
              fetchFile={api.filesRaw}
              class="note-body md-typescale-body-medium files-viewer-body"
            />
          </Show>
        </div>
      </md-elevated-card>
    </div>
  )
}
