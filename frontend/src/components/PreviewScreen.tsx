import { Component, Show } from 'solid-js'
import { ModeSelector } from './ModeSelector'
import type { StudyModes } from '../types'

// Preview-mode start screen (先想后看). Shown instead of the study start
// screen when preview mode is on: its job is to make the zero-stakes nature
// of preview explicit — try to recall first, then check; no grading, no
// wrong answers. Approved cards are released the NEXT day (2026-09-07).
interface Props {
  pool: number | null
  available: number | null
  due: number | null
  /** approved today, still suspended — released into the queue tomorrow */
  pendingRelease?: number | null
  /** daily 放行 goal (2026-09-16): horizontal progress bar of
   * pendingRelease / goal; null (old backend) hides the bar */
  releaseDailyGoal?: number | null
  busy: boolean
  onStart: () => void
  onSkipToReview: () => void
  /** two-tier pacing (2026-09-14): table from the wire + current selection */
  studyModes?: StudyModes | null
  mode?: string
  onModeChange?: (mode: string) => void
}

export const PreviewScreen: Component<Props> = (props) => {
  // 放行进度 = 今天已放行 (pending_release) / 每日目标 — same number the
  // 「今天已放行（明天开考）」 stat row shows, so the two never disagree.
  const goal = () => props.releaseDailyGoal ?? null
  const released = () => props.pendingRelease ?? 0
  const releaseFraction = () => {
    const g = goal()
    return g && g > 0 ? Math.min(released() / g, 1) : 0
  }
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">预览新卡</h1>
          <p class="screen-detail md-typescale-body-medium">
            先想后看：只显示问题，自己先试着回忆，再点开答案对照。不评分，没有答错这回事。
            看完选「放行」，它会在<span class="em">明天</span>进入学习队列——睡一觉再考，记得才牢。
          </p>

          <Show when={goal() != null && goal()! > 0}>
            <div class="release-progress" role="group" aria-label="今日新卡放行进度">
              <div class="release-progress__head">
                <span class="md-typescale-label-medium">今日新卡放行</span>
                <span class="release-progress__count md-typescale-label-medium">
                  {released()} / {goal()} 张
                </span>
              </div>
              <md-linear-progress
                class="release-progress__bar"
                value={releaseFraction()}
                aria-valuenow={released()}
                aria-valuemin={0}
                aria-valuemax={goal()!}
              />
            </div>
          </Show>

          <Show when={props.studyModes}>
            <div class="screen-modes">
              <div class="modes-label md-typescale-label-medium">
                现在有多少时间？
              </div>
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
              <span>预览池待预览</span>
              <span class="stat-value">{props.pool ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>今天可预览</span>
              <span class="stat-value">{props.available ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
            <Show when={(props.pendingRelease ?? 0) > 0}>
              <div class="stat-row">
                <span>今天已放行（明天开考）</span>
                <span class="stat-value">{props.pendingRelease} 张</span>
              </div>
            </Show>
          </div>

          <div class="screen-actions">
            <md-filled-button onClick={() => props.onStart()} disabled={props.busy}>
              开始预览
            </md-filled-button>
          </div>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onSkipToReview()} disabled={props.busy}>
              跳过，直接复习
            </md-text-button>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
