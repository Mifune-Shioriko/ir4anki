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
import { PreviewCard } from './components/PreviewCard'
import { PreviewScreen } from './components/PreviewScreen'
import { PreviewDoneScreen } from './components/PreviewDoneScreen'

type Phase =
  | 'loading'
  | 'start'
  | 'review'
  | 'done'
  | 'empty'
  | 'finished'
  // preview mode (先看后考) — rendered only when the backend flag is on
  | 'previewStart'
  | 'preview'
  | 'previewDone'

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

  // ---- preview mode (先看后考) ----
  // Gated entirely by the backend flag (resync reads it). Preview mode off:
  // every preview signal stays null/false and the UI is the legacy one.
  const [previewMode, setPreviewMode] = createSignal(false)
  const [previewPool, setPreviewPool] = createSignal<number | null>(null)
  const [previewAvailable, setPreviewAvailable] = createSignal<number | null>(null)
  const [pvCards, setPvCards] = createSignal<Card[]>([])
  const [pvDone, setPvDone] = createSignal(0)
  const [pvTotal, setPvTotal] = createSignal(0)
  const [pvApproved, setPvApproved] = createSignal(0)
  const [pvDeferred, setPvDeferred] = createSignal(0)
  const [pvBusy, setPvBusy] = createSignal(false)
  // undo + edit inside preview rounds — same UX as the review round
  const [pvCanUndo, setPvCanUndo] = createSignal(false)
  const [pvUndoBusy, setPvUndoBusy] = createSignal(false)
  const [pvEditOpen, setPvEditOpen] = createSignal(false)

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
      // preview-mode signals (absent when the backend flag is off)
      setPreviewMode(!!d.preview_mode)
      if (d.preview_mode) {
        setPreviewPool(d.preview_pool ?? null)
        setPreviewAvailable(d.preview_available ?? null)
      }
      if (d.state === 'active') {
        // a normal review round in progress always wins — finish it first
        loadBatch(d.cards, d.done, d.total, d.new_in_batch ?? undefined)
      } else if (d.preview_mode && d.preview_round) {
        // resume an unfinished preview round
        setPvCards(d.preview_round.cards)
        setPvDone(d.preview_round.done)
        setPvTotal(d.preview_round.total)
        setPvApproved(0)
        setPvDeferred(0)
        setPvCanUndo(!!d.preview_round.can_undo)
        setPhase('preview')
      } else if (d.preview_mode && (d.preview_available ?? 0) > 0) {
        // top of the funnel: read new cards before they enter testing
        setPhase('previewStart')
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

  // ---- preview actions (先看后考) ----
  const previewCard = () => pvCards()[0]

  const pvStart = async () => {
    setPhase('loading')
    setLoadText('正在加载预览卡…')
    setPvBusy(true)
    try {
      const d = await api.previewStart()
      if (!d.cards.length) {
        // pool drained (deferred today) — back to whatever resync decides
        await resync()
        return
      }
      setPvCards(d.cards)
      setPvDone(0)
      setPvTotal(d.cards.length)
      setPvApproved(0)
      setPvDeferred(0)
      setPvCanUndo(false) // a fresh preview round has no undo slot
      setPreviewPool(d.pool)
      setPreviewAvailable(d.available)
      setPhase('preview')
    } catch (e) {
      setLoadText('加载失败：' + (e as Error).message)
    } finally {
      setPvBusy(false)
    }
  }

  const pvAct = async (action: 'approve' | 'defer') => {
    const card = previewCard()
    if (!card || pvBusy()) return
    setPvBusy(true)
    try {
      const d = await api.previewAct(card.cardId, action)
      if (!d.ok) {
        // stale (card left the pool elsewhere) — resync to the server's view
        await resync()
        return
      }
      setPvCards(prev => prev.slice(1))
      setPvDone(v => v + 1)
      if (action === 'approve') setPvApproved(v => v + 1)
      else setPvDeferred(v => v + 1)
      // the backend recorded a single-level undo slot for this act
      setPvCanUndo(true)
      if (d.round_complete) {
        setPreviewPool(d.pool ?? null)
        setPreviewAvailable(d.available ?? null)
        setPhase('previewDone')
      }
    } catch (e) {
      alert('操作失败：' + (e as Error).message)
    } finally {
      setPvBusy(false)
    }
  }

  // Undo the last preview act (approve→card back in the pool suspended,
  // defer→today's deferred tag removed). Works in-round AND from the
  // previewDone screen (the backend keeps the slot in the tombstone).
  const pvUndo = async () => {
    if (pvUndoBusy()) return
    setPvUndoBusy(true)
    try {
      const d = await api.previewUndo()
      if (phase() === 'preview') {
        // re-insert the restored card at its original position
        setPvCards(prev => {
          const next = [...prev]
          next.splice(Math.min(d.index, next.length), 0, d.card)
          return next
        })
        setPvDone(v => Math.max(0, v - 1))
        if (d.action === 'approve') setPvApproved(v => Math.max(0, v - 1))
        else setPvDeferred(v => Math.max(0, v - 1))
      } else {
        // undo from previewDone reactivates the round — resync to rebuild
        await resync()
      }
      setPvCanUndo(false) // single-level slot consumed
    } catch {
      // nothing to undo / card moved on — resync to the server's view
      await resync()
    } finally {
      setPvUndoBusy(false)
    }
  }

  // "跳过，直接复习" / "开始复习" — one tap straight into a review round.
  // The backend start consumes the preview-approved tombstone, so cards
  // just previewed+released are dealt as this round's new material.
  const pvToReview = () => startNewRound()

  // re-enter the preview funnel from the regular start/done screens.
  // Pool/available signals come from the last resync; pvStart re-fetches
  // fresh pool numbers from the backend when the round actually begins.
  const pvToPreview = () => setPhase('previewStart')

  const pvFinish = async () => {
    setPhase('loading')
    setLoadText('正在同步…')
    try {
      await api.previewFinish()
    } catch { /* not critical */ }
    setPhase('finished')
  }

  // ---- keyboard shortcuts ----
  const handleKeyDown = (e: KeyboardEvent) => {
    if (editOpen() || pvEditOpen()) return
    if (phase() === 'preview') {
      if (pvBusy()) return
      if (e.key === 'z' && (e.ctrlKey || e.metaKey) && pvCanUndo() && !pvUndoBusy()) {
        e.preventDefault()
        pvUndo()
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        pvAct('approve')
      } else if (e.key === 'd' || e.key === 'D') {
        e.preventDefault()
        pvAct('defer')
      }
      return
    }
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
      <TopAppBar
        due={due()}
        newTotal={newTotal()}
        previewPool={previewMode() ? previewPool() : null}
      />

      <div class="content">
        <Show when={phase() === 'loading'}>
          <Loading text={loadText()} />
        </Show>

        <Show when={phase() === 'previewStart'}>
          <PreviewScreen
            pool={previewPool()}
            available={previewAvailable()}
            due={due()}
            busy={pvBusy()}
            onStart={pvStart}
            onSkipToReview={pvToReview}
          />
        </Show>

        <Show when={phase() === 'preview' && previewCard()}>
          <div class="progress-area">
            <div class="progress-row">
              <md-linear-progress
                class="progress-bar"
                value={pvTotal() > 0 ? Math.min(pvDone() / pvTotal(), 1) : 0}
              />
              <span class="progress-text md-typescale-label-large">
                {pvDone()}/{pvTotal()}
              </span>
            </div>
            <div class="progress-split md-typescale-label-medium">
              <span>已放行 {pvApproved()}</span>
              <span>明天再看 {pvDeferred()}</span>
            </div>
          </div>
          <PreviewCard
            card={previewCard()!}
            busy={pvBusy()}
            onApprove={() => pvAct('approve')}
            onDefer={() => pvAct('defer')}
            onUndo={pvUndo}
            onEdit={() => setPvEditOpen(true)}
            undoEnabled={pvCanUndo()}
            undoBusy={pvUndoBusy()}
          />
        </Show>

        <Show when={phase() === 'previewDone'}>
          <PreviewDoneScreen
            approved={pvApproved()}
            deferred={pvDeferred()}
            pool={previewPool()}
            available={previewAvailable()}
            due={due()}
            busy={pvBusy()}
            onMore={pvStart}
            onToReview={pvToReview}
            onFinish={pvFinish}
            onUndo={pvUndo}
            canUndo={pvCanUndo()}
            undoBusy={pvUndoBusy()}
          />
        </Show>

        <Show when={phase() === 'start'}>
          <StartScreen
            due={due()}
            newQuotaLeft={newQuotaLeft()}
            newQuotaTotal={newQuotaTotal()}
            newTotal={newTotal()}
            busy={false}
            onBegin={startNewRound}
            previewPool={previewMode() ? previewPool() : null}
            onToPreview={pvToPreview}
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
            previewPool={previewMode() ? previewPool() : null}
            onToPreview={pvToPreview}
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

      <Show when={pvEditOpen() && previewCard()}>
        <EditDialog
          cardId={previewCard()!.cardId}
          onClose={() => setPvEditOpen(false)}
          onSaved={(q, a) => {
            const cid = previewCard()!.cardId
            setPvCards(prev =>
              prev.map(c => (c.cardId === cid ? { ...c, question: q, answer: a } : c)),
            )
          }}
        />
      </Show>
    </div>
  )
}
