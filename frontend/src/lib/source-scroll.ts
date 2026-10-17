/** One source target owns alignment until the reader interacts or it is replaced.
 * Observe layout, not a polling timer: a failed/pending image never blocks entry.
 */
export interface SourceScrollSession {
  setVisible(visible: boolean): void
  dispose(): void
}

export function sourceScroll(
  panel: HTMLElement,
  content: HTMLElement,
  anchor: HTMLElement,
  highlights: HTMLElement[],
  initiallyVisible: boolean,
): SourceScrollSession {
  let owned = true
  let disposed = false
  let visible = initiallyVisible
  let positioned = false
  let highlightHeight = 0
  let expectedScrollTop = 0
  let contentHeight = 0
  let contentWidth = 0
  let panelHeight = 0
  let panelWidth = 0
  let frame: number | undefined
  let flashTimer: ReturnType<typeof setTimeout> | undefined

  const clearFlash = () => {
    clearTimeout(flashTimer)
    flashTimer = undefined
    highlights.forEach(el => el.classList.remove('note-hl'))
  }
  const flash = () => {
    clearFlash()
    // Restart the existing CSS fade after meaningful exposure/correction.
    void anchor.offsetWidth
    highlights.forEach(el => el.classList.add('note-hl'))
    flashTimer = setTimeout(clearFlash, 2800)
  }
  const align = () => {
    frame = undefined
    if (!owned || disposed || !visible || !panel.isConnected || !anchor.isConnected
      || panel.getClientRects().length === 0 || panel.clientHeight === 0) return
    const box = panel.getBoundingClientRect()
    const target = anchor.getBoundingClientRect()
    const inset = panel.clientTop + (parseFloat(getComputedStyle(panel).paddingTop) || 0)
    const top = Math.max(0, Math.min(panel.scrollHeight - panel.clientHeight,
      panel.scrollTop + target.top - box.top - inset))
    const moved = Math.abs(top - panel.scrollTop) > 1
    if (moved) {
      // Explicit instant scrolling also overrides CSS smooth scrolling. It avoids
      // chasing an obsolete animation endpoint and respects reduced motion.
      // Only this panel moves; scrollIntoView would move outer ancestors too.
      panel.scrollTo({ top, behavior: 'instant' })
    }
    const height = highlights.reduce((sum, el) => sum + el.getBoundingClientRect().height, 0)
    if (!positioned || moved || Math.abs(height - highlightHeight) > 1) flash()
    positioned = true
    highlightHeight = height
    expectedScrollTop = panel.scrollTop
    const layout = content.getBoundingClientRect()
    contentHeight = layout.height
    contentWidth = layout.width
    panelHeight = panel.clientHeight
    panelWidth = panel.clientWidth
  }
  const schedule = () => {
    if (owned && !disposed && visible && frame === undefined) frame = requestAnimationFrame(align)
  }
  const observer = new ResizeObserver(schedule)
  observer.observe(panel)
  // A scroll container's own box does not grow when images above its target do.
  observer.observe(content)
  const stopTracking = () => {
    owned = false
    if (frame !== undefined) cancelAnimationFrame(frame)
    frame = undefined
    observer.disconnect()
    panel.removeEventListener('load', schedule, true)
    panel.removeEventListener('error', schedule, true)
    panel.removeEventListener('wheel', onWheel, true)
    panel.removeEventListener('touchstart', onTouch, true)
    panel.removeEventListener('pointerdown', onPointer, true)
    panel.removeEventListener('keydown', onKey, true)
    panel.removeEventListener('scroll', onScroll)
  }
  const onScroll = () => {
    if (!owned || !visible || !positioned || Math.abs(panel.scrollTop - expectedScrollTop) <= 1) return
    const layout = content.getBoundingClientRect()
    // Native scrollbar gestures need not dispatch DOM pointer events. An
    // unexpected scroll on unchanged content transfers ownership to the reader.
    // Image/font growth changes these dimensions, so browser scroll anchoring
    // during layout is not mistaken for such a gesture.
    if (Math.abs(layout.height - contentHeight) <= 1 && Math.abs(layout.width - contentWidth) <= 1
      && panel.clientHeight === panelHeight && panel.clientWidth === panelWidth) stopTracking()
  }
  const onWheel = (event: WheelEvent) => {
    if (event.deltaX || event.deltaY) stopTracking()
  }
  const onTouch = () => stopTracking()
  const onPointer = (event: PointerEvent) => {
    // Selection/dragging and native scrollbar interaction are reading intent too.
    if (event.button === 0 || event.button === 1) stopTracking()
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
    if ((event.target as Element | null)?.closest('input, textarea, select, [contenteditable="true"]')) return
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) stopTracking()
  }
  panel.addEventListener('load', schedule, true)
  panel.addEventListener('error', schedule, true)
  panel.addEventListener('wheel', onWheel, { capture: true, passive: true })
  panel.addEventListener('touchstart', onTouch, { capture: true, passive: true })
  panel.addEventListener('pointerdown', onPointer, { capture: true, passive: true })
  panel.addEventListener('keydown', onKey, true)
  panel.addEventListener('scroll', onScroll, { passive: true })
  schedule()

  return {
    setVisible(value) {
      visible = value
      if (!visible && frame !== undefined) {
        cancelAnimationFrame(frame)
        frame = undefined
      }
      // Does not renew ownership: an unchanged pane preserves reader position.
      if (visible) schedule()
    },
    dispose() {
      if (disposed) return
      disposed = true
      stopTracking()
      clearFlash()
    },
  }
}
