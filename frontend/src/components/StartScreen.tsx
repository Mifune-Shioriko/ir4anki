import { Component, Show } from 'solid-js'
import type { StudyModes, RoundSummary } from '../types'
import { IconCheckCircle } from './icons'

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
  /** reading segments the next round will deal (渐进制卡) — two-slot
   * dealing since 2026-10-09: every 阅读清单 file contributes up to two
   * segments (reading_dealable on the wire), so this count IS the round
   * size (all card-making quotas retired) */
  readingAvailable?: number
  /** what the round that just ended accomplished (hero summary strip,
   * 2026-09-27 round 2); null until a round completes this session */
  roundSummary?: RoundSummary | null
}

// Single unified start screen (user spec 2026-09-24): one button runs the
// whole daily chain — 阅读 → 复习 — then returns here. 2026-09-27 桌面重设计:
// headline-medium emphasized title, tabular emphasized stat values, and
// first-run empty-state guidance (a fresh collection with nothing due used
// to show "— 张" and a 开始 button that deals an empty round). The keyboard
// shortcut hint row the redesign first added here was REMOVED on user
// request (round 2) — shortcuts stay discoverable via the keycaps ON the
// ease buttons (ActionArea), which is where they actually apply.
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
          <Show when={props.roundSummary}>
            {/* hero moment (P2, 2026-09-27 round 2): the round that just
                finished gets ONE big celebratory number + the stage
                breakdown — replaces the easy-to-miss 4s snackbar. Only
                shows stages that actually ran (reading-only or
                review-only rounds don't list a zero row). */}
            <div class="round-summary">
              <span class="round-summary__hero" aria-hidden="true">
                <IconCheckCircle size={40} />
              </span>
              <div class="round-summary__title md-typescale-headline-small">
                本轮完成
              </div>
              <div class="round-summary__stats">
                <Show when={(props.roundSummary!.readingDone ?? 0) + (props.roundSummary!.readingSkipped ?? 0) > 0}>
                  <div class="round-summary__stat">
                    <span class="round-summary__num md-typescale-headline-medium">
                      {(props.roundSummary!.readingDone ?? 0) + (props.roundSummary!.readingSkipped ?? 0)}
                    </span>
                    <span class="round-summary__label md-typescale-label-small">阅读段处理</span>
                    <Show when={(props.roundSummary!.readingSkipped ?? 0) > 0}>
                      <span class="round-summary__sub md-typescale-label-small">
                        其中跳过 {props.roundSummary!.readingSkipped} 段
                      </span>
                    </Show>
                  </div>
                </Show>
                <Show when={(props.roundSummary!.reviewed ?? 0) > 0}>
                  <div class="round-summary__stat">
                    <span class="round-summary__num md-typescale-headline-medium">
                      {props.roundSummary!.reviewed ?? 0}
                    </span>
                    <span class="round-summary__label md-typescale-label-small">卡片复习</span>
                    <Show when={(props.roundSummary!.newReviewed ?? 0) > 0}>
                      <span class="round-summary__sub md-typescale-label-small">
                        含新卡 {props.roundSummary!.newReviewed} 张
                      </span>
                    </Show>
                  </div>
                </Show>
              </div>
              <md-divider />
            </div>
          </Show>
          <h1 class="screen-title md-typescale-headline-medium">开始学习</h1>
          <p class="screen-detail md-typescale-body-medium">
            一轮依次推进：阅读笔记 → 复习。阅读阶段每篇文章最多推进两段，全部过完自动进入复习；复习清掉全部到期卡和当日放行的新卡。今天制的卡进预览池，明天自动放行为新卡。
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
                  <span>阅读（每篇最多两段）</span>
                  <span class="stat-value">{props.readingAvailable} 段</span>
                </div>
              </Show>
              <div class="stat-row">
                <span>复习（新卡 + 到期）</span>
                <span class="stat-value">
                  {sizes()!.new > 0 ? sizes()!.new : '全部'} 新 + {sizes()!.review > 0 ? sizes()!.review : '—'} 到期
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
        </div>
      </md-elevated-card>
    </div>
  )
}
