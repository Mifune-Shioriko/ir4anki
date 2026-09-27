// Single source of truth for layout breakpoints (P2 断点收敛, 2026-09-27).
// The CSS side (index.css) mirrors these EXACT values — when changing one,
// change the other (grep index.css for "BREAKPOINTS" to find the anchors):
//
//   WIDE_MIN = 1180  →  CSS: @media (max-width: 1179px) collapses to single
//                       column; @media (min-width: 1180px) trims .screen top
//                       padding.
//
// 统一双栏 (user spec 2026-09-27 round 2): the old WIDE3 = 1500px
// three-column breakpoint is retired — 相关卡片 lives in the right column's
// tabs (SidePanelTabs) at every desktop width. Below WIDE_MIN the side
// column unmounts (single-column fallback; a 2-column layout under ~1180px
// would crush the 620px card).
export const WIDE_MIN = 1180
export const WIDE_QUERY = `(min-width: ${WIDE_MIN}px)`
