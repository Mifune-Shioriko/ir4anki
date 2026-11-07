import { Component, createSignal, onMount, Show } from 'solid-js'
import { api } from '../api'
import type { EngineStatus } from '../types'

function formatSyncTime(raw: unknown): string {
  if (!raw || typeof raw !== 'string') return '尚未同步过'
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  const diffSec = Math.round((Date.now() - date.getTime()) / 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const abs = sameDay
    ? `今天 ${hm}`
    : date.getFullYear() === now.getFullYear()
      ? `${date.getMonth() + 1}月${date.getDate()}日 ${hm}`
      : `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${hm}`

  if (diffSec >= 0 && diffSec < 60) return `刚刚（${abs}）`
  if (diffSec >= 60 && diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前（${abs}）`
  if (diffSec >= 3600 && diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前（${abs}）`
  return abs
}

/** Local learner sync dialog; opening and cancelling never dispatch sync. */
export const ManualSyncDialog: Component<{ onClose: () => void; onSynced?: () => void }> = (props) => {
  const [status, setStatus] = createSignal<EngineStatus>({})
  const [busy, setBusy] = createSignal(false)
  const [done, setDone] = createSignal(false)
  const [tone, setTone] = createSignal<'info' | 'success' | 'error'>('info')
  const [message, setMessage] = createSignal('')
  const refresh = async () => { setStatus(await api.engineStatus()) }
  onMount(() => {
    void refresh().catch(e => {
      setTone('error')
      setMessage(String((e as Error)?.message ?? e))
    })
  })
  const sync = async () => {
    if (busy() || status().backend !== 'pylib') return
    setBusy(true)
    setDone(false)
    setTone('info')
    setMessage('正在同步集合和媒体，请稍候…')
    try {
      const r = await api.engineSync()
      if (r.synced) {
        setDone(true)
        setTone('success')
        setMessage('集合与媒体同步完成。')
        props.onSynced?.()
      } else {
        setTone('info')
        setMessage('尚未完成同步，请检查当前状态。')
      }
    } catch (e) {
      setTone('error')
      const detail = (e as Error)?.message ? `：${(e as Error).message}` : ''
      setMessage(`同步失败${detail}`)
    } finally {
      setBusy(false)
      await refresh().catch(() => {})
    }
  }
  return (
    <md-dialog
      class="confirm-dialog sync-dialog"
      open
      aria-label="手动同步"
      onCancel={(e: Event) => { if (busy()) e.preventDefault() }}
      onClose={() => { if (!busy()) props.onClose() }}
    >
      <div slot="headline">手动同步</div>
      <div slot="content" class="sync-dialog__content md-typescale-body-medium">
        <div class="sync-status-list">
          <div class="sync-status-row">
            <span class="sync-status-row__label">上次同步</span>
            <span class="sync-status-row__value">{formatSyncTime(status().last_sync_success)}</span>
          </div>
          <div class="sync-status-row">
            <span class="sync-status-row__label">本地状态</span>
            <span
              class="sync-status-badge"
              classList={{
                'sync-status-badge--pending': !!status().local_changes_pending,
                'sync-status-badge--clean': !status().local_changes_pending,
              }}
            >
              {status().local_changes_pending ? '有本地变更待同步' : '集合无新增变更'}
            </span>
          </div>
        </div>

        <Show when={status().undo_available || status().sync_deferred}>
          <div class="sync-notice sync-notice--warn md-typescale-body-small" role="note">
            同步会一并提交上一张卡片的评分，同步后无法再撤销该次评分。
          </div>
        </Show>

        <Show when={status().last_sync_error && !message()}>
          <div class="sync-notice sync-notice--error md-typescale-body-small" role="status">
            上次同步异常：{String(status().last_sync_error)}
          </div>
        </Show>

        <Show when={message()}>
          <div
            class="sync-notice md-typescale-body-small"
            classList={{
              'sync-notice--info': tone() === 'info',
              'sync-notice--success': tone() === 'success',
              'sync-notice--error': tone() === 'error',
            }}
            role="status"
            aria-live="polite"
            aria-busy={busy()}
          >
            {message()}
          </div>
        </Show>
      </div>
      <div slot="actions">
        <md-text-button disabled={busy()} onClick={() => { if (!busy()) props.onClose() }}>
          {done() ? '关闭' : '取消'}
        </md-text-button>
        <md-filled-button
          disabled={busy() || status().backend !== 'pylib'}
          onClick={sync}
        >
          {busy() ? '正在同步…' : done() ? '再次同步' : '立即同步'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
