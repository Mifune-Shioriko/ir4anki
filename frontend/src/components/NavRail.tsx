import { Component } from 'solid-js'
import { IconSchool, IconFolder, IconMenuBook } from './icons'
import type { IconProps } from './icons'

// Left navigation rail (user spec 2026-09-19 round 3): replaces the old
// "Anki" top bar. Three destinations — 学习 (the whole study funnel),
// 文件 (read-only note browser), 阅读清单 (reading-list manager). The rail
// is always visible; the current section is highlighted (MD3 nav-rail
// idiom: pill indicator via secondary-container).
export type Section = 'study' | 'files' | 'readingList'

interface Props {
  section: Section
  onNavigate: (s: Section) => void
  /** badge count on the 阅读清单 destination (files in the list) */
  readingListSize?: number
}

const ITEMS: { key: Section; label: string; icon: Component<IconProps> }[] = [
  { key: 'study', label: '学习', icon: IconSchool },
  { key: 'files', label: '文件', icon: IconFolder },
  { key: 'readingList', label: '阅读清单', icon: IconMenuBook },
]

export const NavRail: Component<Props> = (props) => {
  return (
    <nav class="nav-rail" aria-label="主导航">
      {ITEMS.map(item => {
        const Icon = item.icon
        return (
          <button
            type="button"
            class="nav-rail__item"
            classList={{ 'nav-rail__item--active': props.section === item.key }}
            onClick={() => props.onNavigate(item.key)}
            aria-current={props.section === item.key ? 'page' : undefined}
            title={item.label}
          >
            <span class="nav-rail__indicator">
              <Icon size={24} />
            </span>
            <span class="nav-rail__label md-typescale-label-small">
              {item.label}
              {item.key === 'readingList' && props.readingListSize
                ? ` (${props.readingListSize})`
                : ''}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
