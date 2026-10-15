import { Component, createSignal, onMount, Show } from 'solid-js'
import { api } from '../api'
import type { EngineStatus } from '../types'

/** Local learner confirmation; opening and cancelling never dispatch sync. */
export const ManualSyncDialog: Component<{ onClose: () => void }> = (props) => {
  const [status, setStatus] = createSignal<EngineStatus>({})
  const [confirmed, setConfirmed] = createSignal(false)
  const [busy, setBusy] = createSignal(false)
  const [message, setMessage] = createSignal('')
  const refresh = async () => { setStatus(await api.engineStatus()) }
  onMount(() => { void refresh().catch(e => setMessage(String(e))) })
  const sync = async () => {
    if (!confirmed() || busy() || status().backend !== 'pylib') return
    setBusy(true)
    setMessage('正在同步集合和媒体，请等待完成后继续学习…')
    try {
      const r = await api.engineSync()
      if (r.synced) setConfirmed(false)
      setMessage(r.synced ? '集合与媒体同步完成。' : '尚未同步，请检查状态。')
    } catch (e) {
      setMessage('同步失败，可关闭窗口继续离线学习。已结束的撤销不能恢复。' + (e as Error).message)
    } finally {
      setBusy(false)
      await refresh().catch(() => {})
    }
  }
  return <md-dialog open aria-label="手动同步确认" onCancel={(e: Event) => { if (busy()) e.preventDefault() }} onClose={() => { if (!busy()) props.onClose() }}>
    <div slot="headline">手动同步</div>
    <div slot="content">
      <p>默认离线使用。此操作使用已配置的账号和服务器，同步期间暂停学习。</p>
      <p>上次完整同步：{String(status().last_sync_success ?? '从未记录')}</p>
      <p>{status().local_changes_pending ? '有本地集合变更待同步。' : '媒体可能仍有变更。'} 媒体状态需实际同步确认。</p>
      <Show when={status().last_sync_error}><p role="status">{String(status().last_sync_error)}</p></Show>
      <p><label><input type="checkbox" checked={confirmed()} onChange={e => setConfirmed(e.currentTarget.checked)} disabled={busy()} />确认结束最后一次评分的撤销，即使同步失败也不能恢复。</label></p>
      <p role="status" aria-live="polite" aria-busy={busy()}>{message()}</p>
    </div>
    <div slot="actions">
      <md-text-button disabled={busy()} onClick={() => { if (!busy()) props.onClose() }}>取消</md-text-button>
      <md-filled-button disabled={busy() || !confirmed() || status().backend !== 'pylib'} onClick={sync}>{busy() ? '正在同步…' : '确认并同步'}</md-filled-button>
    </div>
  </md-dialog>
}
