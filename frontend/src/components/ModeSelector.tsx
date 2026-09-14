import { Component, For } from 'solid-js'
import type { StudyModes } from '../types'

// Two-tier pacing selector (user spec 2026-09-14).
//   quick = 碎片时间 (canteen queue): the tiny 1 preview + 1 new + 4 review
//           rhythm, meant to be opened many times a day.
//   focus = 整块时间 (a free block): exactly 5× quick — 5 + 5 + 20.
// The CARD SIZES come from the backend wire (`study_modes`), never
// hardcoded here; this component only attaches the display copy (icon,
// name, blurb) and derives a rough time estimate from the total card count.
//
// Rendered as two selectable tiles. The selected tile gets a primary border
// + tinted background + a check dot (MD3 selected-state idiom). Clicking a
// tile fires onSelect; App persists the choice in localStorage so the next
// open pre-selects it.

const MODE_META: Record<string, { icon: string; name: string; desc: string }> = {
  quick: { icon: '⚡', name: '快速', desc: '碎片时间' },
  focus: { icon: '🎯', name: '专注', desc: '整块时间' },
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
          const meta = () => MODE_META[key] ?? { icon: '•', name: key, desc: '' }
          const sel = () => props.selected === key
          const total = () => m().preview + m().new + m().review
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
                <span class="mode-tile__icon" aria-hidden="true">{meta().icon}</span>
                <span class="mode-tile__name md-typescale-title-medium">{meta().name}</span>
                <span class="mode-tile__desc md-typescale-label-medium">{meta().desc}</span>
                <span class="mode-tile__check" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="18" height="18">
                    <path
                      fill="currentColor"
                      d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"
                    />
                  </svg>
                </span>
              </div>
              <div class="mode-tile__sizes md-typescale-body-medium">
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
