// JSX type declarations for @material/web custom elements used by this app.
// Only elements actually imported in md-components.ts are declared — this file
// doubles as the inventory of what the app may use.

declare module 'solid-js' {
  namespace JSX {
    interface IntrinsicElements {
      // buttons
      'md-filled-button': any
      'md-filled-tonal-button': any
      'md-outlined-button': any
      'md-text-button': any
      'md-icon-button': any

      // chips (must live inside <md-chip-set>)
      'md-chip-set': any
      'md-assist-chip': any
      'md-filter-chip': any
      'md-input-chip': any
      'md-suggestion-chip': any

      // cards
      'md-elevated-card': any

      // dialog / divider / icon
      'md-dialog': any
      'md-divider': any
      'md-icon': any

      // progress
      'md-circular-progress': any
      'md-linear-progress': any

      // switch
      'md-switch': any

      // tabs
      'md-tabs': any
      'md-primary-tab': any

      // text field
      'md-outlined-text-field': any
    }
  }
}

export {}
