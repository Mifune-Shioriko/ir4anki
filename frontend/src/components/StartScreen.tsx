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
  /** preview pool size; a small note is shown when cards await 明日自动放行
   * (2026-09-27: the manual preview stage is gone, releases are automatic) */
  previewPool?: number | null
  /** cards made TODAY, still in the pool (auto-release tomorrow) */
  pendingRelease?: number | null
  /** preview feature flag — the pool row is hidden when off */
  previewMode?: boolean
  /** reading segments available this round (渐进制卡) */
  readingAvailable?: number
}

// Single unified start screen (user spec 2026-09-24): one button runs the
// whole daily chain — 阅读 → 复习 — then returns here. 2026-09-27 桌面重设计:
// headline-medium emphasized title, tabular emphasized stat values, keyboard
// shortcut hints (Space / 1-4 / Ctrl+Z already exist in App — this makes
// them discoverable), and first-run empty-state guidance (a fresh
// collection with nothing due used to show "— 张" and a 开始 button that
// deals an empty round).
export const StartScreen: Component<Props> = (props) => {
  const sizes = () => {
    const t = props.studyModes
    const m = props.mode ?? 'daily'
    return t && t[m] ? t[m] : null
  }
  // nothing anywhere to study TODAY: no due, no new pool, no preview pool
  // awaiting tomorrow's release, no reading segments
  const isEmpty = () =>
    (props.due ?? 0) === 0 &&
    (props.newTotal ?? 0) === 0 &&
    (props.previewPool ?? 0) === 0 &&
    (props.readingAvailable ?? 0) === 0
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-medium">开始学习</h1>
          <p class="screen-detail md-typescale-body-medium">
            一轮依次推进：阅读笔记 → 复习。中途不用选，做完自动进入下一段，全部完成回到这里。今天制的卡进预览池，明天自动放行为新卡。
          </p>

          <Show when={isEmpty() && props.due != null}>
            <div class="screen-empty md-typescale-body-medium">
              牌组里暂时没有可学的东西。可以先：在桌面 Anki 里建牌组导入卡片，或在「阅读清单」里加入 markdown 笔记开始制卡。
            </div>
          </Show>

          <Show when={sizes()}>
            <div class="screen-stats md-typescale-body-medium">
              <div class="plan-head md-typescale-label-large">今日每轮计划</div>
              <Show when={(props.readingAvailable ?? 0) > 0}>
                <div class="stat-row">
                  <span>阅读</span>
                  <span class="stat-value">{sizes()!.read ?? 0} 段</span>
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
                <span>预览池（明日自动放行）</span>
                <span class="stat-value">
                  {props.previewPool} 张
                  <Show when={(props.pendingRelease ?? 0) > 0}>
                    {' '}（今日新制 {props.pendingRelease}）
                  </Show>
                </span>
              </div>
            </Show>
          </div>

          <md-filled-button class="screen-start-button" onClick={() => props.onBegin()} disabled={props.busy}>
            开始
          </md-filled-button>

          <div class="screen-keys md-typescale-label-small">
            <span><kbd>Space</kbd> 显示答案</span>
            <span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd><kbd>4</kbd> 评分</span>
            <span><kbd>Ctrl+Z</kbd> 撤销</span>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
