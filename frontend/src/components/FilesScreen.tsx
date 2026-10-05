import { Component, For, Show, createSignal, onMount } from 'solid-js'
import { api, UploadConflictError } from '../api'
import { FileViewer } from './FileViewer'
import { buildTree, allDirPaths } from '../lib/tree'
import type { TreeNode, TreeFile } from '../lib/tree'
import {
  IconChevronRight, IconDelete, IconDescription, IconDriveFileMove, IconEdit,
  IconFolder, IconFolderOpen, IconRestartAlt,
} from './icons'
import { Snackbar } from './Snackbar'
import { applyDialogGuard } from '../lib/dialog-guard'

// 文件 section (user spec 2026-09-19 round 3; MANAGEMENT since 2026-10-06):
// the app is the SINGLE SOURCE OF TRUTH for the ~/anki-notes corpus — no
// more side-channel file-manager edits. Left = folder tree with a CURRENT
// DIRECTORY (upload target), right = FileViewer + rename/move/delete for
// the selected file. Uploads accept .md + images (folders keep their
// subtree via webkitRelativePath); rename/move migrate reading progress
// transactionally on the backend (path-keyed rows follow the file);
// delete is HARD (disk + reading progress) behind a type-the-name 防呆.
// The list endpoint is NOT reading-gated, so browsing works with the
// reading feature flag off too.

interface Props {
  /** reading-list membership: render an 清单 badge + a delete warning */
  listedPaths?: () => Set<string>
}

type PendingUpload = { file: File; rel: string }[]

export const FilesScreen: Component<Props> = (props) => {
  const [files, setFiles] = createSignal<TreeFile[]>([])
  const [dirs, setDirs] = createSignal<string[]>([])
  const [loading, setLoading] = createSignal(true)
  const [loadError, setLoadError] = createSignal('')
  const [selected, setSelected] = createSignal<string | null>(null)
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set())
  // current directory = the upload target (user ruling 2026-10-06:
  // 先选中当前文件夹作为落点). '' = corpus root.
  const [currentDir, setCurrentDir] = createSignal('')
  const [snack, setSnack] = createSignal('')
  let snackTimer: ReturnType<typeof setTimeout> | null = null
  const showSnack = (msg: string) => {
    setSnack(msg)
    if (snackTimer) clearTimeout(snackTimer)
    snackTimer = setTimeout(() => setSnack(''), 4000)
  }

  const reload = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const d = await api.filesList()
      setFiles(d.files)
      setDirs(d.dirs ?? [])
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

  // Directory tree for the MOVE dialog, built from the backend's real dir
  // list (includes empty dirs the file-tree can't show). Returns nested
  // {name, path, children} so a freshly-created empty folder is a valid
  // move destination.
  interface DirNode { name: string; path: string; children: DirNode[] }
  const dirTree = (): DirNode[] => {
    const roots: DirNode[] = []
    const index = new Map<string, DirNode>()
    for (const d of [...dirs()].sort()) {
      const parts = d.split('/')
      const node: DirNode = {
        name: parts[parts.length - 1], path: d, children: [] }
      index.set(d, node)
      if (parts.length === 1) {
        roots.push(node)
      } else {
        const parent = index.get(parts.slice(0, -1).join('/'))
        if (parent) parent.children.push(node)
        else roots.push(node)
      }
    }
    return roots
  }
  const isOpen = (path: string) => expanded().has(path)
  const setDirOpen = (path: string, open: boolean) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (open) next.add(path)
      else next.delete(path)
      return next
    })
  const toggleDir = (path: string) => setDirOpen(path, !isOpen(path))
  const isListed = (path: string) => props.listedPaths?.().has(path) ?? false
  const crumb = () => (selected() ?? '').replace(/\.md$/, '')
  const dirLabel = () => currentDir() || '语料库根目录'

  // ---- upload -------------------------------------------------------------
  // Hidden inputs: `multiple` for loose files (they land directly in the
  // current dir), `webkitdirectory` for a whole folder (entries keep their
  // relative subtree under the current dir).
  let fileInputRef: HTMLInputElement | undefined
  let dirInputRef: HTMLInputElement | undefined
  const [uploading, setUploading] = createSignal(false)
  // conflict dialog state: the batch that 409'd + the clashing names
  const [conflict, setConflict] = createSignal<{ entries: PendingUpload; dir: string; names: string[] } | null>(null)
  const [conflictBusy, setConflictBusy] = createSignal(false)

  const entriesFromInput = (input: HTMLInputElement): PendingUpload => {
    const out: PendingUpload = []
    for (const f of Array.from(input.files ?? [])) {
      // webkitRelativePath is set for folder picks ("sub/dir/file.md"),
      // empty for loose file picks → rel = just the name
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      out.push({ file: f, rel })
    }
    return out
  }

  const doUpload = async (
    entries: PendingUpload,
    dir: string,
    policy: 'error' | 'overwrite' | 'rename' | 'skip',
  ) => {
    if (!entries.length) return
    setUploading(true)
    try {
      const d = await api.filesUpload(entries, dir, policy)
      const n = d.saved.filter(s => s.action !== 'skipped').length
      const skipped = d.saved.filter(s => s.action === 'skipped').length
      const renamedImgs = Object.keys(d.images_renamed ?? {}).length
      let msg = `已上传 ${n} 个文件`
      if (skipped) msg += `，跳过 ${skipped} 个重名文件`
      if (renamedImgs) msg += `，${renamedImgs} 张重名图片已自动改名`
      showSnack(msg)
      await reload()
    } catch (e) {
      if (e instanceof UploadConflictError) {
        setConflict({ entries, dir, names: e.conflicts })
      } else {
        showSnack('上传失败：' + ((e as Error).message || e))
      }
    } finally {
      setUploading(false)
    }
  }

  const retryWithPolicy = async (policy: 'overwrite' | 'rename' | 'skip') => {
    const c = conflict()
    if (!c) return
    setConflictBusy(true)
    setConflict(null)
    try {
      await doUpload(c.entries, c.dir, policy)
    } finally {
      setConflictBusy(false)
    }
  }

  const onFilesPicked = () => {
    if (!fileInputRef) return
    const entries = entriesFromInput(fileInputRef)
    fileInputRef.value = ''
    if (entries.length) void doUpload(entries, currentDir(), 'error')
  }
  const onDirPicked = () => {
    if (!dirInputRef) return
    const entries = entriesFromInput(dirInputRef)
    dirInputRef.value = ''
    if (entries.length) void doUpload(entries, currentDir(), 'error')
  }

  // ---- rename dialog -------------------------------------------------------
  const [renameTarget, setRenameTarget] = createSignal<{ path: string; isDir: boolean } | null>(null)
  const [renameValue, setRenameValue] = createSignal('')
  const [renameBusy, setRenameBusy] = createSignal(false)
  const openRename = (path: string, isDir: boolean) => {
    const name = path.split('/').pop() ?? path
    setRenameValue(isDir ? name : name.replace(/\.md$/, ''))
    setRenameTarget({ path, isDir })
  }
  const closeRename = () => setRenameTarget(null)
  const submitRename = async () => {
    const t = renameTarget()
    if (!t) return
    const name = renameValue().trim()
    if (!name) return
    setRenameBusy(true)
    try {
      const d = await api.filesRename(t.path, name)
      closeRename()
      showSnack(d.unchanged ? '名称未变化' : `已重命名为 ${d.path.split('/').pop()}`)
      if (selected() === t.path) setSelected(d.path)
      await reload()
    } catch (e) {
      showSnack('重命名失败：' + ((e as Error).message || e))
    } finally {
      setRenameBusy(false)
    }
  }

  // ---- move dialog (folder-tree picker + 根目录) ---------------------------
  const [moveTarget, setMoveTarget] = createSignal<{ path: string; isDir: boolean } | null>(null)
  const [moveDest, setMoveDest] = createSignal<string | null>(null)
  const [moveBusy, setMoveBusy] = createSignal(false)
  const openMove = (path: string, isDir: boolean) => {
    setMoveDest(null)
    setMoveTarget({ path, isDir })
  }
  const closeMove = () => setMoveTarget(null)
  // a directory can't move into itself or its own subtree
  const moveForbidden = (dirPath: string): boolean => {
    const t = moveTarget()
    if (!t || !t.isDir) return false
    return dirPath === t.path || dirPath.startsWith(t.path + '/')
  }
  const moveCurrentParent = () => {
    const t = moveTarget()
    if (!t) return ''
    const i = t.path.lastIndexOf('/')
    return i < 0 ? '' : t.path.slice(0, i)
  }
  const submitMove = async () => {
    const t = moveTarget()
    const dest = moveDest()
    if (!t || dest === null) return
    setMoveBusy(true)
    try {
      const d = await api.filesMove(t.path, dest)
      closeMove()
      showSnack(d.unchanged ? '位置未变化' : `已移动到 ${d.path}`)
      if (selected() === t.path) setSelected(d.path)
      if (currentDir() === t.path) setCurrentDir(dest ? dest + '/' + (t.path.split('/').pop() ?? '') : (t.path.split('/').pop() ?? ''))
      await reload()
    } catch (e) {
      showSnack('移动失败：' + ((e as Error).message || e))
    } finally {
      setMoveBusy(false)
    }
  }

  const MoveDirRows: Component<{ nodes: DirNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node => (
        <>
          <div
            class="files-move-dir"
            classList={{
              'files-move-dir--pick': moveDest() === node.path,
              'files-move-dir--disabled': moveForbidden(node.path),
            }}
            style={{ 'padding-left': `${tp.depth * 16 + 8}px` }}
            onClick={() => {
              if (!moveForbidden(node.path)) setMoveDest(node.path)
            }}
          >
            <md-icon class="files-tree-icon">
              {moveDest() === node.path ? <IconFolderOpen size={18} /> : <IconFolder size={18} />}
            </md-icon>
            <span class="md-typescale-body-medium">{node.name}</span>
          </div>
          <MoveDirRows nodes={node.children} depth={tp.depth + 1} />
        </>
      )}
    </For>
  )

  // ---- delete dialog (HARD delete + typed-name 防呆, user ruling) ----------
  const [deleteTarget, setDeleteTarget] = createSignal<{ path: string; isDir: boolean } | null>(null)
  const [deleteConfirm, setDeleteConfirm] = createSignal('')
  const [deleteBusy, setDeleteBusy] = createSignal(false)
  const openDelete = (path: string, isDir: boolean) => {
    setDeleteConfirm('')
    setDeleteTarget({ path, isDir })
  }
  const closeDelete = () => setDeleteTarget(null)
  const deleteBasename = () => {
    const t = deleteTarget()
    if (!t) return ''
    return t.path.split('/').pop() ?? t.path
  }
  // files under a deleted directory that sit in the reading list
  const deleteListedCount = () => {
    const t = deleteTarget()
    if (!t) return 0
    const listed = props.listedPaths?.() ?? new Set<string>()
    if (t.isDir) {
      return Array.from(listed).filter(p =>
        p === t.path || p.startsWith(t.path + '/')).length
    }
    return listed.has(t.path) ? 1 : 0
  }
  const deleteCanSubmit = () => deleteConfirm().trim() === deleteBasename() && !deleteBusy()
  const submitDelete = async () => {
    const t = deleteTarget()
    if (!t || !deleteCanSubmit()) return
    setDeleteBusy(true)
    try {
      await api.filesDelete(t.path, deleteConfirm().trim())
      closeDelete()
      showSnack(`已删除 ${deleteBasename()}`)
      if (selected() === t.path || (t.isDir && (selected() ?? '').startsWith(t.path + '/'))) {
        setSelected(null)
      }
      if (currentDir() === t.path || currentDir().startsWith(t.path + '/')) {
        setCurrentDir('')
      }
      await reload()
    } catch (e) {
      showSnack('删除失败：' + ((e as Error).message || e))
    } finally {
      setDeleteBusy(false)
    }
  }

  // hover row actions (rename / move / delete) — one small icon-button trio
  const RowActions: Component<{ path: string; isDir: boolean }> = (ap) => (
    <span class="files-row-actions" onClick={e => e.stopPropagation()}>
      <md-icon-button
        class="files-row-action"
        aria-label="重命名"
        onClick={() => openRename(ap.path, ap.isDir)}
      >
        <md-icon><IconEdit size={16} /></md-icon>
      </md-icon-button>
      <md-icon-button
        class="files-row-action"
        aria-label="移动到"
        onClick={() => openMove(ap.path, ap.isDir)}
      >
        <md-icon><IconDriveFileMove size={16} /></md-icon>
      </md-icon-button>
      <md-icon-button
        class="files-row-action files-row-action--danger"
        aria-label="删除"
        onClick={() => openDelete(ap.path, ap.isDir)}
      >
        <md-icon><IconDelete size={16} /></md-icon>
      </md-icon-button>
    </span>
  )

  const TreeRows: Component<{ nodes: TreeNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node =>
        node.kind === 'dir' ? (
          <>
            <div
              class="files-tree-dir"
              classList={{ 'files-tree-dir--current': currentDir() === node.path }}
              style={{ 'padding-left': `${tp.depth * 16 + 8}px` }}
              onClick={() => {
                // row click = make this the CURRENT DIRECTORY (upload
                // target) and open it; the chevron alone collapses
                setCurrentDir(node.path)
                setDirOpen(node.path, true)
              }}
            >
              <span
                class={`files-tree-chevron${isOpen(node.path) ? ' files-tree-chevron--open' : ''}`}
                onClick={e => { e.stopPropagation(); toggleDir(node.path) }}
              >
                <IconChevronRight size={18} />
              </span>
              <md-icon class="files-tree-icon">
                {currentDir() === node.path
                  ? <IconFolderOpen size={18} />
                  : <IconFolder size={18} />}
              </md-icon>
              <span class="md-typescale-label-large files-tree-dir__name">{node.name}</span>
              <RowActions path={node.path} isDir={true} />
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
            <RowActions path={node.path} isDir={false} />
          </div>
        )
      }
    </For>
  )

  return (
    <div class="files-screen">
      {/* hidden upload inputs (webkitdirectory is a non-standard attr —
          set it via ref so TSX doesn't reject it) */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".md,.png,.jpg,.jpeg,.gif,.webp,.svg,.bmp,.avif"
        style={{ display: 'none' }}
        onChange={onFilesPicked}
      />
      <input
        ref={el => {
          dirInputRef = el
          // webkitdirectory is a non-standard attribute — TS's lib.dom
          // doesn't know it; set it imperatively so a folder pick keeps
          // webkitRelativePath on every entry
          el.setAttribute('webkitdirectory', '')
        }}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={onDirPicked}
      />

      <md-elevated-card class="files-tree-card">
        <div class="files-tree-inner">
          <div class="files-tree-head">
            <span class="md-typescale-title-small">笔记</span>
            <span class="files-tree-head-actions">
              <md-text-button
                class="files-upload-btn files-upload-file"
                disabled={uploading()}
                onClick={() => fileInputRef?.click()}
              >
                上传文件
              </md-text-button>
              <md-text-button
                class="files-upload-btn files-upload-dir"
                disabled={uploading()}
                onClick={() => dirInputRef?.click()}
              >
                上传文件夹
              </md-text-button>
              <md-icon-button aria-label="刷新" onClick={reload}>
                <md-icon>
                  <IconRestartAlt size={18} />
                </md-icon>
              </md-icon-button>
            </span>
          </div>
          <div
            class="files-current-dir md-typescale-label-small"
            title="上传落点：在左侧树中点击文件夹切换"
          >
            <md-icon class="files-tree-icon"><IconFolderOpen size={14} /></md-icon>
            上传到：<span class="files-current-dir__path">{dirLabel()}</span>
            <Show when={currentDir()}>
              <md-text-button
                class="files-current-dir__root"
                onClick={() => setCurrentDir('')}
              >
                回到根目录
              </md-text-button>
            </Show>
          </div>
          <Show when={uploading()}>
            <md-linear-progress indeterminate />
          </Show>
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
              语料库里没有 .md 文件。用上方按钮上传笔记开始。
            </div>
          </Show>
          <div class="files-tree">
            <div
              class="files-tree-dir files-tree-dir--root"
              classList={{ 'files-tree-dir--current': currentDir() === '' }}
              onClick={() => setCurrentDir('')}
            >
              <md-icon class="files-tree-icon">
                {currentDir() === '' ? <IconFolderOpen size={18} /> : <IconFolder size={18} />}
              </md-icon>
              <span class="md-typescale-label-large">/ 根目录</span>
            </div>
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
                从左边选择一个文件查看内容。点击文件夹可将其设为上传落点；
                悬停行尾按钮可重命名 / 移动 / 删除。
              </div>
            }
          >
            <div class="files-viewer-crumb md-typescale-label-small">
              {crumb()}
              <span class="files-viewer-actions">
                <md-text-button onClick={() => openRename(selected()!, false)}>
                  重命名
                </md-text-button>
                <md-text-button onClick={() => openMove(selected()!, false)}>
                  移动到…
                </md-text-button>
                <md-text-button
                  class="danger-text-button"
                  onClick={() => openDelete(selected()!, false)}
                >
                  删除
                </md-text-button>
              </span>
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

      {/* ---- rename dialog ---- */}
      <Show when={renameTarget()}>
        <md-dialog
          class="files-dialog"
          open
          ref={el => el && applyDialogGuard(el as HTMLElement, closeRename)}
        >
          <div slot="headline">
            重命名{renameTarget()!.isDir ? '文件夹' : '文件'}
          </div>
          <div slot="content" class="files-dialog-content">
            <div class="files-dialog-path md-typescale-label-small">
              {renameTarget()!.path}
            </div>
            <md-outlined-text-field
              class="files-rename-input"
              label="新名称"
              value={renameValue()}
              onInput={(e: Event) =>
                setRenameValue((e.target as HTMLInputElement).value)}
              onKeyDown={(e: KeyboardEvent) => {
                if (e.key === 'Enter') void submitRename()
              }}
            />
            <Show when={isListed(renameTarget()!.path)}>
              <div class="files-dialog-note md-typescale-body-small">
                该文件在阅读清单中——重命名后阅读进度会自动跟随，不会丢失。
              </div>
            </Show>
          </div>
          <div slot="actions">
            <md-text-button onClick={closeRename} disabled={renameBusy()}>
              取消
            </md-text-button>
            <md-filled-button
              onClick={submitRename}
              disabled={renameBusy() || !renameValue().trim()}
            >
              重命名
            </md-filled-button>
          </div>
        </md-dialog>
      </Show>

      {/* ---- move dialog ---- */}
      <Show when={moveTarget()}>
        <md-dialog
          class="files-dialog"
          open
          ref={el => el && applyDialogGuard(el as HTMLElement, closeMove)}
        >
          <div slot="headline">
            移动{moveTarget()!.isDir ? '文件夹' : '文件'}
          </div>
          <div slot="content" class="files-dialog-content">
            <div class="files-dialog-path md-typescale-label-small">
              {moveTarget()!.path}
            </div>
            <div class="files-move-hint md-typescale-label-small">选择目标文件夹：</div>
            <div class="files-move-list">
              <div
                class="files-move-dir"
                classList={{
                  'files-move-dir--pick': moveDest() === '',
                  'files-move-dir--disabled': moveTarget()!.isDir && moveForbidden(''),
                }}
                onClick={() => {
                  if (!(moveTarget()!.isDir && moveForbidden(''))) setMoveDest('')
                }}
              >
                <md-icon class="files-tree-icon">
                  {moveDest() === '' ? <IconFolderOpen size={18} /> : <IconFolder size={18} />}
                </md-icon>
                <span class="md-typescale-body-medium">/ 根目录</span>
              </div>
              <MoveDirRows nodes={dirTree()} depth={1} />
            </div>
            <Show when={moveDest() !== null && moveDest() === moveCurrentParent()}>
              <div class="files-dialog-note md-typescale-body-small">
                已在该文件夹中——继续将不会有任何变化。
              </div>
            </Show>
            <Show when={isListed(moveTarget()!.path)}>
              <div class="files-dialog-note md-typescale-body-small">
                该文件在阅读清单中——移动后阅读进度会自动跟随，不会丢失。
              </div>
            </Show>
          </div>
          <div slot="actions">
            <md-text-button onClick={closeMove} disabled={moveBusy()}>
              取消
            </md-text-button>
            <md-filled-button
              onClick={submitMove}
              disabled={moveBusy() || moveDest() === null}
            >
              移动
            </md-filled-button>
          </div>
        </md-dialog>
      </Show>

      {/* ---- delete dialog (typed-name 防呆) ---- */}
      <Show when={deleteTarget()}>
        <md-dialog
          class="files-dialog"
          open
          ref={el => el && applyDialogGuard(el as HTMLElement, closeDelete)}
        >
          <div slot="headline">
            永久删除{deleteTarget()!.isDir ? '文件夹' : '文件'}
          </div>
          <div slot="content" class="files-dialog-content">
            <div class="files-dialog-path md-typescale-label-small">
              {deleteTarget()!.path}
            </div>
            <div class="files-delete-warn md-typescale-body-medium">
              将从磁盘永久删除{deleteTarget()!.isDir ? '整个文件夹及其内容' : '该文件'}，无法恢复。
            </div>
            <Show when={deleteListedCount() > 0}>
              <div class="files-delete-warn files-delete-warn--listed md-typescale-body-small">
                其中 {deleteListedCount()} 个文件在阅读清单中：阅读进度将一并清除；已制的 Anki 卡片会保留。
              </div>
            </Show>
            <md-outlined-text-field
              class="files-delete-input"
              label={`输入 ${deleteBasename()} 以确认`}
              value={deleteConfirm()}
              onInput={(e: Event) =>
                setDeleteConfirm((e.target as HTMLInputElement).value)}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={closeDelete} disabled={deleteBusy()}>
              取消
            </md-text-button>
            <md-filled-button
              class="danger-button"
              onClick={submitDelete}
              disabled={!deleteCanSubmit()}
            >
              永久删除
            </md-filled-button>
          </div>
        </md-dialog>
      </Show>

      {/* ---- upload conflict dialog ---- */}
      <Show when={conflict()}>
        <md-dialog class="files-dialog" open>
          <div slot="headline">文件名冲突</div>
          <div slot="content" class="files-dialog-content">
            <div class="md-typescale-body-medium">
              以下 {conflict()!.names.length} 个文件在目标位置已存在：
            </div>
            <div class="files-conflict-list md-typescale-body-small">
              <For each={conflict()!.names.slice(0, 8)}>
                {n => <div class="files-conflict-item">{n}</div>}
              </For>
              <Show when={conflict()!.names.length > 8}>
                <div class="files-conflict-item">…等共 {conflict()!.names.length} 个</div>
              </Show>
            </div>
          </div>
          <div slot="actions">
            <md-text-button
              onClick={() => setConflict(null)}
              disabled={conflictBusy()}
            >
              取消上传
            </md-text-button>
            <md-outlined-button
              onClick={() => retryWithPolicy('skip')}
              disabled={conflictBusy()}
            >
              跳过重名
            </md-outlined-button>
            <md-outlined-button
              onClick={() => retryWithPolicy('rename')}
              disabled={conflictBusy()}
            >
              改名保留两者
            </md-outlined-button>
            <md-filled-button
              class="danger-button"
              onClick={() => retryWithPolicy('overwrite')}
              disabled={conflictBusy()}
            >
              覆盖
            </md-filled-button>
          </div>
        </md-dialog>
      </Show>

      <Snackbar open={!!snack()} label={snack()} />
    </div>
  )
}
