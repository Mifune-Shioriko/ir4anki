// MD3 theme bootstrap — official pattern:
//   1. import component definitions (production: per-component, not all.js)
//   2. push the official typescale stylesheet into adoptedStyleSheets
//   3. define the color scheme via --md-sys-color-* tokens (tokens.css,
//      generated verbatim from @material/web's own v0_192 baseline tables)
//   4. set typefaces via --md-ref-typeface-* tokens
import './md-components'
import './tokens.css'
import { styles as typescaleStyles } from '@material/web/typography/md-typescale-styles.js'

document.adoptedStyleSheets.push(typescaleStyles.styleSheet)

const CJK_STACK = `'Roboto', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif`
document.documentElement.style.setProperty('--md-ref-typeface-brand', CJK_STACK)
document.documentElement.style.setProperty('--md-ref-typeface-plain', CJK_STACK)

// Sync dark mode class + browser chrome color with the OS preference.
// tokens.css carries the matching `.dark` scheme overrides.
export function syncThemeColor() {
  const meta =
    document.querySelector('meta[name="theme-color"]') ||
    (() => {
      const m = document.createElement('meta')
      m.name = 'theme-color'
      document.head.appendChild(m)
      return m
    })()

  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const apply = () => {
    document.documentElement.classList.toggle('dark', mq.matches)
    meta.setAttribute('content', mq.matches ? '#141218' : '#fef7ff')
  }
  apply()
  mq.addEventListener('change', apply)
  return () => mq.removeEventListener('change', apply)
}
