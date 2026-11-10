import { Component, For, Show, createMemo, createSignal, onMount } from 'solid-js'
import { api } from '../api'
import { lectureRequest } from '../lib/lecture-api'
import type { ReadingCorpusFile, ReadingFileSummary } from '../types'
import { buildTree, allDirPaths } from '../lib/tree'
import type { TreeNode } from '../lib/tree'
import { filterTree } from '../lib/files-state'
import {
  IconArrowDownward, IconArrowUpward, IconChevronRight, IconClose, IconDescription, IconFolder,
  IconMenuBook, IconRestartAlt, IconVerticalAlignTop,
} from './icons'

// Reading workbench uses the same elevated panes and compact headers as 文件.
// The narrow corpus picker keeps search, counts and archived-progress actions;
// the wider priority list keeps dual-segment progress, frontier and reorder controls.

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
  held: '等卡片过预览池',
}

const fmtNum = (n: number | undefined): string =>
  (n ?? 0).toLocaleString('zh-CN')

const calcPercent = (part: number, total: number): number =>
  total > 0 ? Math.min(100, Math.round((part / total) * 100)) : 0

export const ReadingListScreen: Component<Props> = (props) => {
  const [list, setList] = createSignal<ReadingFileSummary[]>([])
  const [corpus, setCorpus] = createSignal<ReadingCorpusFile[]>([])
  const [removeTarget, setRemoveTarget] = createSignal<ReadingFileSummary | null>(null)
  const [error, setError] = createSignal('')
  const [message, setMessage] = createSignal('')
  const [loading, setLoading] = createSignal(true)
  const [actionBusy, setActionBusy] = createSignal(false)
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set())
  const [query, setQuery] = createSignal('')
  const [initializedTree, setInitializedTree] = createSignal(false)

  const reload = async () => {
    try {
      const [status, corp] = await Promise.all([api.readingStatus(), api.readingCorpus()])
      setList(status.list)
      setCorpus(corp.files)
      if (!initializedTree()) {
        setExpanded(allDirPaths(buildTree(corp.files)))
        setInitializedTree(true)
      }
    } catch {
      setError('读取清单失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }
  onMount(reload)

  const busyNow = () => props.busy || actionBusy()

  const mutate = async (fn: () => Promise<unknown>) => {
    if (busyNow()) return
    setActionBusy(true)
    setError('')
    setMessage('')
    try {
      await fn()
      await reload()
      props.onListChange?.()
      setMessage('阅读清单已更新')
    } catch (e) {
      setError('操作失败：' + (e as Error).message)
    } finally {
      setActionBusy(false)
    }
  }

  const moveUp = (s: ReadingFileSummary) =>
    mutate(() => api.readingListReorder({ path: s.path }))
  const moveTop = (s: ReadingFileSummary) =>
    mutate(() => api.readingListReorder({ path: s.path, top: true }))
  const moveDown = (idx: number) => {
    const items = list()
    if (idx < 0 || idx >= items.length - 1) return
    const order = items.map(x => x.path)
    const tmp = order[idx]
    order[idx] = order[idx + 1]
    order[idx + 1] = tmp
    mutate(() => api.readingListReorder({ order }))
  }
  const addFile = (f: { path: string; kind?: 'pdf'; document_id?: string; revision_id?: string }, fresh = false) =>
    mutate(() => f.kind === 'pdf'
      ? lectureRequest(`/${f.document_id}/revisions/${f.revision_id}/read`, 'POST')
      : api.readingListAdd(f.path, fresh))
  const confirmRemove = () => {
    const t = removeTarget()
    if (!t) return
    mutate(async () => {
      await api.readingListRemove(t.path)
      setRemoveTarget(null)
    })
  }

  const isPdf = (s: ReadingFileSummary) => s.kind === 'pdf'
  const unitName = (s: ReadingFileSummary) => (isPdf(s) ? '页' : '字')
  const totalUnits = (s: ReadingFileSummary) =>
    isPdf(s) ? (s.total_pages ?? s.total_chunks) : (s.total_chars ?? 0)
  const readUnits = (s: ReadingFileSummary) =>
    isPdf(s)
      ? (s.read_pages ?? (s.done + s.skipped + (s.background ?? 0)))
      : (s.read_chars ?? 0)
  const activeUnits = (s: ReadingFileSummary) =>
    isPdf(s) ? (s.active_pages ?? s.active) : (s.active_chars ?? 0)

  const readRatio = (s: ReadingFileSummary) => {
    const tot = totalUnits(s)
    if (tot > 0) return Math.min(1, readUnits(s) / tot)
    return s.total_chunks > 0
      ? (s.done + s.skipped + (s.background ?? 0)) / s.total_chunks
      : 0
  }
  const activeRatio = (s: ReadingFileSummary) => {
    const tot = totalUnits(s)
    if (tot > 0) return Math.min(1 - readRatio(s), activeUnits(s) / tot)
    return 0
  }

  const fullTree = createMemo(() => buildTree(corpus()))
  const tree = createMemo(() => filterTree(fullTree(), query()))
  const isOpen = (path: string) => query().trim() !== '' || expanded().has(path)
  const toggleDir = (path: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  const expandAll = () => setExpanded(allDirPaths(fullTree()))

  const pendingInTree = createMemo(() =>
    corpus().filter(f => !f.in_list).length
  )

  const frontierText = (
    s: ReadingFileSummary,
    f: NonNullable<ReadingFileSummary['frontier']>,
  ): string => {
    const lineTag = (f.line_start ?? 0) > 1 ? `L${f.line_start} · ` : ''
    const snippet = f.preview && f.preview !== s.title ? f.preview : f.title
    return `${lineTag}${snippet}`
  }

  const TreeRows: Component<{ nodes: TreeNode[]; depth: number }> = (tp) => (
    <For each={tp.nodes}>
      {node =>
        node.kind === 'dir' ? (
          <>
            <div
              class="reading-tree-dir"
              title={node.path}
              style={{ 'padding-left': `${tp.depth * 16 + 8}px` }}
              onClick={() => toggleDir(node.path)}
            >
              <span class={`reading-tree-chevron${isOpen(node.path) ? ' reading-tree-chevron--open' : ''}`}>
                <IconChevronRight size={18} />
              </span>
              <md-icon class="reading-tree-icon"><IconFolder /></md-icon>
              <span class="reading-tree-dir__name md-typescale-title-small">{node.name}</span>
            </div>
            <Show when={isOpen(node.path)}>
              <TreeRows nodes={node.dirs} depth={tp.depth + 1} />
              <TreeRows nodes={node.files} depth={tp.depth + 1} />
            </Show>
          </>
        ) : (
          <div
            class="reading-tree-file"
            title={node.path}
            classList={{ 'reading-tree-file--added': !!node.file?.in_list }}
            style={{ 'padding-left': `${tp.depth * 16 + 8}px` }}
          >
            <md-icon class="reading-tree-icon"><IconDescription /></md-icon>
            <div class="reading-tree-file__text">
              <div class="reading-tree-file__title md-typescale-body-medium">{node.name}</div>
              <Show when={(node.file?.chars ?? 0) > 0 || (node.file?.page_count ?? 0) > 0}>
                <div class="reading-tree-file__meta md-typescale-label-small">
                  {node.file?.kind === 'pdf'
                    ? `${fmtNum(node.file?.page_count)} 页`
                    : `${fmtNum(node.file?.chars)} 字`}
                  {node.file?.archived && !node.file?.in_list ? ' · 有存档进度' : ''}
                </div>
              </Show>
            </div>
            <div class="reading-tree-file__actions">
              <Show
                when={!node.file?.in_list}
                fallback={<span class="reading-tree-added md-typescale-label-small">已加入</span>}
              >
                <Show when={node.file?.kind !== 'pdf' && node.file?.archived}>
                  <md-text-button
                    disabled={busyNow()}
                    onClick={() => node.file && addFile(node.file, true)}
                  >
                    重新播种
                  </md-text-button>
                </Show>
                <md-text-button
                  disabled={busyNow()}
                  onClick={() => node.file && addFile(node.file)}
                >
                  {node.file?.archived ? '恢复进度' : '加入清单'}
                </md-text-button>
              </Show>
            </div>
          </div>
        )
      }
    </For>
  )

  return (
    <div class="reading-workbench">
      {/* ---- Left column: Corpus file picker tree ---- */}
      <md-elevated-card class="reading-corpus-card reading-picker-dialog" role="region" aria-label="语料库文件">
        <div class="reading-corpus-inner">
          <div class="reading-corpus-head">
            <div>
              <h2 class="md-typescale-title-small">语料库文件</h2>
            </div>
            <div class="reading-corpus-head__actions">
              <md-text-button onClick={expandAll} disabled={busyNow()}>
                全部展开
              </md-text-button>
              <md-icon-button aria-label="刷新" onClick={reload} disabled={busyNow()}>
                <md-icon><IconRestartAlt size={18} /></md-icon>
              </md-icon-button>
            </div>
          </div>

          <div class="reading-picker-hint md-typescale-label-small">
            待加入 {pendingInTree()} 篇 · 共 {corpus().length} 篇
          </div>
          <input
            class="reading-corpus-search"
            type="search"
            aria-label="搜索语料文件"
            placeholder="搜索文件名或路径…"
            value={query()}
            onInput={e => setQuery(e.currentTarget.value)}
          />

          <Show when={!loading() && corpus().length === 0}>
            <div class="reading-picker-empty md-typescale-body-medium">
              还没有可读内容。可先在「文件」页上传笔记或导入 PDF / PPTX。
            </div>
          </Show>

          <Show when={!loading() && corpus().length > 0 && tree().length === 0}>
            <div class="reading-picker-empty md-typescale-body-medium" role="status">
              没有匹配的文件，试试其他文件名或路径。
            </div>
          </Show>

          <div class="reading-tree" role="tree" aria-label="语料库目录树" aria-busy={loading()}>
            <TreeRows nodes={tree()} depth={0} />
          </div>
        </div>
      </md-elevated-card>

      {/* ---- Right column: Reading list priority queue & character progress ---- */}
      <md-elevated-card class="reading-queue-card reading-list-card" role="region" aria-label="阅读清单">
        <div class="reading-queue-inner">
          <div class="reading-queue-head">
            <div class="reading-queue-head__title-row">
              <h1 class="reading-queue-title md-typescale-title-small">阅读清单</h1>
              <span class="reading-queue-count md-typescale-label-small">{list().length} 篇</span>
              <Show when={props.onBack}>
                <md-text-button onClick={() => props.onBack?.()} disabled={busyNow()}>
                  返回
                </md-text-button>
              </Show>
            </div>
          </div>

          <div class="reading-queue-body" aria-busy={loading()}>
            <Show when={error()}><p role="alert" class="lecture-error">{error()}</p></Show>
            <Show when={message()}><p role="status" class="reading-status-banner md-typescale-label-medium">{message()}</p></Show>
            <Show when={loading()}>
              <div class="reading-list-loading">
                <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
              </div>
            </Show>

            <Show when={!loading() && list().length === 0}>
              <div class="reading-list-empty md-typescale-body-medium">
                <md-icon class="reading-list-empty-icon"><IconMenuBook /></md-icon>
                <div>清单还是空的——从左侧语料库目录树点击「加入清单」把要读的笔记加进来。</div>
              </div>
            </Show>

            <div class="reading-list">
              <For each={list()}>
                {(s, i) => {
                  const pct = () => calcPercent(readUnits(s), totalUnits(s))
                  const unit = () => unitName(s)
                  const doneSegs = () => s.done + s.skipped + (s.background ?? 0)
                  const dirPath = () => {
                    const stripped = s.path.replace(/^\d{4}\//, '')
                    const slash = stripped.lastIndexOf('/')
                    return slash > 0 ? stripped.slice(0, slash) : stripped
                  }
                  return (
                    <div
                      class="reading-list-item"
                      classList={{
                        'reading-list-item--missing': !!s.missing,
                        'reading-list-item--top': i() === 0,
                      }}
                    >
                      <div class="reading-list-item__rank" title={`优先级 #${i() + 1}`}>
                        #{i() + 1}
                      </div>

                      <div class="reading-list-item__main">
                        <div class="reading-list-item__header">
                          <div class="reading-list-item__title md-typescale-title-small">
                            {s.title}
                            <span class="reading-list-item__path md-typescale-label-small">
                              {dirPath()}
                            </span>
                            <Show when={s.missing}>
                              <span class="reading-badge reading-badge--warn">文件已不在语料中</span>
                            </Show>
                            <Show when={!s.missing && s.orphans > 0}>
                              <span class="reading-badge reading-badge--warn">
                                文件已变更 · {s.orphans} 段状态待确认
                              </span>
                            </Show>
                          </div>

                          <div class="reading-list-item__actions">
                            <md-icon-button
                              aria-label="置顶"
                              title="置顶"
                              disabled={busyNow() || i() === 0}
                              onClick={() => moveTop(s)}
                            >
                              <md-icon><IconVerticalAlignTop /></md-icon>
                            </md-icon-button>
                            <md-icon-button
                              aria-label="上移"
                              title="上移一位"
                              disabled={busyNow() || i() === 0}
                              onClick={() => moveUp(s)}
                            >
                              <md-icon><IconArrowUpward /></md-icon>
                            </md-icon-button>
                            <md-icon-button
                              aria-label="下移"
                              title="下移一位"
                              disabled={busyNow() || i() === list().length - 1}
                              onClick={() => moveDown(i())}
                            >
                              <md-icon><IconArrowDownward /></md-icon>
                            </md-icon-button>
                            <md-icon-button
                              aria-label="移出清单"
                              title="移出清单（保留进度）"
                              disabled={busyNow()}
                              onClick={() => setRemoveTarget(s)}
                            >
                              <md-icon><IconClose /></md-icon>
                            </md-icon-button>
                          </div>
                        </div>

                        <Show when={!s.missing}>
                          <div class="reading-list-item__progress-row">
                            <div
                              class="reading-char-bar reading-list-item__bar"
                              role="progressbar"
                              aria-valuemin={0}
                              aria-valuemax={totalUnits(s)}
                              aria-valuenow={readUnits(s)}
                              title={`已读 ${fmtNum(readUnits(s))} ${unit()} / 共 ${fmtNum(totalUnits(s))} ${unit()}${activeUnits(s) > 0 ? `（正在制卡 ${fmtNum(activeUnits(s))} ${unit()}）` : ''}`}
                            >
                              <div
                                class="reading-char-bar__read"
                                style={{ width: `${readRatio(s) * 100}%` }}
                              />
                              <div
                                class="reading-char-bar__active"
                                style={{ width: `${activeRatio(s) * 100}%` }}
                              />
                            </div>
                            <span class="reading-list-item__pct md-typescale-label-medium">
                              {pct()}%
                            </span>
                          </div>

                          <div class="reading-list-item__meta md-typescale-label-small">
                            <div class="reading-list-item__stats">
                              <span class="reading-stat-Highlight">
                                已读 {fmtNum(readUnits(s))} / {fmtNum(totalUnits(s))} {unit()}
                              </span>
                              <Show when={activeUnits(s) > 0}>
                                <span class="reading-stat-active">
                                  · 正在制卡 {fmtNum(activeUnits(s))} {unit()} ({s.active} 段)
                                </span>
                              </Show>
                              <Show when={s.cards_created > 0}>
                                <span>· 已制卡 {s.cards_created} 张</span>
                              </Show>
                              <span class="reading-stat-chunks">
                                · 片段 {doneSegs()} / {s.total_chunks}
                              </span>
                            </div>

                            <div class="reading-list-item__frontier">
                              <Show when={s.frontier}>
                                <span class="reading-frontier-text" title={s.frontier!.preview || s.frontier!.title}>
                                  下一段：{frontierText(s, s.frontier!)}
                                </span>
                                <span class={`reading-status-chip reading-status-${s.frontier!.status}`}>
                                  {FRONTIER_LABEL[s.frontier!.status] ?? s.frontier!.status}
                                </span>
                              </Show>
                              <Show when={!s.frontier && s.gated_frontier}>
                                <span class="reading-frontier-text" title={s.gated_frontier!.preview || s.gated_frontier!.title}>
                                  下一段：{frontierText(s, s.gated_frontier!)}
                                </span>
                                <span class="reading-status-chip reading-status-held">
                                  等卡片过预览池
                                </span>
                              </Show>
                              <Show when={!s.frontier && !s.gated_frontier && !s.missing}>
                                <span class="reading-frontier-done">全部读完 ✓</span>
                              </Show>
                            </div>
                          </div>
                        </Show>
                      </div>
                    </div>
                  )
                }}
              </For>
            </div>
          </div>
        </div>
      </md-elevated-card>

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
