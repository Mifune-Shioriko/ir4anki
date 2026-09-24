import { Component, Show } from 'solid-js'
import type { StudyModes } from '../types'

interface Props {
  due: number | null
  newTotal: number | null
  busy: boolean
  onBegin: () => void
  /** resolved single-tier pacing table (2026-09-24) — today's plan comes from
   * the wire, never hardcoded. `mode` selects the row (only "daily" now). */
  studyModes?: StudyModes | null
  mode?: string
  /** preview pool size; a small note is shown when cards await 放行 */
  previewPool?: number | null
  /** preview feature flag — the plan row is hidden when off (pipeline skips
   * the preview stage entirely) */
  previewMode?: boolean
  /** reading segments available this round (渐进制卡) */
  readingAvailable?: number
}

// Single unified start screen (user spec 2026-09-24): the quick/focus tiers
// and every intermediate stats/选择 page are gone. One button runs the whole
// daily chain — 阅读 → 新卡预览 → 复习 — then returns here. The card just
// shows TODAY'S PLAN (sizes from the wire) so the user knows what they're
// about to do; no per-stage picking.
export const StartScreen: Component<Props> = (props) => {
  const sizes = () => {
    const t = props.studyModes
    const m = props.mode ?? 'daily'
    return t && t[m] ? t[m] : null
  }
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">开始学习</h1>
          <p class="screen-detail md-typescale-body-medium">
            一轮依次推进：阅读笔记 → 预览新卡 → 复习。中途不用选，做完自动进入下一段，全部完成回到这里。
          </p>

          <Show when={sizes()}>
            <div class="screen-stats md-typescale-body-medium">
              <div class="plan-head md-typescale-label-medium">今日每轮计划</div>
              <Show when={(props.readingAvailable ?? 0) > 0}>
                <div class="stat-row">
                  <span>阅读</span>
                  <span class="stat-value">{sizes()!.read ?? 0} 段</span>
                </div>
              </Show>
              <Show when={props.previewMode !== false}>
                <div class="stat-row">
                  <span>预览新卡（先看后考）</span>
                  <span class="stat-value">{sizes()!.preview} 张</span>
                </div>
              </Show>
              <div class="stat-row">
                <span>复习（新卡 + 到期）</span>
                <span class="stat-value">
                  {sizes()!.new} 新 + {sizes()!.review > 0 ? sizes()!.review : '—'} 到期
                </span>
              </div>
            </div>
          </Show>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>牌组新卡池</span>
              <span class="stat-value">{props.newTotal ?? '—'} 张</span>
            </div>
            <Show when={props.previewPool != null && props.previewPool! > 0}>
              <div class="stat-row">
                <span>预览池待放行</span>
                <span class="stat-value">{props.previewPool} 张</span>
              </div>
            </Show>
          </div>

          <md-filled-button onClick={() => props.onBegin()} disabled={props.busy}>
            开始
          </md-filled-button>
        </div>
      </md-elevated-card>
    </div>
  )
}
