import { Component, For, Show, createSignal, createMemo, createEffect, onCleanup, onMount } from 'solid-js'
import { api, UploadConflictError, UploadPartialError } from '../api'
import { FilesMenu } from './FilesMenu'
import { remapPath, validMove, filterTree } from '../lib/files-state'
import { FileOperations } from '../lib/fileOperations'
import { FileViewer, invalidateFileCache } from './FileViewer'
import { buildTree, allDirPaths } from '../lib/tree'
import type { TreeNode, TreeFile } from '../lib/tree'
import {
  IconChevronRight, IconDelete, IconDescription, IconDriveFileMove, IconEdit,
  IconFolder, IconFolderOpen, IconRestartAlt, IconArrowBack,
} from './icons'
import { Snackbar } from './Snackbar'
import { applyDialogGuard } from '../lib/dialog-guard'

// 文件 section (user spec 2026-09-19 round 3; MANAGEMENT since 2026-10-06):
// the app is the SINGLE SOURCE OF TRUTH for the ~/anki-notes corpus — no
// more side-channel file-manager edits. Left = folder tree with a CURRENT
// DIRECTORY (upload target), right = FileViewer. Management commands belong
// to the single row menu. Uploads accept .md + images (folders keep their
// subtree via webkitRelativePath); rename/move migrate reading progress
// through a recoverable backend journal (path-keyed rows follow the file);
// delete is HARD (disk + reading progress) behind a type-the-name 防呆.
// The list endpoint is NOT reading-gated, so browsing works with the
// reading feature flag off too.

interface Props {
  /** reading-list membership: render an 清单 badge + a delete warning */
  listedPaths?: () => Set<string>
}

type PendingUpload = { file: File; rel: string }[]

export const FilesScreen: Component<Props> = (_props) => {
  const [files, setFiles] = createSignal<TreeFile[]>([])
  const [dirs, setDirs] = createSignal<string[]>([])
  const [loading, setLoading] = createSignal(true)
  const [loadError, setLoadError] = createSignal('')
  const [selected, setSelected] = createSignal<string | null>(null)
  const readSaved = <T,>(key: string, fallback: T): T => {
    try { return JSON.parse((key === 'width' ? localStorage : sessionStorage).getItem('files-' + key) ?? 'null') ?? fallback } catch { return fallback }
  }
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set(readSaved<string[]>('expanded', [])))
  const [focused, setFocused] = createSignal('')
  const [query, setQuery] = createSignal('')
  const [preview, setPreview] = createSignal(false)
  const [membership, setMembership] = createSignal(new Set<string>())
  const [undoId, setUndoId] = createSignal<string | null>(readSaved<string | null>('undo-operation', null))
  createEffect(() => sessionStorage.setItem('files-undo-operation', JSON.stringify(undoId())))
  const [undoBusy, setUndoBusy] = createSignal(false)
  const [viewerVersion, setViewerVersion] = createSignal(0)
  const [treeWidth, setTreeWidth] = createSignal(Math.max(260, Math.min(600, readSaved<number>('width', 380))))
  const scrollPositions = new Map<string, number>()
  let previewReadyPath: string | null = null
  let previewBody: HTMLDivElement | undefined
  createEffect(() => {
    sessionStorage.setItem('files-expanded', JSON.stringify([...expanded()]))
    localStorage.setItem('files-width', JSON.stringify(treeWidth()))
  })
  let refreshGeneration = 0
  let alive = true
  // current directory = the upload target (user ruling 2026-10-06:
  // 先选中当前文件夹作为落点). '' = corpus root.
  const [currentDir, setCurrentDir] = createSignal(readSaved<string>('directory', ''))
  createEffect(() => sessionStorage.setItem('files-directory', JSON.stringify(currentDir())))
  const [snack, setSnack] = createSignal('')
  let snackTimer: ReturnType<typeof setTimeout> | null = null
  const showSnack = (msg: string) => {
    setSnack(msg)
    if (snackTimer) clearTimeout(snackTimer)
    snackTimer = setTimeout(() => setSnack(''), 4000)
  }

  const reload = async () => {
    const hadTreeFocus = !!document.activeElement?.closest('.files-tree')
    const generation = ++refreshGeneration
    setLoading(true)
    setLoadError('')
    try {
      const confirmed = await operations.reconcile()
      if (confirmed && alive) showSnack('已确认文件迁移，可撤销')
      if (!alive || generation !== refreshGeneration) return
      const [d, members] = await Promise.all([api.filesList(), api.filesMembership()])
      if (!alive || generation !== refreshGeneration) return
      setMembership(new Set(members.paths))
      setFiles(d.files)
      setDirs(d.dirs ?? [])
      const available = allDirPaths(buildTree(d.files, d.dirs))
      setExpanded(prev => new Set([...prev].filter(p => available.has(p))))
      if (currentDir() && !available.has(currentDir())) setCurrentDir('')
      if (selected() && !d.files.some(f => f.path === selected())) setSelected(null)
      if (focused() && !available.has(focused()) && !d.files.some(f => f.path === focused())) setFocused('')
      if (hadTreeFocus) requestAnimationFrame(() => focusRow(focused()))
    } catch (e) {
      if (alive && generation === refreshGeneration) setLoadError((e as Error).message || '加载失败')
    } finally {
      if (alive && generation === refreshGeneration) setLoading(false)
    }
  }
  onMount(reload)
  onCleanup(() => { alive = false; refreshGeneration++; if (snackTimer) clearTimeout(snackTimer) })

  const fullTree = createMemo(() => buildTree(files(), dirs()))
  const tree = createMemo(() => filterTree(fullTree(), query()))
  const fileIndex = createMemo(() => new Map(files().map(file => [file.path, file])))

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
  const isListed = (path: string) => membership().has(path)
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
    if (!entries.length || uploading()) return
    if (operations.isBusy()) { showSnack('请先刷新确认待处理的文件操作，再重新上传'); return }
    setUploading(true)
    try {
      const d = await api.filesUpload(entries, dir, policy)
      d.saved.filter(s => s.action !== 'skipped').forEach(s => invalidateFileCache(s.path))
      setViewerVersion(n => n + 1)
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
      } else if (e instanceof UploadPartialError) {
        e.saved.filter(s => s.action !== 'skipped').forEach(s => invalidateFileCache(s.path))
        setViewerVersion(n => n + 1)
        showSnack(e.message)
        await reload()
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
  const closeRename = () => { if (!renameBusy()) setRenameTarget(null) }
  const submitRename = async () => {
    const t = renameTarget()
    if (!t || renameBusy()) return
    const name = renameValue().trim()
    if (!name) return
    setRenameBusy(true)
    try {
      const d = await operations.run('rename', t.path, name)
      if (!d) return
      setRenameTarget(null)
      showSnack(d.unchanged ? '名称未变化' : `已重命名为 ${d.path.split('/').pop()}`)
      await reload()
      requestAnimationFrame(() => focusRow(focused()))
    } catch (e) {
      showSnack((operations.hasPending() ? '' : '无法重命名：') + ((e as Error).message || e))
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
  const closeMove = () => { if (!moveBusy()) setMoveTarget(null) }
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
  const executeMove = async (t: { path: string; isDir: boolean }, dest: string) => {
    if (moveBusy() || !validMove(t.path, t.isDir, dest)) return
    setMoveBusy(true)
    try {
      const d = await operations.run('move', t.path, dest)
      if (!d) return
      setMoveTarget(null)
      showSnack(d.unchanged ? '位置未变化' : `已移动到 ${d.path}`)
      await reload()
      requestAnimationFrame(() => focusRow(focused()))
    } catch (e) {
      showSnack((operations.hasPending() ? '' : '无法移动：') + ((e as Error).message || e))
    } finally {
      setMoveBusy(false)
    }
  }

  const submitMove = () => {
    const target = moveTarget(), dest = moveDest()
    if (target && dest !== null) return executeMove(target, dest)
  }

  const MoveDirRows: Component<{ nodes: DirNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node => (
        <>
          <div
            class="files-move-dir" role="button" tabIndex={moveForbidden(node.path) ? -1 : 0} aria-disabled={moveForbidden(node.path)} onKeyDown={e => { if ((e.key === 'Enter' || e.key === ' ') && !moveForbidden(node.path)) { e.preventDefault(); setMoveDest(node.path) } }}
            classList={{
              'files-move-dir--pick': moveDest() === node.path,
              'files-move-dir--disabled': moveForbidden(node.path),
            }}
            style={{ 'padding-left': `${Math.min(tp.depth, 5) * 14 + 8}px` }}
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
  const closeDelete = () => { if (!deleteBusy()) setDeleteTarget(null) }
  const deleteBasename = () => {
    const t = deleteTarget()
    if (!t) return ''
    return t.path.split('/').pop() ?? t.path
  }
  // files under a deleted directory that sit in the reading list
  const deleteListedCount = () => {
    const t = deleteTarget()
    if (!t) return 0
    const listed = membership()
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
    if (operations.isBusy()) { showSnack('请先刷新确认待处理的文件操作，再重新删除'); return }
    setDeleteBusy(true)
    try {
      await api.filesDelete(t.path, deleteConfirm().trim())
      setDeleteTarget(null)
      showSnack(`已删除 ${t.path.split('/').pop()}`)
      window.dispatchEvent(new CustomEvent('files-deleted', { detail: { path: t.path } }))
      invalidateFileCache(t.path)
      setUndoId(null)
      if (selected() === t.path || (t.isDir && (selected() ?? '').startsWith(t.path + '/'))) {
        setSelected(null)
      }
      if (currentDir() === t.path || currentDir().startsWith(t.path + '/')) {
        setCurrentDir('')
      }
      await reload()
      requestAnimationFrame(() => focusRow(focused()))
    } catch (e) {
      showSnack('删除失败：' + ((e as Error).message || e))
    } finally {
      setDeleteBusy(false)
    }
  }

  type Target = { path: string; isDir: boolean }
  const [operationVersion, setOperationVersion] = createSignal(0)
  const busy = () => { operationVersion(); return operations.isBusy() || renameBusy() || moveBusy() || deleteBusy() || uploading() || undoBusy() }
  const copyPath = async (path: string) => {
    try {
      if (!navigator.clipboard?.writeText) {
        showSnack(`此浏览器无法自动复制，请手动复制路径：${path}`)
        return
      }
      await navigator.clipboard.writeText(path)
      showSnack('路径已复制')
    } catch { showSnack(`无法复制路径，请手动复制：${path}`) }
  }
  const command = (target: Target, action: 'rename' | 'move' | 'delete' | 'copy') => {
    if (busy() || !target.path) return
    if (action === 'rename') openRename(target.path, target.isDir)
    if (action === 'move') openMove(target.path, target.isDir)
    if (action === 'delete') openDelete(target.path, target.isDir)
    if (action === 'copy') void copyPath(target.path)
  }
  const applyRelocation = (old: string, next: string) => {
    if (old === next) return
    // Invalidate before reactive path updates can mount any reading consumer.
    invalidateFileCache(old); invalidateFileCache(next)
    setSelected(p => remapPath(p, old, next))
    setCurrentDir(p => remapPath(p, old, next))
    setFocused(p => remapPath(p, old, next))
    setExpanded(prev => new Set([...prev].map(p => remapPath(p, old, next))))
    setMembership(prev => new Set([...prev].map(p => remapPath(p, old, next))))
    for (const [p, top] of [...scrollPositions]) {
      const mapped = remapPath(p, old, next)
      if (p !== mapped) { scrollPositions.delete(p); scrollPositions.set(mapped, top) }
    }
    window.dispatchEvent(new CustomEvent('files-relocated', { detail: { oldPath: old, newPath: next } }))
    previewReadyPath = null
    setViewerVersion(n => n + 1)
  }
  const operations = new FileOperations(api, sessionStorage, (result, old, request) => {
    applyRelocation(old, result.path)
    if (request.action === 'undo') { if (undoId() === request.value) setUndoId(null) }
    else if (result.operationId) setUndoId(result.operationId)
  }, () => setOperationVersion(v => v + 1))
  const undoMove = async () => {
    const id = undoId()
    if (!id || busy()) return
    setUndoBusy(true)
    try {
      const result = await operations.run('undo', selected() ?? '', id)
      if (!result) return
      showSnack('已撤销文件迁移'); await reload()
    } catch (e) { showSnack('无法撤销：' + (e as Error).message) }
    finally { setUndoBusy(false) }
  }
  const selectFile = (path: string) => {
    if (selected() && previewReadyPath === selected() && previewBody) scrollPositions.set(selected()!, previewBody.scrollTop)
    previewReadyPath = null
    setSelected(path); setFocused(path)
    setCurrentDir(path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
    setPreview(true)

  }
  const focusRow = (path: string) => {
    const row = Array.from(document.querySelectorAll<HTMLElement>('.files-tree [data-path]')).find(el => el.dataset.path === path)
    if (row?.getClientRects().length) row.focus()
  }
  const locate = () => {
    const path = selected()
    if (!path) return
    setQuery(''); setPreview(false); setFocused(path)
    const parts = path.split('/')
    setExpanded(prev => new Set([...prev, ...parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))]))
    requestAnimationFrame(() => {
      const row = Array.from(document.querySelectorAll<HTMLElement>('.files-tree [data-path]')).find(el => el.dataset.path === path)
      row?.focus(); row?.scrollIntoView({ block: 'nearest' })
    })
  }
  const rowKey = (e: KeyboardEvent, target: Target) => {
    if (e.composedPath().some(p => p instanceof HTMLElement && p.matches('md-icon-button, md-menu, md-menu-item'))) return
    const row = e.currentTarget as HTMLElement
    const rows = Array.from(document.querySelectorAll<HTMLElement>('.files-tree [role="treeitem"]'))
    const index = rows.indexOf(row)
    let next: HTMLElement | undefined
    if (e.key === 'ArrowDown') next = rows[index + 1]
    if (e.key === 'ArrowUp') next = rows[index - 1]
    if (e.key === 'Home') next = rows[0]
    if (e.key === 'End') next = rows.at(-1)
    if (next) { e.preventDefault(); next.focus(); return }
    if (e.key === 'ArrowRight' && target.isDir) { e.preventDefault(); setDirOpen(target.path, true) }
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      if (target.isDir && isOpen(target.path)) setDirOpen(target.path, false)
      else rows.find(r => r.dataset.path === target.path.slice(0, Math.max(0, target.path.lastIndexOf('/'))))?.focus()
    }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (target.isDir) { setCurrentDir(target.path); setDirOpen(target.path, true) } else selectFile(target.path) }
    if (e.key === 'F2') { e.preventDefault(); command(target, 'rename') }
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) { e.preventDefault(); (row.querySelector('md-icon-button') as HTMLElement)?.click() }
  }
  const RowActions: Component<Target> = ap => <FilesMenu label={`更多操作：${ap.path}`} disabled={busy()} actions={[
    { label: '重命名', icon: () => <IconEdit />, run: () => command(ap, 'rename') },
    { label: '移动到…', icon: () => <IconDriveFileMove />, run: () => command(ap, 'move') },
    { label: '复制路径', run: () => command(ap, 'copy') },
    { label: '永久删除', icon: () => <IconDelete />, run: () => command(ap, 'delete') },
  ]} />
  // Pointer threshold keeps ordinary clicks distinct from a single-item move.
  const [drag, setDrag] = createSignal<Target | null>(null)
  const [externalDrop, setExternalDrop] = createSignal<string | null>(null)
  const [drop, setDrop] = createSignal<string | null>(null)
  let candidate: { target: Target; x: number; y: number; pointer: number; owner: HTMLElement } | null = null
  let hoverTimer: ReturnType<typeof setTimeout> | undefined
  let scrollFrame = 0, pointerY = 0, pointerX = 0, suppressClick = false
  let treeElement: HTMLDivElement | undefined
  // No timer: a native click may arrive in a later task after capture release.
  // Consume that pointer click once; keyboard activation remains independent.
  const consumeDragClick = (e: MouseEvent) => {
    if (!suppressClick || e.detail === 0) return
    suppressClick = false
    e.preventDefault(); e.stopImmediatePropagation()
  }
  const freshPointerDown = (e: PointerEvent) => { if (e.isPrimary !== false) suppressClick = false }
  const clearDrag = () => {
    const previous = candidate
    candidate = null; setDrag(null); setDrop(null)
    if (previous?.owner.hasPointerCapture(previous.pointer)) previous.owner.releasePointerCapture(previous.pointer)
    clearTimeout(hoverTimer); cancelAnimationFrame(scrollFrame); scrollFrame = 0
  }
  const updateDestination = (x: number, y: number) => {
    const folder = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-folder]')
    const dest = folder?.dataset.folder ?? null
    if (dest !== drop()) {
      clearTimeout(hoverTimer); setDrop(dest)
      if (dest && drag() && validMove(drag()!.path, drag()!.isDir, dest)) hoverTimer = setTimeout(() => setDirOpen(dest, true), 650)
    }
  }
  const autoScroll = () => {
    if (!drag() || !treeElement) return
    const rect = treeElement.getBoundingClientRect()
    const delta = pointerX < rect.left || pointerX > rect.right ? 0 : pointerY < rect.top + 36 ? -12 : pointerY > rect.bottom - 36 ? 12 : 0
    if (delta) treeElement.scrollTop += delta
    // Re-hit-test after scrolling/expansion: never commit to a stale folder.
    updateDestination(pointerX, pointerY)
    scrollFrame = requestAnimationFrame(autoScroll)
  }
  const pointerMove = (e: PointerEvent) => {
    if (!candidate || e.pointerId !== candidate.pointer) return
    if (!(e.buttons & 1)) { clearDrag(); return }
    if (!drag() && Math.hypot(e.clientX - candidate.x, e.clientY - candidate.y) < 7) return
    if (!drag()) {
      // Capture only once this is a drag; ordinary controls and row clicks
      // must retain their native pointerup/click targets.
      try { candidate.owner.setPointerCapture(candidate.pointer) }
      catch { clearDrag(); return }
      setDrag(candidate.target); suppressClick = true; scrollFrame = requestAnimationFrame(autoScroll)
    }
    e.preventDefault(); pointerY = e.clientY; pointerX = e.clientX
    updateDestination(pointerX, pointerY)
  }

  const performMove = async (target: Target, dest: string) => {
    if (busy() || !validMove(target.path, target.isDir, dest)) return
    await executeMove(target, dest)
  }
  const pointerUp = (e: PointerEvent) => {
    if (!candidate || candidate.pointer !== e.pointerId) return
    if (e.button !== 0) { clearDrag(); return }
    updateDestination(e.clientX, e.clientY)
    const source = drag(), dest = drop()
    clearDrag()
    if (source && dest !== null) void performMove(source, dest)
  }
  const cancelKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && candidate) { clearDrag(); showSnack('已取消移动') } }
  const hidden = () => { if (document.hidden) clearDrag() }
  onMount(() => {
    window.addEventListener('pointerdown', freshPointerDown, true)
    window.addEventListener('click', consumeDragClick, true)
    window.addEventListener('pointermove', pointerMove, { passive: false })
    window.addEventListener('pointerup', pointerUp)
    window.addEventListener('pointercancel', clearDrag)
    window.addEventListener('keydown', cancelKey)
    window.addEventListener('blur', clearDrag)
    window.addEventListener('lostpointercapture', clearDrag)
    document.addEventListener('visibilitychange', hidden)
  })
  onCleanup(() => {
    clearDrag(); window.removeEventListener('pointerdown', freshPointerDown, true); window.removeEventListener('click', consumeDragClick, true); window.removeEventListener('pointermove', pointerMove); window.removeEventListener('pointerup', pointerUp)
    window.removeEventListener('pointercancel', clearDrag); window.removeEventListener('keydown', cancelKey)
    window.removeEventListener('blur', clearDrag); window.removeEventListener('lostpointercapture', clearDrag)
    document.removeEventListener('visibilitychange', hidden)
  })
  const pointerDown = (e: PointerEvent, target: Target) => {
    freshPointerDown(e)
    const interactive = 'md-menu, md-menu-item, md-icon-button, button, a, input, textarea, select, summary, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="menuitem"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="textbox"], [role="combobox"], .files-tree-chevron'
    // closest(target) cannot cross a Material shadow/slot boundary. The real
    // composed path includes both the internal control and its custom host.
    if (e.composedPath().some(node => node instanceof Element && node.matches(interactive))) { clearDrag(); return }
    if (e.button || e.pointerType === 'touch' || busy() || !target.path) return
    clearDrag()
    const owner = e.currentTarget as HTMLElement
    candidate = { target, x: e.clientX, y: e.clientY, pointer: e.pointerId, owner }
  }
  const contextMenu = (e: MouseEvent) => { e.preventDefault(); ((e.currentTarget as HTMLElement).querySelector('md-icon-button') as HTMLElement)?.click() }
  const resize = (e: PointerEvent) => {
    e.preventDefault()
    const start = e.clientX, width = treeWidth(), handle = e.currentTarget as HTMLElement
    handle.setPointerCapture(e.pointerId)
    const move = (ev: PointerEvent) => setTreeWidth(Math.max(260, Math.min(600, width + ev.clientX - start)))
    const finish = () => { handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', finish); handle.removeEventListener('pointercancel', finish) }
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish)
  }

  const TreeRows: Component<{ nodes: TreeNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node =>
        node.kind === 'dir' ? (
          <>
            <div
              class="files-tree-dir" role="treeitem" data-path={node.path} data-folder={node.path}
              title={node.path} aria-label={node.path} aria-expanded={!!query() || isOpen(node.path)} aria-selected={currentDir() === node.path}
              tabIndex={focused() === node.path ? 0 : -1} onFocus={() => setFocused(node.path)}
              onKeyDown={e => rowKey(e, { path: node.path, isDir: true })} onContextMenu={contextMenu}
              onPointerDown={e => pointerDown(e, { path: node.path, isDir: true })}
              classList={{ 'files-tree-dir--current': currentDir() === node.path, 'files-drop-valid': drop() === node.path && !!drag() && validMove(drag()!.path, drag()!.isDir, node.path), 'files-drop-invalid': drop() === node.path && !!drag() && !validMove(drag()!.path, drag()!.isDir, node.path) }}
              style={{ 'padding-left': `${Math.min(tp.depth, 5) * 14 + 8}px` }}
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
            <Show when={!!query() || isOpen(node.path)}>
              <TreeRows nodes={node.dirs} depth={tp.depth + 1} />
              <TreeRows nodes={node.files} depth={tp.depth + 1} />
            </Show>
          </>
        ) : (
          <div
            class="files-tree-file" role="treeitem" data-path={node.path} title={node.path} aria-label={node.path}
            aria-selected={selected() === node.path} tabIndex={focused() === node.path ? 0 : -1}
            onFocus={() => setFocused(node.path)} onKeyDown={e => rowKey(e, { path: node.path, isDir: false })}
            onContextMenu={contextMenu} onPointerDown={e => pointerDown(e, { path: node.path, isDir: false })}
            classList={{ 'files-tree-file--selected': selected() === node.path }}
            style={{ 'padding-left': `${Math.min(tp.depth, 5) * 14 + 30}px` }}
            onClick={() => selectFile(node.path)}
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
    <div class="files-screen" classList={{ 'files-screen--preview': preview(), 'files-screen--dragging': !!drag() }} style={{ '--files-tree-width': `${treeWidth()}px` }}>
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
              <FilesMenu label={`上传至 ${dirLabel()}`} text="上传" disabled={uploading()} actions={[
                { label: `上传文件至 ${dirLabel()}`, run: () => fileInputRef?.click() },
                { label: `上传文件夹至 ${dirLabel()}`, run: () => dirInputRef?.click() },
              ]} />
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
          <input class="files-search" type="search" aria-label="搜索文件名或路径" placeholder="搜索文件名或路径" value={query()} onInput={e => setQuery(e.currentTarget.value)} />
          <div class="files-scope">Markdown 笔记与文件夹；附件随文件夹移动</div>
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
          <div class="files-tree" ref={treeElement} role="tree" aria-label="笔记文件" aria-busy={loading()}
            onDragOver={e => { if (e.dataTransfer?.types.includes('Files')) {
              e.preventDefault()
              setExternalDrop((e.target as Element).closest<HTMLElement>('[data-folder]')?.dataset.folder ?? currentDir())
            } }}
            onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setExternalDrop(null) }}
            onDrop={e => {
              if (!e.dataTransfer?.types.includes('Files') || busy()) return
              e.preventDefault()
              setExternalDrop(null)
              const target = (e.target as Element).closest<HTMLElement>('[data-folder]')?.dataset.folder ?? currentDir()
              const entries = Array.from(e.dataTransfer.files).map(file => ({ file, rel: file.name }))
              void doUpload(entries, target, 'error')
            }}>
            <div
              class="files-tree-dir files-tree-dir--root" role="treeitem" data-path="" data-folder="" tabIndex={focused() === '' ? 0 : -1}
              title="语料库根目录" aria-label="语料库根目录" onFocus={() => setFocused('')} onKeyDown={e => rowKey(e, { path: '', isDir: true })}
              classList={{ 'files-tree-dir--current': currentDir() === '', 'files-drop-valid': drop() === '' && !!drag() && validMove(drag()!.path, drag()!.isDir, ''), 'files-drop-invalid': drop() === '' && !!drag() && !validMove(drag()!.path, drag()!.isDir, '') }}
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

      <div class="files-resizer" role="separator" aria-label="调整文件树宽度" aria-orientation="vertical" aria-valuemin={260} aria-valuemax={600} aria-valuenow={treeWidth()} tabIndex={0}
        onPointerDown={resize} onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setTreeWidth(w => Math.max(260, Math.min(600, w + (e.key === 'ArrowRight' ? 20 : -20)))) } }} />
      <md-elevated-card class="files-viewer-card">
        <div class="files-viewer-inner">
          <Show
            when={selected()}
            fallback={
              <div class="files-viewer-empty md-typescale-body-medium">
                从左边选择一个文件查看内容。点击文件夹可将其设为上传落点；
                行尾更多菜单可重命名、移动或永久删除。
              </div>
            }
          >
            <div class="files-preview-heading">
              <md-text-button class="files-back" onClick={() => setPreview(false)}><md-icon slot="icon"><IconArrowBack /></md-icon>返回文件列表</md-text-button>
              <nav aria-label="文件路径" class="files-breadcrumbs">
                <md-text-button onClick={() => { setCurrentDir(''); setPreview(false) }}>根目录</md-text-button>
                <For each={selected()!.split('/').slice(0, -1)}>{(part, index) => <><span aria-hidden="true">/</span><md-text-button onClick={() => {
                  const path = selected()!.split('/').slice(0, index() + 1).join('/')
                  setCurrentDir(path); setDirOpen(path, true); setPreview(false)
                }}>{part}</md-text-button></>}</For>
              </nav>
              <h2 class="md-typescale-title-large" title={selected()!}>{selected()!.split('/').pop()}</h2>
              <md-text-button onClick={locate}>在文件树中定位</md-text-button>
            </div>
            <div class="files-preview-scroll" ref={previewBody} onScroll={() => { if (selected() && previewReadyPath === selected()) scrollPositions.set(selected()!, previewBody!.scrollTop) }}>
            <FileViewer
              path={selected()}
              anchorLine={null}
              fetchFile={api.filesRaw}
              revision={fileIndex().get(selected()!)?.revision}
              onReady={path => {
                if (path !== selected() || !previewBody) return
                previewReadyPath = path
                previewBody.scrollTop = scrollPositions.get(path) ?? 0
              }}
              reloadToken={viewerVersion()}
              class="note-body md-typescale-body-medium files-viewer-body"
            />
            </div>
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
              on:keydown={(e: KeyboardEvent) => {
                if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); void submitRename() }
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
                class="files-move-dir" role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setMoveDest('') } }}
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
              disabled={moveBusy() || moveDest() === null || !validMove(moveTarget()!.path, moveTarget()!.isDir, moveDest()!)}
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

      <div class="files-feedback" role="status" aria-live="polite">
        <Show when={externalDrop() !== null}>外部文件：上传至 {externalDrop() || '根目录'}</Show>
        <Show when={drag()}>{drop() === null ? '拖到文件夹或根目录以移动；Esc 取消' : validMove(drag()!.path, drag()!.isDir, drop()!) ? `移动至 ${drop() || '根目录'}` : '不能移动到此位置'}</Show>
        <Show when={undoId()}><md-text-button disabled={busy()} onClick={undoMove}>撤销上次文件迁移</md-text-button></Show>
      </div>
      <Snackbar open={!!snack()} label={snack()} />
    </div>
  )
}
