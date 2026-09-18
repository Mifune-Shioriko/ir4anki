import { Component, For, Show, createSignal, onMount } from 'solid-js'
import { api } from '../api'
import type { ReadingCorpusFile, ReadingFileSummary } from '../types'
import {
  IconArrowUpward, IconClose, IconMenuBook, IconVerticalAlignTop,
} from './icons'

// 阅读清单 management screen (渐进制卡, user spec 2026-09-19): files are
// opt-in (the corpus mixes study notes with misc logs) and manually ordered
// — top of list = highest priority (decision #2: 置顶/上移 buttons, no
// drag-and-drop in v1). Each row shows progress (done+skipped / total), the
// frontier status, a drift warning badge and a remove button (progress is
// archived server-side, so re-adding restores it).
//
// 「添加文件」 opens a corpus picker dialog listing every .md not yet in the
// list. All mutations go through /api/reading/list/* and re-read the status
// afterwards (single source of truth = the backend's list order).

interface Props {
  onBack: () => void
  busy: boolean
}

const FRONTIER_LABEL: Record<string, string> = {
  todo: '未读',
  active: '正在制卡',
  done: '制卡完成',
  skipped: '已跳过',
}

export const ReadingListScreen: Component<Props> = (props) => {
  const [list, setList] = createSignal<ReadingFileSummary[]>([])
  const [corpus, setCorpus] = createSignal<ReadingCorpusFile[]>([])
  const [pickerOpen, setPickerOpen] = createSignal(false)
  const [removeTarget, setRemoveTarget] = createSignal<ReadingFileSummary | null>(null)
  const [loading, setLoading] = createSignal(true)
  const [actionBusy, setActionBusy] = createSignal(false)

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
  const addFile = (f: ReadingCorpusFile) =>
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
  const notListed = () => corpus().filter(f => !f.in_list)

  return (
    <div class="screen">
      <md-elevated-card class="screen-card reading-list-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">阅读清单</h1>
          <p class="screen-detail md-typescale-body-medium">
            排在前面的文件先推进；一个文件内按顺序读，前一段没完成不会推后面的。
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
                      <Show
                        when={s.frontier}
                        fallback={<span class="reading-frontier-done">全部读完 ✓</span>}
                      >
                        下一段：{s.frontier!.title}
                        <span class={`reading-status-chip reading-status-${s.frontier!.status}`}>
                          {FRONTIER_LABEL[s.frontier!.status] ?? s.frontier!.status}
                        </span>
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
            <md-filled-tonal-button onClick={() => setPickerOpen(true)} disabled={busyNow()}>
              添加文件
            </md-filled-tonal-button>
          </div>
          <md-text-button onClick={() => props.onBack()} disabled={busyNow()}>
            返回
          </md-text-button>
        </div>
      </md-elevated-card>

      <Show when={pickerOpen()}>
        <md-dialog class="reading-picker-dialog" open onClose={() => setPickerOpen(false)}>
          <div slot="headline">选择要读的文件</div>
          <div slot="content" class="reading-picker-list">
            <Show when={notListed().length === 0}>
              <div class="reading-picker-empty md-typescale-body-medium">
                语料里的文件都已经在清单里了。
              </div>
            </Show>
            <For each={notListed()}>
              {f => (
                <div class="reading-picker-item">
                  <div class="reading-picker-item__text">
                    <div class="md-typescale-title-small">{f.title}</div>
                    <div class="md-typescale-label-small reading-picker-item__path">
                      {f.path.replace(/^\d{4}\//, '')}
                    </div>
                  </div>
                  <md-text-button disabled={busyNow()} onClick={() => addFile(f)}>
                    加入清单
                  </md-text-button>
                </div>
              )}
            </For>
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
