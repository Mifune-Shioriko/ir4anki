import { Component, For, JSX } from 'solid-js'
import type { StudyModes } from '../types'
import { IconBolt, IconCheckCircle, IconTrackChanges } from './icons'

// Two-tier pacing selector (user spec 2026-09-14, sizes retuned 2026-09-16).
//   quick = 碎片时间 (canteen queue): 5 preview + 5 new + 20 review,
//           meant to be opened many times a day.
//   focus = 整块时间 (a free block): 10 + 10 + 30, roughly 2× quick.
// The CARD SIZES come from the backend wire (`study_modes`), never
// hardcoded here; this component only attaches the display copy (icon,
// name, blurb) and derives a rough time estimate from the total card count.
//
// MD3 (audit 2026-09-16): icons are official Material Symbols outlined SVGs
// (bolt / track_changes) tinted via currentColor — the previous emoji glyphs
// (⚡🎯) render platform-dependent and can't take theme colors, which breaks
// the MD3 token-driven color story. Selected state follows the MD3 list/card
// selected idiom: primary border + secondary-container fill + a leading
// indicator (check_circle). Tiles are role=radio inside role=radiogroup.

const MODE_ICONS: Record<string, (props: { size?: number }) => JSX.Element> = {
  quick: IconBolt,
  focus: IconTrackChanges,
}

const MODE_META: Record<string, { name: string; desc: string }> = {
  quick: { name: '快速', desc: '碎片时间' },
  focus: { name: '专注', desc: '整块时间' },
}

// Stable display order regardless of wire key order
const MODE_ORDER = ['quick', 'focus']

interface Props {
  modes: StudyModes
  selected: string
  onSelect: (mode: string) => void
  disabled?: boolean
}

// Rough time estimate: ~11s per card on average (reveal + read + grade),
// rounded to a friendly minute range. Keeps the user's "how long will this
// take?" question answered before they commit to a tier.
function timeLabel(total: number): string {
  if (total <= 0) return ''
  const mins = (total * 11) / 60
  const lo = Math.max(1, Math.floor(mins * 0.7))
  const hi = Math.max(lo + 1, Math.ceil(mins * 1.4))
  return lo >= hi ? `约 ${lo} 分钟` : `约 ${lo}–${hi} 分钟`
}

export const ModeSelector: Component<Props> = (props) => {
  const ordered = () =>
    MODE_ORDER.filter(k => k in props.modes).concat(
      Object.keys(props.modes).filter(k => !MODE_ORDER.includes(k)),
    )

  return (
    <div class="mode-select" role="radiogroup" aria-label="复习节奏">
      <For each={ordered()}>
        {key => {
          const m = () => props.modes[key]
          const meta = () => MODE_META[key] ?? { name: key, desc: '' }
          const sel = () => props.selected === key
          const total = () => m().preview + m().new + m().review
          const icon = () => MODE_ICONS[key]
          return (
            <div
              class="mode-tile"
              classList={{ 'mode-tile--selected': sel(), 'mode-tile--disabled': !!props.disabled }}
              role="radio"
              aria-checked={sel()}
              tabIndex={0}
              onClick={() => !props.disabled && props.onSelect(key)}
              onKeyDown={e => {
                if (props.disabled) return
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  props.onSelect(key)
                }
              }}
            >
              <div class="mode-tile__head">
                <span class="mode-tile__icon" aria-hidden="true">
                  {icon() ? icon()({ size: 20 }) : '•'}
                </span>
                <span class="mode-tile__name md-typescale-title-medium">{meta().name}</span>
                <span class="mode-tile__desc md-typescale-label-medium">{meta().desc}</span>
                <span class="mode-tile__check" aria-hidden="true">
                  <IconCheckCircle size={18} />
                </span>
              </div>
              <div class="mode-tile__sizes md-typescale-body-medium">
                {/* 渐进制卡 (2026-09-19): the read segment size rides the
                    same wire table; absent (old backend) → chip hidden */}
                {m().read != null && m().read! > 0 && (
                  <span class="mode-size">阅读 <b>{m().read}</b></span>
                )}
                <span class="mode-size">预览 <b>{m().preview}</b></span>
                <span class="mode-size">新卡 <b>{m().new}</b></span>
                <span class="mode-size">复习 <b>{m().review}</b></span>
              </div>
              <div class="mode-tile__time md-typescale-label-small">
                {total()} 张 · {timeLabel(total())}
              </div>
            </div>
          )
        }}
      </For>
    </div>
  )
}
