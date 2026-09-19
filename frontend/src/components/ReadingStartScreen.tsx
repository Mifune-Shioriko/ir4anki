import { Component, Show } from 'solid-js'
import { ModeSelector } from './ModeSelector'
import type { StudyModes } from '../types'

// Reading-mode start screen (渐进制卡, user spec 2026-09-19). Top of the
// funnel when the 阅读清单 has dealable chunks: one round = 阅读 N 片段 →
// 预览 → 新卡+复习. Skipping is always one tap away (mirrors PreviewScreen's
// 跳过，直接复习), so reading never traps the user.

interface Props {
  /** files in the 阅读清单 */
  listSize: number
  /** files with a dealable frontier chunk */
  available: number
  /** chunks sitting in 正在制卡 (resurface first) */
  active: number
  /** chunks held by the preview-pool gate (B·二段重推, round 3): their
   * cards haven't all cleared 预览池 yet, so they won't be dealt */
  gated?: number
  busy: boolean
  onStart: () => void
  /** funnel next: preview when preview mode is on, review otherwise */
  onSkip: () => void
  skipLabel: string
  onManageList: () => void
  studyModes?: StudyModes | null
  mode?: string
  onModeChange?: (mode: string) => void
}

export const ReadingStartScreen: Component<Props> = (props) => {
  const studyModes = () => props.studyModes
  const mode = () => props.mode ?? 'quick'
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">渐进制卡</h1>
          <p class="screen-detail md-typescale-body-medium">
            先读自己的笔记，读到值得记的地方就动手写卡——每轮推 {studyModes() ? (studyModes()![mode()]?.read ?? 2) : 2} 段（按清单顺序，同一文件可连读多段）。
            本轮先读片段，然后进入预览和复习。
          </p>

          <Show when={props.studyModes}>
            <div class="screen-modes">
              <div class="modes-label md-typescale-label-medium">现在有多少时间？</div>
              <ModeSelector
                modes={props.studyModes!}
                selected={props.mode ?? 'quick'}
                onSelect={m => props.onModeChange?.(m)}
                disabled={props.busy}
              />
            </div>
          </Show>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>阅读清单</span>
              <span class="stat-value">{props.listSize} 个文件</span>
            </div>
            <div class="stat-row">
              <span>可推进的文件</span>
              <span class="stat-value">{props.available} 个</span>
            </div>
            <Show when={props.active > 0}>
              <div class="stat-row">
                <span>正在制卡（会先重现）</span>
                <span class="stat-value">{props.active} 段</span>
              </div>
            </Show>
            <Show when={(props.gated ?? 0) > 0}>
              <div class="stat-row">
                <span>等卡片过预览池（暂缓推送）</span>
                <span class="stat-value">{props.gated} 段</span>
              </div>
            </Show>
          </div>

          <md-filled-button onClick={() => props.onStart()} disabled={props.busy}>
            开始阅读
          </md-filled-button>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>
              {props.skipLabel}
            </md-text-button>
          </div>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onManageList()} disabled={props.busy}>
              管理阅读清单
            </md-text-button>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
