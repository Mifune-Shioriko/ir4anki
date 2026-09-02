import { Component, Show, createEffect, createSignal } from 'solid-js'
import { api } from './api'
import type { Card } from './types'
import { syncThemeColor } from './theme'

import { TopAppBar } from './components/TopAppBar'
import { ProgressBar } from './components/ProgressBar'
import { Flashcard } from './components/Flashcard'
import { ActionArea } from './components/ActionArea'
import { StartScreen } from './components/StartScreen'
import { DoneScreen } from './components/DoneScreen'
import { EmptyScreen } from './components/EmptyScreen'
import { FinishedScreen } from './components/FinishedScreen'
import { EditDialog } from './components/EditDialog'
import { Loading } from './components/Loading'

type Phase = 'loading' | 'start' | 'review' | 'done' | 'empty' | 'finished'

export const App: Component = () => {
  createEffect(() => syncThemeColor())

  // ---- phase & loading text ----
  const [phase, setPhase] = createSignal<Phase>('loading')
  const [loadText, setLoadText] = createSignal('正在加载…')

  // ---- round state ----
  const [cards, setCards] = createSignal<Card[]>([])
  const [idx, setIdx] = createSignal(0)
  const [revealed, setRevealed] = createSignal(false)
  const [answering, setAnswering] = createSignal(false)
  const [finishing, setFinishing] = createSignal(false)
  // roundTotal is the round's ORIGINAL size (done + pending) — from the
  // server, never cards().length after a mid-round resume.
  const [roundTotal, setRoundTotal] = createSignal(0)
  const [totalDone, setTotalDone] = createSignal(0)

  // ---- global stats (top bar chips) ----
  const [due, setDue] = createSignal<number | null>(null)
  const [newQuotaLeft, setNewQuotaLeft] = createSignal<number | null>(null)
  const [newQuotaTotal, setNewQuotaTotal] = createSignal<number | null>(null)
  const [newTotal, setNewTotal] = createSignal<number | null>(null)

  // ---- review/new split for the current batch (front-end only) ----
  // Batch composition is snapshotted at load time; live done counts derive
  // from what is still left in cards() — answered cards are REMOVED from
  // the array (the array mirrors the backend's pending list 1:1).
  const [batchNewTotal, setBatchNewTotal] = createSignal(0)
  const [batchReviewTotal, setBatchReviewTotal] = createSignal(0)
  const reviewTotal = () => batchReviewTotal()
  const newInBatch = () => batchNewTotal()
  const newDone = () => batchNewTotal() - cards().filter(c => c.isNew).length
  const reviewDone = () => batchReviewTotal() - cards().filter(c => !c.isNew).length

  // ---- undo ----
  // Availability mirrors the backend's single undo slot (round.json "last"):
  // on after each answer, off once undone, no time limit. Initial value is
  // restored from /api/session/state on page load.
  const [canUndo, setCanUndo] = createSignal(false)
  const [undoBusy, setUndoBusy] = createSignal(false)

  // ---- edit dialog ----
  const [editOpen, setEditOpen] = createSignal(false)

  // ---- empty screen ----
  const [emptyDetail, setEmptyDetail] = createSignal('')

  const adoptGlobal = (d: {
    due_remaining?: number | null
    new_quota_left?: number | null
    new_quota_total?: number | null
    new_total?: number | null
  }) => {
    if (d.due_remaining != null) setDue(d.due_remaining)
    if (d.new_quota_left != null) setNewQuotaLeft(d.new_quota_left)
    if (d.new_quota_total != null) setNewQuotaTotal(d.new_quota_total)
    if (d.new_total != null) setNewTotal(d.new_total)
  }

  const loadBatch = (list: Card[], done: number, total: number, batchNew?: number) => {
    setCards(list)
    // Prefer the backend's authoritative batch composition (round.json
    // new_count). Fall back to counting the list only when the backend
    // didn't record it — valid for live start/more (list IS the full batch)
    // but wrong for mid-round resume (list is only what's left).
    const nNew = batchNew ?? list.filter(c => c.isNew).length
    setBatchNewTotal(nNew)
    setBatchReviewTotal(total - nNew)
    setIdx(0)
    setTotalDone(done)
    setRoundTotal(total)
    setRevealed(false)
    setPhase('review')
  }

  // ---- resync from the backend (self-heal after any state drift) ----
  const resync = async () => {
    try {
      const d = await api.sessionState()
      adoptGlobal(d)
      setCanUndo(d.can_undo)
      if (d.state === 'active') {
        loadBatch(d.cards, d.done, d.total, d.new_in_batch ?? undefined)
      } else if (d.state === 'complete') {
        setTotalDone(d.done || d.total || 0)
        if (d.new_in_batch != null) {
          setBatchNewTotal(d.new_in_batch)
          setBatchReviewTotal(d.total - d.new_in_batch)
        }
        setPhase('done')
      } else {
        setPhase('start')
      }
    } catch {
      /* keep current view; next interaction will retry */
    }
  }

  // ---- init: resume an in-progress round or show the start screen ----
  createEffect(() => { resync() })

  // ---- actions ----
  const startNewRound = async () => {
    setPhase('loading')
    setLoadText('正在同步并加载卡片…')
    try {
      const data = await api.start()
      adoptGlobal(data)
      if (!data.cards.length) {
        setEmptyDetail(
          data.due_remaining === 0
            ? '待复习的卡片都刷完了，新卡额度情况见上方状态。'
            : `待复习 ${data.due_remaining} 张，但本轮组不出卡片（新卡额度可能已用完）。`
        )
        setPhase('empty')
        return
      }
      setCanUndo(false) // a fresh round starts with no undo slot
      loadBatch(data.cards, 0, data.cards.length)
    } catch (e) {
      setLoadText('加载失败：' + (e as Error).message)
    }
  }

  const continueRound = async () => {
    setPhase('loading')
    setLoadText('正在加载更多复习卡…')
    try {
      const data = await api.more()
      adoptGlobal(data)
      if (!data.cards.length) {
        setEmptyDetail(
          data.due_remaining === 0 ? '复习池已清空，真没了 🎉' : `待复习 ${data.due_remaining} 张`
        )
        setPhase('empty')
        return
      }
      setCanUndo(false) // a fresh batch starts with no undo slot
      loadBatch(data.cards, 0, data.cards.length)
    } catch (e) {
      setLoadText('加载失败：' + (e as Error).message)
    }
  }

  const answer = async (ease: number) => {
    const card = cards()[idx()]
    if (!card || answering()) return
    setAnswering(true)
    try {
      const data = await api.answer(card.cardId, ease)
      if (!data.answered) {
        // backend rejected it (card no longer in this round — e.g. answered
        // on another device). Resync to the server's view instead of
        // advancing on stale local state.
        await resync()
        return
      }

      // Remove the answered card — the local array mirrors the backend's
      // pending list 1:1, so undo positions (backend 'index') line up.
      setCards(prev => prev.filter((_, i) => i !== idx()))
      setTotalDone(totalDone() + 1)
      // idx now naturally points at the next card (or past the end)

      // backend now holds an undo slot for this answer — no time limit
      setCanUndo(true)

      const roundDone = data.round != null && data.round.state === 'complete'
      if (!roundDone && idx() < cards().length) {
        setRevealed(false)
      } else {
        if (data.round) adoptGlobal(data.round)
        setPhase('done')
      }
    } catch (e) {
      alert('评分失败：' + (e as Error).message)
    } finally {
      setAnswering(false)
    }
  }

  const undo = async () => {
    if (undoBusy()) return
    setUndoBusy(true)
    try {
      const d = await api.undo()
      setCards(prev => {
        const next = [...prev]
        // defensive: never insert a duplicate if the card somehow stayed
        next.splice(Math.min(d.index, next.length), 0, d.card)
        return next
      })
      setIdx(Math.min(d.index, cards().length - 1))
      setTotalDone(v => Math.max(0, v - 1))
      setRevealed(false)
      setCanUndo(false) // the single undo slot is consumed
      if (phase() === 'done') setPhase('review')
    } catch (e) {
      alert('撤销失败：' + (e as Error).message)
    } finally {
      setUndoBusy(false)
    }
  }

  const finish = async () => {
    setFinishing(true)
    setPhase('loading')
    setLoadText('正在最终同步…')
    try {
      await api.finish()
    } catch { /* sync result is not critical here */ }
    setPhase('finished')
  }

  // ---- keyboard shortcuts ----
  const handleKeyDown = (e: KeyboardEvent) => {
    if (editOpen()) return
    if (phase() !== 'review') return
    if (e.key === ' ' && !revealed()) {
      e.preventDefault()
      setRevealed(true)
    } else if (revealed() && !answering()) {
      if (e.key === '1') answer(1)
      else if (e.key === '2') answer(2)
      else if (e.key === '3') answer(3)
      else if (e.key === '4') answer(4)
    }
    if (e.key === 'z' && (e.ctrlKey || e.metaKey) && canUndo() && !answering()) {
      e.preventDefault()
      undo()
    }
  }

  const currentCard = () => cards()[idx()]

  return (
    <div class="app" onKeyDown={handleKeyDown}>
      <TopAppBar due={due()} newTotal={newTotal()} />

      <div class="content">
        <Show when={phase() === 'loading'}>
          <Loading text={loadText()} />
        </Show>

        <Show when={phase() === 'start'}>
          <StartScreen
            due={due()}
            newQuotaLeft={newQuotaLeft()}
            newQuotaTotal={newQuotaTotal()}
            newTotal={newTotal()}
            busy={false}
            onBegin={startNewRound}
          />
        </Show>

        <Show when={phase() === 'review' && currentCard()}>
          <ProgressBar
            done={totalDone()}
            total={roundTotal()}
            reviewDone={reviewDone()}
            reviewTotal={reviewTotal()}
            newDone={newDone()}
            newTotal={newInBatch()}
          />
          <Flashcard
            card={currentCard()!}
            revealed={revealed()}
            onEdit={() => setEditOpen(true)}
            onUndo={undo}
            undoEnabled={canUndo()}
            undoBusy={undoBusy()}
          />
          <ActionArea
            revealed={revealed()}
            answering={answering()}
            onReveal={() => setRevealed(true)}
            onAnswer={answer}
          />
        </Show>

        <Show when={phase() === 'done'}>
          <DoneScreen
            count={totalDone()}
            due={due()}
            newQuotaLeft={newQuotaLeft()}
            newTotal={newTotal()}
            reviewDone={reviewDone()}
            reviewTotal={reviewTotal()}
            newDone={newDone()}
            newTotalBatch={newInBatch()}
            finishing={finishing()}
            canUndo={canUndo()}
            undoBusy={undoBusy()}
            onUndo={undo}
            onContinue={continueRound}
            onFinish={finish}
          />
        </Show>

        <Show when={phase() === 'empty'}>
          <EmptyScreen detail={emptyDetail()} onRefresh={() => location.reload()} />
        </Show>

        <Show when={phase() === 'finished'}>
          <FinishedScreen count={totalDone()} />
        </Show>
      </div>

      <Show when={editOpen() && currentCard()}>
        <EditDialog
          cardId={currentCard()!.cardId}
          onClose={() => setEditOpen(false)}
          onSaved={(q, a) => {
            setCards(prev =>
              prev.map(c =>
                c.cardId === currentCard()!.cardId ? { ...c, question: q, answer: a } : c
              )
            )
            setRevealed(false)
          }}
        />
      </Show>
    </div>
  )
}
