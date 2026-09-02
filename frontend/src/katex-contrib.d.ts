// katex ships types for the main entry but not for contrib/auto-render.
declare module 'katex/contrib/auto-render' {
  interface AutoRenderDelimiter {
    left: string
    right: string
    display: boolean
  }
  interface AutoRenderOptions {
    delimiters?: AutoRenderDelimiter[]
    ignoredTags?: string[]
    ignoredClasses?: string[]
    throwOnError?: boolean
    errorColor?: string
  }
  export default function renderMathInElement(
    elem: HTMLElement,
    options?: AutoRenderOptions,
  ): void
}
