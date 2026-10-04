/**
 * Dialog close-guard (user spec 2026-10-05).
 *
 * md-dialog closes on ANY scrim (backdrop) click — with a big centered
 * editor dialog a stray click next to the panel instantly threw away the
 * user's edits. This guard makes scrim clicks a no-op while keeping the
 * explicit exits:
 *   - 取消 / 保存 buttons (component code calls props.onClose())
 *   - Escape (handled here — md-dialog routes Esc through the same
 *     cancelable `cancel` event as a scrim click, so we block `cancel`
 *     wholesale and re-implement Escape ourselves)
 *
 * Attach in the dialog's ref callback, BEFORE it opens:
 *   <md-dialog ref={el => applyDialogGuard(el, () => props.onClose())}>
 */
export function applyDialogGuard(
  dialogEl: HTMLElement,
  onClose: () => void,
): void {
  // Block both close paths md-dialog funnels into `cancel`: the scrim
  // click (handleDialogClick dispatches it on the host) and the native
  // Escape key (redispatched from the inner <dialog>).
  dialogEl.addEventListener('cancel', e => e.preventDefault())
  // keydown is composed — the listener on the host sees keys pressed
  // anywhere inside the dialog (focus trap keeps them there).
  dialogEl.addEventListener('keydown', e => {
    if ((e as KeyboardEvent).key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  })
}
