import { Component, For, Show, createEffect, createSignal, onCleanup } from 'solid-js'
import renderMathInElement from 'katex/contrib/auto-render'
import 'katex/dist/katex.min.css'
import {lectureRequest,pageRequestPath,type PageLocator,type PageSource} from '../lib/lecture-api'
import {relatedDetails,sourcePage} from '../lib/lecture-session'
import { api } from '../api'
import { MARKDOWN_TAG, renderField, renderClozeField } from '../lib/cloze'
import { KATEX_OPTS } from '../lib/math'
import type { ReadingChunk, ReadingCreatedCard } from '../types'

// 左栏「相关卡片」(三栏重构, user spec 2026-09-21).
//
// NOT anki-rag similarity (that project is retired — 匹配准确度达不到要求).
// 相关卡片 here means: every card made from the SAME note segment — exact
// provenance. Two feeding modes:
//   reading round  → the current chunk's cards_created + ancestor_cards
//                    (a split container keeps pre-cut provenance forever)
//   review/preview → the current card's note → GET /api/reading/source →
//                    the segment's cards_created merged with every ancestor
//                    crumb's (same pre-split rule); 404 = 无来源 orphan
// The current card (review/preview mode) is highlighted in the list.
//
// Shell (2026-09-27, header row deleted 2026-09-29): plain flat div — the
// md-elevated-card container is gone, cards sit DIRECTLY on the column
// background (「背景 - 卡片」), and the panel starts straight with the state
// rows / list (the old 本片段已制卡片 + N 张 header just duplicated the tab
// label above it).
//
// Fail-soft like every enrichment panel: dead backend → count line only.

interface Props {
  /** reading-round mode: the chunk on screen (mutually exclusive with noteId) */
  chunk?: ReadingChunk | null
  pageSource?: PageLocator | null
  /** review/preview mode: resolve provenance for this note. Gated on
   *  reveal by the App (noteCard()) — face-down cards pass null so the
   *  list can't leak which segment the answer sits in. */
  noteId?: number | null
  /** forces a full reset even when noteId repeats (card swap) */
  cardKey?: number | null
  /** noteId given but card face-down: show the 先想一想 placeholder */
  blocked?: boolean
  /** bump to refetch after a card add (optimistic ids may be placeholders) */
  refreshToken?: number
}

type Status = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export const RelatedPanel: Component<Props> = (props) => {
  const [status, setStatus] = createSignal<Status>('idle')
  const [cards, setCards] = createSignal<ReadingCreatedCard[]>([])
  const [count, setCount] = createSignal<number | null>(null)
  // note id of the card currently on screen (highlight in review/preview)
  const [selfNote, setSelfNote] = createSignal<number | null>(null)
  let listRef: HTMLDivElement | undefined
  let reqToken = 0
  onCleanup(()=>{++reqToken})

  // merge chunk.cards_created + ancestor_cards (reading mode) — dedup, own
  // cards first (provenance order = creation order)
  const chunkIds = (): number[] => {
    const c = props.chunk
    if (!c) return []
    const own = (c.cards_created || []).filter(id => id > 0)
    const anc = (c.ancestor_cards || []).filter(id => id > 0 && !own.includes(id))
    return [...own, ...anc]
  }

  const refresh = async () => {
    const token = ++reqToken
    setCards([]);setCount(null);setSelfNote(null)
    const chunk=props.chunk
    if(!chunk&&!props.noteId){setStatus('idle');return}
    setStatus('loading')
    try{
      let ids:number[]=[]
      const source=chunk??await api.readingSource(props.noteId!)
      if(token!==reqToken)return
      if(!source){setStatus('empty');return}
      if(!chunk)setSelfNote(props.noteId!)
      if(source.kind==='pdf'){
        const pdf=source as unknown as PageSource
        const selected=chunk?props.pageSource:null
        const page=selected&&selected.document_id===pdf.document_id&&selected.revision_id===pdf.revision_id&&selected.segment_id===pdf.segment_id?selected.page:sourcePage(pdf)
        const result=await lectureRequest<{note_ids:number[]}>(pageRequestPath(pdf,page)+'/related')
        if(token!==reqToken)return
        ids=result.note_ids
      }else if(chunk){ids=chunkIds()}
      else{
        const historical=source as Awaited<ReturnType<typeof api.readingSource>>
        for(const list of [historical!.cards_created,...historical!.breadcrumb.map(b=>b.cards_created)])for(const id of list??[])if(id>0&&!ids.includes(id))ids.push(id)
      }
      setCount(ids.length)
      const result=await relatedDetails(ids,api.readingCards)
      if(token!==reqToken)return
      setCards(result);setStatus('ready')
    }catch{if(token===reqToken)setStatus('error')}
  }

  createEffect(() => {
    props.pageSource // exact left-reader identity, never reference-pane scroll
    props.chunk // document/revision changes reset even for repeating local keys
    props.chunk?.chunk_key // track
    props.chunk?.cards_created.length // track (optimistic add bumps it)
    props.noteId // track
    props.cardKey // track (card swap with the same note still resets)
    props.refreshToken // track
    void refresh()
  })

  // math in the fetched card HTML (same pattern as Flashcard)
  createEffect(() => {
    cards()
    requestAnimationFrame(() => {
      if (listRef) renderMathInElement(listRef, KATEX_OPTS)
    })
  })

  // first field per kind: qa → 正面, cloze → cloze-rendered 文字
  const cardQuestion = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      return renderClozeField(fields['文字'] ?? Object.values(fields)[0] ?? '', 'q', c.tags)
    }
    return renderField(fields['正面'] ?? Object.values(fields)[0] ?? '', c.tags)
  }
  const cardAnswer = (c: ReadingCreatedCard): string => {
    const fields = c.fields || {}
    if (c.kind === 'cloze') {
      return renderClozeField(fields['文字'] ?? Object.values(fields)[0] ?? '', 'a', c.tags)
    }
    const back = fields['背面'] ?? Object.values(fields)[1] ?? ''
    return back ? renderField(back, c.tags) : ''
  }

  return (
    <div class="note-panel related-panel">
      {/* the header row (title + N 张 count) is gone (user spec 2026-09-29)
          — the tab label already says 相关卡片 */}
        <Show when={status() === 'loading'}>
          <div class="note-panel-state">
            <md-circular-progress indeterminate style="--md-circular-progress-size:32px" />
          </div>
        </Show>

        <Show when={status() === 'idle'}>
          <div class="note-panel-state md-typescale-body-medium">
            {props.blocked ? (
              <>
                先想一想
                <span class="note-panel-hint md-typescale-body-small">
                  显示背面后，这里会列出同一笔记片段制的其他卡片
                </span>
              </>
            ) : (
              '开始复习后，这里会列出同一笔记片段制的其他卡片'
            )}
          </div>
        </Show>

        <Show when={status() === 'empty'}>
          <div class="note-panel-state note-panel-empty md-typescale-body-medium">
            无来源
            <span class="note-panel-hint md-typescale-body-small">
              这张卡不是从阅读模式制的，没有同片段卡片
            </span>
          </div>
        </Show>

        <Show when={status() === 'error'}>
          <div class="note-panel-state md-typescale-body-medium">
            {(count() ?? 0) > 0
              ? `已制 ${count()} 张（内容加载失败）`
              : '服务不可用'}
            <md-text-button onClick={() => refresh()}>重试</md-text-button>
          </div>
        </Show>

        <Show when={status() === 'ready'}>
          <Show when={cards().length === 0} fallback={
            <div class="related-list" ref={listRef}>
              <For each={cards()}>
                {c => (
                  <div
                    class={`similar-item related-item${c.noteId === selfNote() ? ' related-item--self' : ''}`}
                  >
                    <div class="similar-q md-typescale-body-medium">
                      <span innerHTML={cardQuestion(c)} />
                      <span class="similar-score md-typescale-label-small">
                        {c.kind === 'cloze' ? `挖空 ×${c.numCards}` : '问答'}
                      </span>
                    </div>
                    <Show when={cardAnswer(c)}>
                      <div class="similar-a md-typescale-body-small" innerHTML={cardAnswer(c)} />
                    </Show>
                    <Show when={c.tags.some(t => t !== MARKDOWN_TAG)}>
                      <div class="reading-made-tags md-typescale-label-small">
                        {c.tags.filter(t => t !== MARKDOWN_TAG).map(t => `#${t}`).join(' ')}
                      </div>
                    </Show>
                  </div>
                )}
              </For>
            </div>
          }>
            <div class="note-panel-state md-typescale-body-medium">
              还没有从{props.chunk ? '本片段' : '这个片段'}制过卡片
            </div>
          </Show>
        </Show>
    </div>
  )
}
