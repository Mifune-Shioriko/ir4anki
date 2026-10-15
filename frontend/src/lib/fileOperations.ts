import { ApiError } from '../api'
import type { FileOperationResult, FileOperationStatus } from '../api'
import { remapPath } from './files-state'

type Request = { id: string; action: 'move' | 'rename' | 'undo'; path: string; value: string }
type Transport = {
  filesMove(path: string, value: string, id: string): Promise<FileOperationResult>
  filesRename(path: string, value: string, id: string): Promise<FileOperationResult>
  filesUndo(operationId: string, id: string): Promise<FileOperationResult>
  filesOperation(id: string): Promise<FileOperationStatus>
}
const key = 'files-pending-relocation-v1'
const acknowledged = new WeakMap<object, Set<string>>()
// One outstanding intent per tab. No historical polling or migration replay.
export class FileOperations {
  private active: Promise<FileOperationResult | null> | null = null
  private pending: Request | null = null
  private applied: Set<string>
  constructor(private api: Transport, private storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>,
    private commit: (result: FileOperationResult, old: string, request: Request) => void,
    private changed: () => void = () => {}) {
    this.applied = acknowledged.get(storage) ?? new Set<string>()
    acknowledged.set(storage, this.applied)
    try {
      const value = JSON.parse(storage.getItem(key) ?? 'null')
      if (value && /^[a-f0-9]{32}$/.test(value.id) && ['rename', 'move', 'undo'].includes(value.action)
        && typeof value.path === 'string' && typeof value.value === 'string') this.pending = value
    } catch { /* unavailable/obsolete session storage */ }
  }
  hasPending() { return this.pending !== null }
  isBusy() { return this.active !== null || this.hasPending() }
  private save(value: Request | null) {
    // Persistence must precede dispatch; fail closed if the intent cannot be saved.
    if (value) this.storage.setItem(key, JSON.stringify(value))
    else this.storage.removeItem(key)
    this.pending = value
    this.changed()
  }
  private accept(result: FileOperationResult, request: Request) {
    if (!result.unchanged && (!result.operationId || !result.old_path)) throw new Error('迁移响应缺少确认信息，请刷新核对')
    if (!result.operationId || !this.applied.has(result.operationId)) {
      this.commit(result, result.old_path ?? request.path, request)
      if (result.operationId) this.applied.add(result.operationId)
    }
    // A delayed duplicate response must not erase a newer outstanding intent.
    this.pending = null
    if (JSON.parse(this.storage.getItem(key) ?? 'null')?.id === request.id) this.storage.removeItem(key)
    this.changed()
    return result
  }
  private dispatch(request: Request) {
    if (request.action === 'undo') return this.api.filesUndo(request.value, request.id)
    return request.action === 'move' ? this.api.filesMove(request.path, request.value, request.id)
      : this.api.filesRename(request.path, request.value, request.id)
  }
  private definitive(error: unknown) { return error instanceof ApiError && error.status >= 400 && error.status < 500 }
  private async check(request: Request, retry: boolean) {
    try {
      const status = await this.api.filesOperation(request.id)
      if (status.status === 'complete') return this.accept(status, request)
      if (status.status === 'cancelled') { this.save(null); throw new ApiError('迁移已取消，可重新操作', 409) }
      if (retry) { this.save(null); throw new ApiError('请求未执行；文件可能已变化，请核对后重新操作', 409) }
      return null
    } catch (error) {
      if (this.definitive(error)) this.save(null)
      throw error
    }
  }
  private exclusive(fn: () => Promise<FileOperationResult | null>) {
    if (this.active) return Promise.reject(new ApiError('文件操作正在确认，请稍后重试', 409))
    this.active = fn().finally(() => { this.active = null; this.changed() })
    this.changed()
    return this.active
  }
  reconcile() {
    if (this.active) return this.active
    return this.exclusive(async () => this.pending ? this.check(this.pending, true) : null)
  }
  run(action: Request['action'], path: string, value: string) {
    return this.exclusive(async () => {
      if (this.pending) {
        // An uncertain previous intent must resolve before any new one starts.
        const previous = this.pending
        const result = await this.check(previous, true)
        if (previous.action === action && previous.path === path && previous.value === value) return result
        if (result?.old_path) {
          path = remapPath(path, result.old_path, result.path)
          if (action === 'move') value = remapPath(value, result.old_path, result.path)
        }
      }
      // getRandomValues also works on the intercepted http fixture / LAN HTTP.
      const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
      const request: Request = { id, action, path, value }
      this.save(request)
      try { return this.accept(await this.dispatch(request), request) }
      catch (error) {
        if (this.definitive(error)) { this.save(null); throw error }
        try {
          const confirmed = await this.check(request, false)
          if (confirmed) return confirmed
        } catch (checkError) {
          if (this.definitive(checkError)) throw checkError
          throw new Error('迁移结果待确认，请刷新或重试；将沿用同一请求标识')
        }
        throw new Error('迁移结果待确认，请刷新或重试；将沿用同一请求标识')
      }
    })
  }
}
