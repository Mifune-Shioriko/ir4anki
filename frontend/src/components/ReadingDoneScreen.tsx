import { Component } from 'solid-js'
import type { ReadingRoundStats } from '../types'

// Reading segment done screen (渐进制卡, 2026-09-19): shown when the reading
// round drains. Next step follows the funnel: preview (when preview mode is
// on) or review. 再读一轮 deals another reading batch (same mode).

interface Props {
  stats: ReadingRoundStats
  /** files still with a dealable frontier */
  available: number
  busy: boolean
  onMore: () => void
  onNext: () => void
  nextLabel: string
  onFinish: () => void
}

export const ReadingDoneScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">阅读完成</h1>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>本轮制卡完成</span>
              <span class="stat-value">{props.stats.done ?? 0} 段</span>
            </div>
            <div class="stat-row">
              <span>无需制卡，跳过</span>
              <span class="stat-value">{props.stats.skipped ?? 0} 段</span>
            </div>
            <div class="stat-row">
              <span>稍后继续（下轮重现）</span>
              <span class="stat-value">{props.stats.next ?? 0} 段</span>
            </div>
          </div>

          <p class="screen-detail md-typescale-body-medium">
            {props.available > 0
              ? `阅读清单还有 ${props.available} 个文件可以继续推进。`
              : '阅读清单里的片段都读完了，可以补充新文件。'}
          </p>

          <md-filled-button onClick={() => props.onNext()} disabled={props.busy}>
            {props.nextLabel}
          </md-filled-button>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onMore()} disabled={props.busy}>
              再读一轮
            </md-text-button>
          </div>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onFinish()} disabled={props.busy}>
              结束学习
            </md-text-button>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
