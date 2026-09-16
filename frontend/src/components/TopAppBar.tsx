import { Component } from 'solid-js'

// MD3 "small top app bar" (tokens: _md-comp-top-app-bar-small.scss):
// container = surface, height 64px, headline = title-large / on-surface.
// The library has no <md-top-app-bar> web component, so this is plain HTML
// styled strictly with sys tokens.
// 2026-09-17: the global-stat assist chips (待复习/新卡池/预览池) were removed
// at the user's request — the bar now shows only the "Anki" title.
export const TopAppBar: Component = () => {
  return (
    <header class="top-bar">
      <span class="top-bar__title md-typescale-title-large">Anki</span>
    </header>
  )
}
