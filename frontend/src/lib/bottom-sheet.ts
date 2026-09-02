/**
 * Bottom-sheet open/close animations for md-dialog.
 *
 * md-dialog officially supports overriding `getOpenAnimation` /
 * `getCloseAnimation` (properties on the Dialog element). We replace the
 * default "slide down from top" with a slide-up-from-bottom sheet motion,
 * keeping the official easing and the scrim fade.
 */
import type { DialogAnimation } from '@material/web/dialog/internal/animations.js'

// Official emphasized easing (internal/motion/animation.js)
const EMPHASIZED = 'cubic-bezier(.3,0,0,1)'
const EMPHASIZED_ACCELERATE = 'cubic-bezier(.3,0,.8,.15)'

export const BOTTOM_SHEET_OPEN: DialogAnimation = {
  dialog: [
    // slide up from the bottom edge
    [
      [{ transform: 'translateY(100%)' }, { transform: 'translateY(0)' }],
      { duration: 400, easing: EMPHASIZED },
    ],
  ],
  scrim: [
    [[{ opacity: 0 }, { opacity: 0.32 }], { duration: 300, easing: 'linear' }],
  ],
  headline: [
    [
      [{ opacity: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1 }],
      { duration: 350, easing: 'linear', fill: 'forwards' },
    ],
  ],
  content: [
    [
      [{ opacity: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1 }],
      { duration: 350, easing: 'linear', fill: 'forwards' },
    ],
  ],
  actions: [
    [
      [{ opacity: 0 }, { opacity: 0, offset: 0.5 }, { opacity: 1 }],
      { duration: 400, easing: 'linear', fill: 'forwards' },
    ],
  ],
}

export const BOTTOM_SHEET_CLOSE: DialogAnimation = {
  dialog: [
    [
      [{ transform: 'translateY(0)' }, { transform: 'translateY(100%)' }],
      { duration: 200, easing: EMPHASIZED_ACCELERATE },
    ],
  ],
  scrim: [
    [[{ opacity: 0.32 }, { opacity: 0 }], { duration: 200, easing: 'linear' }],
  ],
  headline: [
    [[{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: 'linear', fill: 'forwards' }],
  ],
  content: [
    [[{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: 'linear', fill: 'forwards' }],
  ],
  actions: [
    [[{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: 'linear', fill: 'forwards' }],
  ],
}

/** Attach the bottom-sheet animations to an md-dialog element. */
export function applyBottomSheetAnimation(dialogEl: HTMLElement) {
  const dlg = dialogEl as HTMLElement & {
    getOpenAnimation: () => DialogAnimation
    getCloseAnimation: () => DialogAnimation
  }
  dlg.getOpenAnimation = () => BOTTOM_SHEET_OPEN
  dlg.getCloseAnimation = () => BOTTOM_SHEET_CLOSE
}
