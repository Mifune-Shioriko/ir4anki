import { Component, For, Show, createSignal, onMount } from 'solid-js'
import { api } from '../api'
import type { ReadingCorpusFile, ReadingFileSummary } from '../types'
import { buildTree, allDirPaths } from '../lib/tree'
import type { TreeNode } from '../lib/tree'
import {
  IconArrowUpward, IconChevronRight, IconClose, IconDescription, IconFolder,
  IconMenuBook, IconVerticalAlignTop,
} from './icons'

// 阅读清单 management screen (渐进制卡, user spec 2026-09-19): files are
// opt-in (the corpus mixes study notes with misc logs) and manually ordered
// — top of list = highest priority (decision #2: 置顶/上移 buttons, no
// drag-and-drop in v1). Each row shows progress (done+skipped / total), the
// frontier status, a drift warning badge and a remove button (progress is
// archived server-side, so re-adding restores it).
//
// 「添加文件」 opens a corpus picker rendered as a FOLDER TREE (user spec
// 2026-09-19 round 2 — 像正经的文件管理器: 文件夹可展开，里面是子文件夹
// 或文件). The tree is built client-side from the flat /api/reading/corpus
// paths (year/科目/文件.md) via the SHARED lib/tree.ts (the 文件 browser
// uses the same builder). The dialog STAYS OPEN across adds for batch
// adding; files already in the list render as 已加入 (disabled). All
// mutations go through /api/reading/list/* and re-read the status
// afterwards (single source of truth = the backend's list order).

interface Props {
  onBack?: () => void
  busy: boolean
  /** fired after any list mutation so the nav-rail badge (readingListSize)
   * stays fresh while this section is open (round 3) */
  onListChange?: () => void
}

const FRONTIER_LABEL: Record<string, string> = {
  todo: '未读',
  active: '正在制卡',
  done: '制卡完成',
  skipped: '已跳过',
  // held by the preview-pool gate (B·二段重推, round 3) — not a stored
  // status; the list screen maps gated_frontier.status to this label when
  // the chunk is currently held
  held: '等卡片过预览池',
}

export const ReadingListScreen: Component<Props> = (props) => {
  const [list, setList] = createSignal<ReadingFileSummary[]>([])
  const [corpus, setCorpus] = createSignal<ReadingCorpusFile[]>([])
  const [pickerOpen, setPickerOpen] = createSignal(false)
  const [removeTarget, setRemoveTarget] = createSignal<ReadingFileSummary | null>(null)
  const [loading, setLoading] = createSignal(true)
  const [actionBusy, setActionBusy] = createSignal(false)
  // expanded folders in the picker tree (by dir path); root dirs start open
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set())

  const reload = async () => {
    try {
      const [status, corp] = await Promise.all([api.readingStatus(), api.readingCorpus()])
      setList(status.list)
      setCorpus(corp.files)
    } catch {
      /* status screen degrades to empty; the back button always works */
    } finally {
      setLoading(false)
    }
  }
  onMount(reload)

  const busyNow = () => props.busy || actionBusy()

  const mutate = async (fn: () => Promise<unknown>) => {
    if (busyNow()) return
    setActionBusy(true)
    try {
      await fn()
      await reload()
      props.onListChange?.()
    } catch (e) {
      alert('操作失败：' + (e as Error).message)
    } finally {
      setActionBusy(false)
    }
  }

  const moveUp = (s: ReadingFileSummary) =>
    mutate(() => api.readingListReorder({ path: s.path }))
  const moveTop = (s: ReadingFileSummary) =>
    mutate(() => api.readingListReorder({ path: s.path, top: true }))
  const addFile = (f: { path: string }) =>
    // keep the dialog OPEN after an add (batch adding several files is the
    // common case); the picker list refreshes via reload() and the just-added
    // file drops out of notListed()
    mutate(() => api.readingListAdd(f.path))
  const confirmRemove = () => {
    const t = removeTarget()
    if (!t) return
    mutate(async () => {
      await api.readingListRemove(t.path)
      setRemoveTarget(null)
    })
  }

  const progress = (s: ReadingFileSummary) => s.done + s.skipped
  // tree over the WHOLE corpus; files already in the list show as 已加入
  const tree = () => buildTree(corpus())
  const isOpen = (path: string) => expanded().has(path)
  const toggleDir = (path: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  // open the picker with every folder pre-expanded (small corpus — showing
  // the whole tree beats making the user click into each folder)
  const openPicker = () => {
    setExpanded(allDirPaths(tree()))
    setPickerOpen(true)
  }
  const pendingInTree = () => {
    let n = 0
    const count = (nodes: TreeNode[]) =>
      nodes.forEach(x => {
        if (x.kind === 'dir') { count(x.dirs); count(x.files) }
        else if (x.file && !x.file.in_list) n++
      })
    count(tree())
    return n
  }

  // recursive tree rows (depth → indent)
  const TreeRows: Component<{ nodes: TreeNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node =>
        node.kind === 'dir' ? (
          <>
            <div
              class="reading-tree-dir"
              style={{ 'padding-left': `${tp.depth * 18 + 4}px` }}
              onClick={() => toggleDir(node.path)}
            >
              <span class={`reading-tree-chevron${isOpen(node.path) ? ' reading-tree-chevron--open' : ''}`}>
                <IconChevronRight size={18} />
              </span>
              <md-icon class="reading-tree-icon"><IconFolder /></md-icon>
              <span class="md-typescale-title-small">{node.name}</span>
            </div>
            <Show when={isOpen(node.path)}>
              <TreeRows nodes={node.dirs} depth={tp.depth + 1} />
              <TreeRows nodes={node.files} depth={tp.depth + 1} />
            </Show>
          </>
        ) : (
          <div
            class="reading-tree-file"
            classList={{ 'reading-tree-file--added': !!node.file?.in_list }}
            style={{ 'padding-left': `${tp.depth * 18 + 4}px` }}
          >
            <md-icon class="reading-tree-icon"><IconDescription /></md-icon>
            <div class="reading-tree-file__text">
              <div class="md-typescale-title-small">{node.name}</div>
            </div>
            <Show
              when={!node.file?.in_list}
              fallback={<span class="reading-tree-added md-typescale-label-small">已加入</span>}
            >
              <md-text-button
                disabled={busyNow()}
                onClick={() => node.file && addFile(node.file)}
              >
                加入清单
              </md-text-button>
            </Show>
          </div>
        )
      }
    </For>
  )

  return (
    <div class="screen">
      <md-elevated-card class="screen-card reading-list-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">阅读清单</h1>
          <p class="screen-detail md-typescale-body-medium">
            排在前面的文件先推进；一个文件内按顺序读，前一段没完成不会推后面的。
            已制卡的片段会等它的卡过完预览池（次日放行）后再重推，补卡后点「制卡完成」解锁下一段。
            移出清单不会丢进度，重新添加即恢复。
          </p>

          <Show when={loading()}>
            <div class="reading-list-loading">
              <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
            </div>
          </Show>

          <Show when={!loading() && list().length === 0}>
            <div class="reading-list-empty md-typescale-body-medium">
              <md-icon class="reading-list-empty-icon"><IconMenuBook /></md-icon>
              清单还是空的——点下面的「添加文件」把要读的笔记加进来。
            </div>
          </Show>

          <div class="reading-list">
            <For each={list()}>
              {(s, i) => (
                <div class="reading-list-item" classList={{ 'reading-list-item--missing': !!s.missing }}>
                  <div class="reading-list-item__main">
                    <div class="reading-list-item__title md-typescale-title-small">
                      {s.title}
                      <Show when={s.missing}>
                        <span class="reading-badge reading-badge--warn">文件已不在语料中</span>
                      </Show>
                      <Show when={!s.missing && s.orphans > 0}>
                        <span class="reading-badge reading-badge--warn">
                          文件已变更 · {s.orphans} 段状态待确认
                        </span>
                      </Show>
                    </div>
                    <div class="reading-list-item__meta md-typescale-label-small">
                      <span>{s.path.replace(/^\d{4}\//, '')}</span>
                      <Show when={!s.missing}>
                        <span>
                          进度 {progress(s)} / {s.total_chunks}
                          {s.active > 0 ? ` · 正在制卡 ${s.active}` : ''}
                          {s.cards_created > 0 ? ` · 已制卡 ${s.cards_created}` : ''}
                        </span>
                      </Show>
                    </div>
                    <Show when={!s.missing}>
                      <md-linear-progress
                        class="reading-list-item__bar"
                        value={s.total_chunks > 0 ? progress(s) / s.total_chunks : 0}
                      />
                    </Show>
                    <div class="reading-list-item__frontier md-typescale-label-small">
                      <Show when={s.frontier}>
                        下一段：{s.frontier!.title}
                        <span class={`reading-status-chip reading-status-${s.frontier!.status}`}>
                          {FRONTIER_LABEL[s.frontier!.status] ?? s.frontier!.status}
                        </span>
                      </Show>
                      {/* B·二段重推 (round 3): frontier held because its
                          cards are still inside the preview pipeline */}
                      <Show when={!s.frontier && s.gated_frontier}>
                        下一段：{s.gated_frontier!.title}
                        <span class="reading-status-chip reading-status-held">
                          等卡片过预览池
                        </span>
                      </Show>
                      <Show when={!s.frontier && !s.gated_frontier && !s.missing}>
                        <span class="reading-frontier-done">全部读完 ✓</span>
                      </Show>
                    </div>
                  </div>
                  <div class="reading-list-item__actions">
                    <md-icon-button
                      aria-label="置顶"
                      disabled={busyNow() || i() === 0}
                      onClick={() => moveTop(s)}
                    >
                      <md-icon><IconVerticalAlignTop /></md-icon>
                    </md-icon-button>
                    <md-icon-button
                      aria-label="上移"
                      disabled={busyNow() || i() === 0}
                      onClick={() => moveUp(s)}
                    >
                      <md-icon><IconArrowUpward /></md-icon>
                    </md-icon-button>
                    <md-icon-button
                      aria-label="移出清单"
                      disabled={busyNow()}
                      onClick={() => setRemoveTarget(s)}
                    >
                      <md-icon><IconClose /></md-icon>
                    </md-icon-button>
                  </div>
                </div>
              )}
            </For>
          </div>

          <div class="screen-actions">
            <md-filled-tonal-button onClick={openPicker} disabled={busyNow()}>
              添加文件
            </md-filled-tonal-button>
          </div>
          <Show when={props.onBack}>
            <md-text-button onClick={() => props.onBack?.()} disabled={busyNow()}>
              返回
            </md-text-button>
          </Show>
        </div>
      </md-elevated-card>

      <Show when={pickerOpen()}>
        <md-dialog class="reading-picker-dialog" open onClose={() => setPickerOpen(false)}>
          <div slot="headline">选择要读的文件</div>
          <div slot="content" class="reading-picker-list">
            <div class="reading-picker-hint md-typescale-label-small">
              待加入 {pendingInTree()} 个文件 · 点文件夹展开/收起 · 可连续添加
            </div>
            <Show when={corpus().length === 0}>
              <div class="reading-picker-empty md-typescale-body-medium">
                语料库里没有找到可读的 .md 文件。
              </div>
            </Show>
            <div class="reading-tree">
              <TreeRows nodes={tree()} depth={0} />
            </div>
          </div>
          <div slot="actions">
            <md-text-button onClick={() => setPickerOpen(false)}>关闭</md-text-button>
          </div>
        </md-dialog>
      </Show>

      <Show when={removeTarget() !== null}>
        <md-dialog class="confirm-dialog" open onClose={() => setRemoveTarget(null)}>
          <div slot="headline">移出阅读清单？</div>
          <div slot="content" class="confirm-text md-typescale-body-medium">
            「{removeTarget()!.title}」将从清单移出。已读进度会保留，重新添加即恢复。
          </div>
          <div slot="actions">
            <md-text-button onClick={() => setRemoveTarget(null)} disabled={actionBusy()}>
              取消
            </md-text-button>
            <md-filled-button class="danger-button" onClick={confirmRemove} disabled={actionBusy()}>
              移出
            </md-filled-button>
          </div>
        </md-dialog>
      </Show>
    </div>
  )
}
