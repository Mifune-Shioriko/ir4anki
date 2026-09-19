"""Anki review web app — backend (v2).

Session model:
  - TWO PACING MODES (user spec 2026-09-14, sizes retuned 2026-09-16):
    "quick" = 5 preview + 5 new + 20 review (碎片时间, a few minutes — the
    短视频式 rhythm of 2026-09-06), "focus" = 10 preview + 10 new + 30
    review (整块时间, roughly 2× quick).
    The mode is chosen per round at start and travels with round.json /
    preview.json so a page refresh resumes the SAME mode. 'more' inherits
    the mode of the round that just completed. Numbers are overridable via
    env (ANKI_QUICK_REVIEW / ANKI_FOCUS_NEW / …) without code changes.
  - one round = a PREVIEW lead-in (preview mode; since 2026-09-07
    the preview hides the answer until revealed — a low-stakes retrieval
    attempt), then up to {mode.review} review cards + up to {mode.new}
    new cards
  - PREVIEW APPROVE = NEXT-DAY RELEASE (user spec 2026-09-07): approved
    cards stay suspended with a released-YYYYMMDD stamp and are unsuspended
    by release_yesterday_approved() on the next calendar day, so the first
    grading happens ≥1 night after preview. Same-day grading after reading
    is recognition, not recall (measured: first-review pass 61% vs 88%
    pre-preview cohort; first-Good→next-Again 33% vs 6%).
  - new cards: NO hourly quota — every round (and 'continue') draws up to
    the active mode's `new` count UNIFORMLY AT RANDOM from the new-card pool
    (user
    spec 2026-09-04; replaces the hourly quota + preview-priority funnel so
    older released cards can't be starved by fresher ones)
  - DOUBLE-AGAIN AUTO-RETURN (user spec 2026-09-16): a NEW card whose first
    two consecutive gradings are both Again is automatically sent back to
    the preview pool (same reset as the manual 移回预览池). Only the first
    learning cycle counts; streak state persists in state/new_again.json.
  - every answer triggers a fire-and-forget sync to the self-hosted server
  - round state persists in state/round.json: refreshing the page resumes
    the in-progress round instead of dealing a fresh one (GET /api/session/state)
  - READING MODE (渐进制卡, user spec 2026-09-19, gated ANKI_READING_MODE):
    a reading segment of {mode.read} note chunks (quick=2, focus=5,
    ANKI_QUICK_READ / ANKI_FOCUS_READ) runs BEFORE preview+review. The user
    reads their own markdown notes (~/anki-notes, chunked by notes-rag's
    chunker.py) and writes cards by hand. Per chunk: todo → active(正在制卡)
    → done(制卡完成), or skipped(无需制卡). Within a file only the frontier
    chunk is dealt, so later chunks stay locked until the frontier is
    finished; an `active` chunk resurfaces first every round. Files are
    opt-in via the 阅读清单 (manual priority). State: state/reading.json.
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import sys
import uuid
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# All external endpoints and filesystem paths are overridable via env vars
# so the same code runs in any deployment layout. Defaults match the repo
# layout: backend/ and frontend/ side by side under the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent

ANKICONNECT = os.getenv("ANKICONNECT_URL", "http://127.0.0.1:8765")
ANKI_RAG = os.getenv("ANKI_RAG_URL", "http://127.0.0.1:8789")
ANKI_EXPLAIN = os.getenv("ANKI_EXPLAIN_URL", "http://127.0.0.1:8788")
NOTES_RAG = os.getenv("NOTES_RAG_URL", "http://127.0.0.1:8791")

# Anki collection.media directory (served back at /media/<name>)
MEDIA_DIR = Path(os.getenv("ANKI_MEDIA_DIR", str(_REPO_ROOT / "collection.media")))
# built SolidJS frontend (served as the SPA; skipped if absent)
REVIEW_WEB_V2_DIST = Path(
    os.getenv("REVIEW_DIST_DIR", str(_REPO_ROOT / "frontend" / "dist"))
)
STATE_DIR = Path(os.getenv("ANKI_STATE_DIR", str(Path(__file__).parent / "state")))
ROUND_FILE = STATE_DIR / "round.json"

ROUND_EXPIRE_HOURS = 24  # a half-finished round older than this is discarded

# ---- two pacing modes (user spec 2026-09-14, sizes retuned 2026-09-16) ----
# quick = 碎片时间 (queue at the canteen): the 5+5+20 rhythm, meant to
#         be opened many times a day.
# focus = 整块时间 (a free afternoon block): 10+10+30, roughly 2× quick.
# The mode is picked per round on the start screen and PERSISTS in
# round.json / preview.json (page refresh resumes the same mode); 'more'
# inherits the completed round's mode. All numbers env-overridable.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

STUDY_MODES: dict[str, dict[str, int]] = {
    "quick": {
        "read": _env_int("ANKI_QUICK_READ", 2),
        "preview": _env_int("ANKI_QUICK_PREVIEW", 5),
        "new": _env_int("ANKI_QUICK_NEW", 5),
        "review": _env_int("ANKI_QUICK_REVIEW", 20),
    },
    "focus": {
        "read": _env_int("ANKI_FOCUS_READ", 5),
        "preview": _env_int("ANKI_FOCUS_PREVIEW", 10),
        "new": _env_int("ANKI_FOCUS_NEW", 10),
        "review": _env_int("ANKI_FOCUS_REVIEW", 30),
    },
}
DEFAULT_MODE = "quick"

# Daily goal for new-card releases (放行) — progress bar on the preview
# start screen (user spec 2026-09-16) AND a hard cap on preview dealing
# (user spec 2026-09-16, second iteration): round sizes of 5/10 would
# otherwise overshoot the goal (43, 48…), so once the remaining budget
# drops below the biggest preview size, EVERY mode deals exactly what's
# left, and at 0 the day's previews are done. Tracks the same number as
# the wire field `pending_release` (cards approved TODAY, still suspended,
# released tomorrow) — cap and bar can never disagree. Deferred cards
# (明天再看) do NOT consume the budget; undoing an approval gives it back.
# <=0 disables both the bar and the cap. Env-overridable.
RELEASE_DAILY_GOAL = _env_int("ANKI_RELEASE_DAILY_GOAL", 40)

def study_mode(name: str | None) -> str:
    """Validate/normalize a mode name; falls back to the default."""
    return name if name in STUDY_MODES else DEFAULT_MODE

# ---- preview mode (先看后考: read new cards before they enter testing) ----
# Newly injected cards land in PREVIEW_DECK SUSPENDED, so they are invisible
# to the scheduler and to normal rounds. A preview round shows the question
# first (answer hidden until the user reveals it — a low-stakes retrieval
# attempt, NO grading); per card the user either approves (放行) or defers
# (tagged deferred-YYYYMMDD, hidden from today's preview rounds, resurfaces
# tomorrow).
#
# Approve = NEXT-DAY release (2026-09-07, user spec): the card moves to
# RELEASE_DECK but STAYS SUSPENDED and gets tag released-YYYYMMDD. On the
# next calendar day release_yesterday_approved() unsuspends it, so the FIRST
# grading happens ≥1 night after preview. Rationale (measured 2026-09-07):
# same-day grading right after reading is recognition, not recall — first-
# review pass rate for same-day-approved cards was 61% vs 88% for the pre-
# preview cohort, and 33% of first-Good cards failed their very next answer
# (vs 6% before). The overnight gap makes the first grade honest, so FSRS
# seeds stability from recall instead of a fluency illusion.
#
# The whole feature is gated on ANKI_PREVIEW_MODE. With it unset, preview
# endpoints return 404 and status reports preview_mode=false — the frontend
# then renders the exact legacy UI (system-level rollback).
# Data-level rollback: preview cards are untouched NEW cards; unsuspend them
# (and drop released-*/deferred-* tags) and the collection is exactly as
# before — same-day release can be restored by unsuspensing right away in
# the approve branch.
PREVIEW_MODE = os.getenv("ANKI_PREVIEW_MODE", "").lower() in ("1", "true", "yes", "on")
PREVIEW_DECK = os.getenv("ANKI_PREVIEW_DECK", "预览池")
RELEASE_DECK = os.getenv("ANKI_PREVIEW_RELEASE_DECK", "2026")
PREVIEW_FILE = STATE_DIR / "preview.json"

# ---- double-Again auto-return (user spec 2026-09-16) ----
# A NEW card whose first two consecutive gradings are both Again goes back
# to the preview pool (先看后考 again): two honest recall failures right
# after release mean the card was approved too early — re-learning it beats
# grinding relearning steps. Only the FIRST learning cycle counts (初学):
# the streak opens only while the card is still type==0 (new) and is cleared
# by any other grade, by undo, by a manual 移回预览池, and by delete. Streak
# state persists in state/new_again.json so it survives rounds, refreshes
# and restarts (the two Agains usually land in DIFFERENT rounds — Anki's
# relearning step re-deals the card minutes later).
# Kill-switch: ANKI_NEW_AGAIN_RETURN=0; threshold: ANKI_NEW_AGAIN_LIMIT.
NEW_AGAIN_RETURN = os.getenv("ANKI_NEW_AGAIN_RETURN", "1").lower() in ("1", "true", "yes", "on")
NEW_AGAIN_LIMIT = max(1, _env_int("ANKI_NEW_AGAIN_LIMIT", 2))
NEW_AGAIN_FILE = STATE_DIR / "new_again.json"

# ---- reading mode (渐进制卡, user spec 2026-09-19) ----
# Progressive card-making: the user reads their own markdown notes chunk by
# chunk and writes cards by hand (AI batch card-making proved too unreliable
# — fixing bad cards costs more than writing them). Memory scheduling stays
# with Anki/FSRS; the reading side is a plain priority queue + a 4-state
# machine (todo → active → done, plus skipped), NO SuperMemo-style fragment
# rescheduling in v1.
#
# Ordering rule (user spec, updated 2026-09-19 round 2): a round deals up
# to STUDY_MODES[mode]["read"] CHUNKS — active chunks first (resurface until
# done/skipped), then one frontier chunk per file (breadth, 阅读清单
# priority), then — while the budget isn't filled — the same files' NEXT
# dealable chunks in file order (depth). A single-file list therefore still
# gets a full round instead of exactly one chunk. Within a file, chunks
# before the frontier are done/skipped by definition; done/skipped chunks
# are never dealt. An `active` chunk IS dealable, so it automatically
# resurfaces every round until the user marks it done/skipped.
#
# 开始制卡 is GONE from the UI (2026-09-19 round 2): chunks auto-mark
# `active` when a card or cloze is created from them (_reading_record_card).
# The mark_active API action remains (harmless, used by tests/scripts).
#
# Files are opt-in (阅读清单): the corpus mixes study notes with misc logs,
# so nothing is auto-queued; the user adds files and orders them by hand
# (decision #2). Round size rides the pacing modes: STUDY_MODES[*]["read"]
# (decision #4). Chunks come from notes-rag's chunker.py (single source of
# truth — imported, not copied) over ~/anki-notes.
#
# Gated on ANKI_READING_MODE like preview: off ⇒ endpoints 404, the wire
# reports reading_mode=false and the frontend renders the legacy UI.
READING_MODE = os.getenv("ANKI_READING_MODE", "").lower() in ("1", "true", "yes", "on")
NOTES_DIR = Path(os.getenv("ANKI_NOTES_DIR", str(Path.home() / "anki-notes")))
NOTES_RAG_DIR = os.getenv("NOTES_RAG_DIR", str(Path.home() / "notes-rag"))
READING_FILE = STATE_DIR / "reading.json"
# same exclusion set notes-rag/sync.py uses
NOTES_SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules"}
READING_ACTIONS = ("mark_active", "complete", "skip", "next")


def _active_preview_per_round() -> int | None:
    """Batch size of the ACTIVE preview round.

    The round's own `total` is authoritative: it already carries whatever
    cap applied when it was dealt (release budget, small pool). Falls back
    to the stored mode's size for legacy rounds without a total.
    None when there is no active preview round — the frontend then renders
    sizes from study_modes[selected] on the start screens instead.
    """
    prd = _preview_read()
    if prd is not None and prd.get("status") == "active" and prd.get("pending"):
        total = prd.get("total")
        if isinstance(total, int) and total > 0:
            return total
        return STUDY_MODES[study_mode(prd.get("mode"))]["preview"]
    return None


def _capped_study_modes(budget: int | None) -> dict[str, dict[str, int]]:
    """STUDY_MODES with each tier's `preview` size adapted to the remaining
    daily release budget (user spec 2026-09-16).

    budget=None (goal disabled) → the raw table unchanged. While the budget
    is at least the biggest preview size every tier deals its normal size;
    once it drops below (e.g. 34 released of 40 → 6 left), EVERY tier deals
    exactly what's left — one round lands the goal instead of dragging it
    across several quick rounds. The frontend renders sizes from this table
    only, so the rule lives in exactly one place.
    """
    if budget is None:
        return STUDY_MODES
    max_preview = max(m["preview"] for m in STUDY_MODES.values())
    return {
        name: {
            **sizes,
            "preview": budget if budget < max_preview else min(sizes["preview"], budget),
        }
        for name, sizes in STUDY_MODES.items()
    }


def _capped_preview_size(mode: str, budget: int | None) -> int:
    """Preview batch size for one round: the mode's size, adapted to the
    remaining daily release budget (see _capped_study_modes)."""
    return _capped_study_modes(budget)[mode]["preview"]

# stamp tag added on approve; the card is released (unsuspended) when this
# date is strictly BEFORE today — see release_yesterday_approved()
RELEASED_TAG_PREFIX = "released-"
# Anki's day rollover is 4 AM (collection default, container TZ fixed to
# Asia/Shanghai). Stamp/release comparisons use the ANKI day, not the
# calendar day, so a card approved at 23:50 is not "released" at 00:05 —
# it waits until the scheduler's own next day (04:00), guaranteeing a real
# overnight gap before the first grading.
ANKI_ROLLOVER_HOUR = int(os.getenv("ANKI_ROLLOVER_HOUR", "4"))


def _anki_day() -> str:
    """Current Anki day (YYYYMMDD): calendar day, rolled over at 4 AM."""
    return (datetime.now() - timedelta(hours=ANKI_ROLLOVER_HOUR)).strftime("%Y%m%d")

# card fields snapshotted before answering (for /api/undo) and the Anki
# attribute names used to restore them via setSpecificValueOfCard
SNAPSHOT_FIELDS = ("interval", "factor", "due", "reps", "lapses", "left", "type", "queue")
RESTORE_KEYS = ["ivl", "factor", "due", "reps", "lapses", "left", "type", "queue"]

app = FastAPI(title="anki-review")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sync_lock = asyncio.Lock()
_sync_pending = False  # coalesce fire-and-forget syncs

# Serializes the read-modify-write cycles on round.json / preview.json in
# the per-card mutation endpoints (answer, undo, preview act/undo, delete,
# to-preview). The user reviews from MULTIPLE devices (phone + desktop over
# tailscale); without this lock two concurrent mutations both read the old
# pending list and the later write silently drops the earlier one — an
# answered card comes back, undo slots point at stale indices. Single
# uvicorn process, so an asyncio.Lock is sufficient. Lock order is always
# _state_lock -> _sync_lock (do_sync); never the reverse, so no deadlock.
_state_lock = asyncio.Lock()


async def anki(action: str, params: dict | None = None, timeout: float = 30):
    payload = {"action": action, "version": 6, "params": params or {}}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(ANKICONNECT, json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("error"):
        raise HTTPException(status_code=502, detail=f"AnkiConnect: {data['error']}")
    return data.get("result")


async def do_sync() -> bool:
    global _sync_pending
    try:
        async with _sync_lock:
            await anki("sync", timeout=300)
        return True
    except Exception:
        return False
    finally:
        _sync_pending = False


def fire_and_forget_sync():
    """Coalesced background sync: at most one pending at a time."""
    global _sync_pending
    if _sync_pending:
        return
    _sync_pending = True
    asyncio.get_running_loop().create_task(do_sync())


# ---- new-card drawing ------------------------------------------------------
# No hourly quota since 2026-09-04 (user spec): every round independently
# draws up to the active mode's `new` count UNIFORMLY AT RANDOM from the
# dealable new pool. The old hour-keyed quota.json is no longer read or
# written.


def new_card_query() -> str:
    """Search for dealable new cards.

    In preview mode, new cards in the preview pool sit SUSPENDED and must
    not leak into the regular new-card queue, so exclude suspended cards.
    Preview mode off: the historical query, unchanged.
    """
    return "is:new -is:suspended" if PREVIEW_MODE else "is:new"


# ---- round persistence (survives page refresh) ---------------------------

def _round_read() -> dict | None:
    STATE_DIR.mkdir(exist_ok=True)
    if not ROUND_FILE.exists():
        return None
    try:
        return json.loads(ROUND_FILE.read_text())
    except Exception:
        return None


def _round_write(rd: dict | None):
    STATE_DIR.mkdir(exist_ok=True)
    if rd is None:
        ROUND_FILE.unlink(missing_ok=True)
    else:
        tmp = ROUND_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rd))
        tmp.replace(ROUND_FILE)  # atomic


def _round_expired(rd: dict) -> bool:
    try:
        created = datetime.fromisoformat(rd["created"])
        return (datetime.now() - created).total_seconds() > ROUND_EXPIRE_HOURS * 3600
    except Exception:
        return True


def current_round() -> dict | None:
    """Current round state; expired rounds are discarded silently."""
    rd = _round_read()
    if rd is None:
        return None
    if _round_expired(rd):
        _round_write(None)
        return None
    return rd


async def restore_round_cards(rd: dict) -> list[dict]:
    """Fetch the round's still-pending cards.

    Cards answered on another device since the round started are dropped
    (counted as done) so they don't resurface. Review cards are checked
    against Anki's own due logic (prop:due<=0, since their `due` values
    are collection-day numbers); new cards are kept while still unlearned
    (prop:due does not match the new queue).
    """
    ids = rd.get("pending", [])
    if not ids:
        return []
    due_ids = set(await anki("findCards", {"query": "prop:due<=0"}) or [])
    infos = await anki("cardsInfo", {"cards": ids})
    # filter [{}] empty rows (vanished cards) before indexing
    info_by_id = {i["cardId"]: i for i in infos or [] if i.get("cardId")}
    keep = [
        cid
        for cid in ids
        if cid in info_by_id
        and (info_by_id[cid]["type"] == 0 or cid in due_ids)
    ]
    dropped = len(ids) - len(keep)
    if dropped:
        rd["done_count"] = rd.get("done_count", 0) + dropped
    rd["pending"] = keep
    if not keep:
        rd["status"] = "complete"
    _round_write(rd)
    return await fetch_cards(keep) if keep else []


# ---- card helpers --------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strip HTML to plain text (anki-rag lookup keys are plain 正面 text).

    Skips <style>/<script> content — rendered Anki questions embed the
    card CSS in a <style> block, which must not pollute the lookup key.
    """

    _SKIP = {"style", "script"}

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._parts).split())


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html or "")
    except Exception:
        return ""
    return p.text()


async def fetch_similar(question_html: str, top_k: int = 3) -> list[dict]:
    """Ask anki-rag for similar cards (server-side; template JS doesn't run here).

    Fail-soft: any error or timeout returns [] so review never breaks.
    """
    q = html_to_text(question_html)
    if not q:
        return []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"{ANKI_RAG}/search", params={"query": q, "top_k": top_k}
            )
            r.raise_for_status()
            return r.json().get("results", [])
    except Exception:
        return []


async def fetch_explanation(note_id: int | None) -> str:
    """Ask anki-explain (:8788) for the AI explanation of this note.

    Exact lookup by note id (AnkiConnect cardsInfo returns it as "note").
    Fail-soft like fetch_similar: any error returns "".
    """
    if not note_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{ANKI_EXPLAIN}/note/{note_id}")
            r.raise_for_status()
            d = r.json()
            return d.get("explanation", "") if d.get("found") else ""
    except Exception:
        return ""


async def fetch_note_sections(note_id: int | None, top_k: int = 3) -> list[dict]:
    """Ask notes-rag (:8791) for the note sections this card came from.

    The right-hand note panel (Phase 1 of the 知识成体系 project) renders
    the top-1 as a "page" with the rest switchable as tabs. Fail-soft like
    the other enrichment calls: notes-rag down / card not embedded / note
    gone → [] and the panel simply doesn't render. Timeout is generous
    (6s) because notes-rag embeds on cache miss (one DashScope call) for
    cards added after the last sync — a normal review must never wait on
    that, so the frontend must treat this as async enrichment.
    """
    if not note_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                f"{NOTES_RAG}/search", params={"note_id": note_id, "top_k": top_k}
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            return results if isinstance(results, list) else []
    except Exception:
        return []


async def fetch_cards(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    infos = await anki("cardsInfo", {"cards": ids})
    # cardsInfo returns cards sorted by id — re-order to match the REQUEST
    # order (matters for preview-priority new cards and due-order batches).
    # Defensive: AnkiConnect returns [{}] (one EMPTY row) for vanished ids —
    # never index those rows directly (KeyError → 500).
    by_id = {i["cardId"]: i for i in infos or [] if i.get("cardId")}
    ordered = [by_id[cid] for cid in ids if cid in by_id]
    out = []
    for info in ordered:
        out.append(
            {
                "cardId": info["cardId"],
                "noteId": info.get("note"),
                "question": info["question"],
                "answer": info["answer"],
                "deckName": info["deckName"],
                "modelName": info["modelName"],
                "css": info.get("css", ""),
                "isNew": info["type"] == 0,
                "due": info["due"],
            }
        )
    # enrich with similar cards + AI explanations in parallel (local
    # lookups, fast). NOTE: priorKnowledge enrichment removed 2026-09-17
    # (anki-prior-knowledge project retired at user request — block judged
    # not useful during actual reviews).
    similars = await asyncio.gather(*(fetch_similar(c["question"]) for c in out))
    explanations = await asyncio.gather(*(fetch_explanation(c["noteId"]) for c in out))
    for card, sim, expl in zip(out, similars, explanations):
        card["similar"] = sim
        card["explanation"] = expl
    return out


async def build_batch(review_count: int, allow_new: bool, new_count: int = 0):
    """Assemble one study batch.

    New cards are INTERLEAVED evenly among the review cards (like Anki's
    mixed new/review ordering). They used to be appended at the very end,
    so a user who stopped after the 10 reviews never reached them — that
    was the "never pushes new cards" bug (2026-08-26).

    New-card draw (2026-09-04 spec): up to `new_count` cards sampled
    UNIFORMLY AT RANDOM from the whole dealable pool — no hourly quota, no
    priority for just-approved preview cards, so nothing starves.
    """
    cards: list[dict] = []

    # review cards: due but not new, most overdue first
    due_ids = await anki("findCards", {"query": "is:due -is:new"}) or []
    if due_ids:
        infos = await anki("cardsInfo", {"cards": due_ids})
        # filter [{}] empty rows before indexing "due"
        infos = [i for i in infos or [] if i.get("cardId")]
        infos.sort(key=lambda i: i["due"])
        cards += await fetch_cards([i["cardId"] for i in infos[:review_count]])

    # new cards: random sample within the per-round cap, spread evenly
    if allow_new:
        new_ids = await anki("findCards", {"query": new_card_query()}) or []
        take = min(new_count, len(new_ids))
        if take > 0:
            new_cards = await fetch_cards(random.sample(new_ids, take))
            n_reviews = len(cards)
            if n_reviews:
                for i, c in enumerate(new_cards):
                    # evenly spaced slots; +i accounts for earlier inserts
                    pos = round((i + 1) * n_reviews / (take + 1)) + i
                    cards.insert(min(len(cards), pos), c)
            else:
                cards += new_cards

    due_remaining = len(await anki("findCards", {"query": "is:due -is:new"}) or [])
    new_total = len(await anki("findCards", {"query": new_card_query()}) or [])
    return cards, due_remaining, new_total


# ---- API ------------------------------------------------------------------

@app.get("/api/status")
async def status():
    try:
        due = await anki("findCards", {"query": "is:due -is:new"})
        new = await anki("findCards", {"query": new_card_query()})
        # capped mode table (2026-09-16): preview sizes track the daily
        # release budget, so every wire copy of study_modes agrees
        try:
            budget = await _release_budget_left()
        except Exception:
            budget = None
        out = {
            "anki": "ok",
            "due_review": len(due or []),
            "new_total": len(new or []),
            "new_per_round": STUDY_MODES[DEFAULT_MODE]["new"],
            "preview_mode": PREVIEW_MODE,
            "reading_mode": READING_MODE,
            # two pacing modes (user spec 2026-09-14) — the frontend renders
            # the start-screen choice from this table, never hardcoded
            "study_modes": _capped_study_modes(budget),
            "default_mode": DEFAULT_MODE,
            # daily 放行 goal for the preview-start progress bar (2026-09-16)
            "release_daily_goal": RELEASE_DAILY_GOAL,
        }
        if PREVIEW_MODE:
            out["preview_pool"] = len(await preview_pool_ids() or [])
        if READING_MODE:
            try:
                out.update(await _reading_extras())
            except Exception:
                pass  # fail-soft: status must never break on reading state
        return out
    except Exception as e:
        return {"anki": "error", "detail": str(e)}


@app.post("/api/session/start")
async def start_session(mode: str | None = None):
    """Deal under _state_lock; the (potentially slow) sync runs outside it
    so other devices' state polls are never blocked for minutes.

    `mode` picks the pacing (quick = 碎片时间 5+5+20, focus = 整块时间
    10+10+30); unknown/absent falls back to DEFAULT_MODE."""
    synced = await do_sync()  # do_sync acquires _sync_lock itself
    async with _state_lock:
        return await _start_session_impl(synced, study_mode(mode))


async def _start_session_impl(synced: bool, mode: str = DEFAULT_MODE):
    if PREVIEW_MODE:
        await release_yesterday_approved()
    m = STUDY_MODES[mode]
    cards, due_remaining, new_total = await build_batch(
        m["review"], allow_new=True, new_count=m["new"]
    )
    if cards:
        _round_write(
            {
                "status": "active",
                "created": datetime.now().isoformat(timespec="seconds"),
                "total": len(cards),
                "done_count": 0,
                "pending": [c["cardId"] for c in cards],
                # batch composition (review/new split) — the frontend needs
                # it for the done-screen stats even after a page reload
                "new_count": sum(1 for c in cards if c["isNew"]),
                # pacing mode — travels with the round so refresh/more keep it
                "mode": mode,
            }
        )
    # capped table (2026-09-16): the frontend stores whatever study_modes
    # rides along, so this must match session/state's view of the budget
    budget = None
    if PREVIEW_MODE:
        try:
            budget = await _release_budget_left()
        except Exception:
            budget = None
    return {
        "synced": synced,
        "cards": cards,
        "due_remaining": due_remaining,
        "new_per_round": m["new"],
        "new_total": new_total,
        "mode": mode,
        "study_modes": _capped_study_modes(budget),
    }


@app.get("/api/session/state")
async def session_state():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _session_state_impl()


async def _session_state_impl():
    """Page-load entry point: resume an unfinished round instead of auto-starting."""
    if PREVIEW_MODE:
        # next-day release: cards approved on a PREVIOUS day become gradeable
        # now, so every count below reflects today's true new-card pool
        await release_yesterday_approved()
    try:
        due = await anki("findCards", {"query": "is:due -is:new"})
        due_left = len(due or [])
    except Exception:
        due_left = None
    try:
        new_ids = await anki("findCards", {"query": new_card_query()})
        new_total = len(new_ids or [])
    except Exception:
        new_total = None

    # preview-mode extras (the frontend decides from preview_mode whether to
    # render the preview UI at all — feature flag off ⇒ exact legacy shape)
    preview: dict = {
        "preview_mode": PREVIEW_MODE,
        # pacing-mode table for the start screens (2026-09-14) — the UI
        # renders the quick/focus choice from this, never hardcoded numbers
        "study_modes": STUDY_MODES,
        "default_mode": DEFAULT_MODE,
        # daily 放行 goal for the preview-start progress bar (2026-09-16);
        # the bar tracks pending_release below against this goal
        "release_daily_goal": RELEASE_DAILY_GOAL,
    }
    if PREVIEW_MODE:
        # batch size of the ACTIVE preview round (mode-dependent since
        # 2026-09-14) — None when no preview round: the start screens then
        # use study_modes[selected] to preview the sizes
        preview["preview_per_round"] = _active_preview_per_round()
        try:
            preview["pending_release"] = await _pending_release_today()
        except Exception:
            preview["pending_release"] = None
        # daily release budget (2026-09-16): remaining 放行 slots. The
        # study_modes table below is capped by it, so the start screens
        # show the ACTUAL deal size for each tier
        try:
            budget = await _release_budget_left()
        except Exception:
            budget = None
        preview["release_budget_left"] = budget
        preview["study_modes"] = _capped_study_modes(budget)
        try:
            pool = await preview_pool_ids()
            deferred = await _deferred_today_cards(pool)
            preview["preview_pool"] = len(pool)
            preview["preview_available"] = len(pool) - len(deferred)
            prd = _preview_read()
            if (
                prd is not None
                and prd.get("status") == "active"
                and prd.get("pending")
            ):
                infos = await anki("cardsInfo", {"cards": prd["pending"]})
                still = [
                    i["cardId"]
                    for i in infos or []
                    if i.get("cardId")
                    and PREVIEW_DECK in i.get("deckName", "")
                    and i.get("type") == 0
                ]
                pending = [c for c in prd["pending"] if c in still]
                if pending:
                    prd["pending"] = pending
                    _preview_write(prd)
                    preview["preview_round"] = {
                        "cards": await fetch_cards(pending),
                        "done": prd.get("done", 0),
                        "total": prd.get("total", len(pending)),
                        "can_undo": bool(prd.get("last")),
                        "approved": len(prd.get("approved") or []),
                        "deferred": prd.get("deferred", 0),
                        "mode": study_mode(prd.get("mode")),
                    }
                else:
                    # round drained — keep the approved tombstone (if any):
                    # the preview done-screen undo still needs it; mode rides
                    # along so 're-preview' chains the same pacing
                    approved = prd.get("approved") or []
                    _preview_write(
                        {
                            "status": "complete",
                            "approved": approved,
                            "mode": study_mode(prd.get("mode")),
                        }
                        if approved
                        else None
                    )
        except Exception:
            preview["preview_pool"] = None

    rd = current_round()
    reading: dict = {}
    if READING_MODE:
        try:
            reading = await _reading_extras()
        except Exception:
            reading = {"reading_mode": True}
    else:
        reading = {"reading_mode": False}
    if rd is None:
        return {
            "state": "none",
            "due_remaining": due_left,
            "new_per_round": STUDY_MODES[DEFAULT_MODE]["new"],
            "new_total": new_total,
            "can_undo": False,
            **preview,
            **reading,
        }

    mode = study_mode(rd.get("mode"))
    if rd.get("status") == "complete" or not rd.get("pending"):
        return {
            "state": "complete",
            "done": rd.get("done_count", rd.get("total", 0)),
            "total": rd.get("total", 0),
            "new_in_batch": rd.get("new_count"),
            "due_remaining": due_left,
            "new_per_round": STUDY_MODES[mode]["new"],
            "new_total": new_total,
            "can_undo": bool((rd.get("last") or {}).get("snap")),
            "mode": mode,
            **preview,
            **reading,
        }

    cards = await restore_round_cards(rd)
    if rd.get("status") == "complete" or not cards:
        return {
            "state": "complete",
            "done": rd.get("done_count", 0),
            "total": rd.get("total", 0),
            "new_in_batch": rd.get("new_count"),
            "due_remaining": due_left,
            "new_per_round": STUDY_MODES[study_mode(rd.get("mode"))]["new"],
            "new_total": new_total,
            "can_undo": bool((rd.get("last") or {}).get("snap")),
            "mode": study_mode(rd.get("mode")),
            **preview,
            **reading,
        }
    return {
        "state": "active",
        "cards": cards,
        "done": rd.get("done_count", 0),
        "total": rd.get("total", len(cards)),
        "new_in_batch": rd.get("new_count"),
        "due_remaining": due_left,
        "new_per_round": STUDY_MODES[mode]["new"],
        "new_total": new_total,
        "can_undo": bool((rd.get("last") or {}).get("snap")),
        "mode": mode,
        **preview,
        **reading,
    }


@app.post("/api/session/more")
async def session_more():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _session_more_impl()


async def _session_more_impl():
    """'Keep going' — another full batch: reviews + a fresh random new draw.

    INHERITS the pacing mode of the round that just finished (2026-09-14):
    a focus session chains focus-sized batches, a quick session quick ones.
    """
    if PREVIEW_MODE:
        await release_yesterday_approved()
    rd = current_round()
    mode = study_mode((rd or {}).get("mode"))
    m = STUDY_MODES[mode]
    cards, due_remaining, new_total = await build_batch(
        m["review"], allow_new=True, new_count=m["new"]
    )
    if cards:
        _round_write(
            {
                "status": "active",
                "created": datetime.now().isoformat(timespec="seconds"),
                "total": len(cards),
                "done_count": 0,
                "pending": [c["cardId"] for c in cards],
                "new_count": sum(1 for c in cards if c["isNew"]),
                "mode": mode,
            }
        )
    # capped table (2026-09-16) — same as session/start
    budget = None
    if PREVIEW_MODE:
        try:
            budget = await _release_budget_left()
        except Exception:
            budget = None
    return {
        "cards": cards,
        "due_remaining": due_remaining,
        "new_per_round": m["new"],
        "new_total": new_total,
        "mode": mode,
        "study_modes": _capped_study_modes(budget),
    }


@app.post("/api/session/finish")
async def session_finish():
    """State write under _state_lock; the (slow) sync runs outside it."""
    async with _state_lock:
        _round_write(None)
    synced = await do_sync()
    return {"synced": synced}


@app.post("/api/answer")
async def answer(card_id: int, ease: int):
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _answer_impl(card_id, ease)


async def _answer_impl(card_id: int, ease: int):
    if ease not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="ease must be 1-4")

    # Guard: only cards in the ACTIVE round may be answered. Refuse BEFORE
    # answerCards — otherwise a stale client (or a stray request) could
    # schedule cards that were answered on another device or never dealt,
    # and the scheduling would run twice. The client resyncs via
    # /api/session/state when it sees answered=false.
    rd_check = current_round()
    if (
        not rd_check
        or rd_check.get("status") != "active"
        or card_id not in rd_check.get("pending", [])
    ):
        return {"answered": False, "reason": "stale", "round": None}

    # Snapshot scheduling state BEFORE answering. Our background sync wipes
    # Anki's native undo stack, so /api/undo restores from this snapshot via
    # setSpecificValueOfCard instead of guiUndo.
    snap = None
    infos = await anki("cardsInfo", {"cards": [card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is not None:
        snap = {k: info.get(k) for k in SNAPSHOT_FIELDS}

    res = await anki("answerCards", {"answers": [{"cardId": card_id, "ease": ease}]})
    if not (res and res[0]):
        raise HTTPException(status_code=404, detail="card not found")

    # update round state synchronously (no awaits between read and write)
    round_info = None
    rd = current_round()
    if rd and rd.get("status") == "active" and card_id in rd.get("pending", []):
        idx_pos = rd["pending"].index(card_id)
        rd["pending"] = [c for c in rd["pending"] if c != card_id]
        rd["done_count"] = rd.get("done_count", 0) + 1
        # single-level undo slot: where the card sat + how to restore it
        rd["last"] = (
            {"cardId": card_id, "index": idx_pos, "ease": ease, "snap": snap}
            if snap
            else None
        )
        if not rd["pending"]:
            rd["status"] = "complete"
        _round_write(rd)
        round_info = {
            "state": rd["status"],
            "done": rd["done_count"],
            "total": rd.get("total", rd["done_count"]),
            "mode": study_mode(rd.get("mode")),
        }
        # round just finished: hand the UI fresh pool/quota numbers for the done page
        if rd["status"] == "complete":
            try:
                due_ids = await anki("findCards", {"query": "is:due -is:new"})
                round_info["due_remaining"] = len(due_ids or [])
                new_ids = await anki("findCards", {"query": new_card_query()})
                round_info["new_total"] = len(new_ids or [])
            except Exception:
                pass
            round_info["new_per_round"] = STUDY_MODES[study_mode(rd.get("mode"))]["new"]

    # ---- double-Again auto-return (user spec 2026-09-16) ----
    # A NEW card whose first two consecutive grades are both Again goes
    # back to the preview pool. Non-Again grades clear the streak. Runs
    # AFTER the round bookkeeping so the undo slot is already recorded —
    # the hook marks it auto_return so /api/undo can reverse the move.
    returned = None
    if PREVIEW_MODE and NEW_AGAIN_RETURN:
        if ease == 1:
            returned = await _maybe_auto_return_to_preview(card_id, ease, info)
        else:
            _streak_clear(card_id)
    if returned is not None:
        fire_and_forget_sync()
        return {"answered": True, "round": round_info, "returned_to_preview": returned}

    fire_and_forget_sync()
    return {"answered": True, "round": round_info}


@app.post("/api/undo")
async def undo():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _undo_impl()


async def _undo_impl():
    """Undo the last answer of the current round.

    Sync wipes Anki's native undo stack, so this restores the card's
    scheduling fields from the pre-answer snapshot and puts the card back
    into the round at its original position.
    """
    rd = current_round()
    last = (rd or {}).get("last")
    if not rd or not last or not last.get("snap"):
        raise HTTPException(status_code=409, detail="nothing to undo")

    cid = last["cardId"]
    snap = last["snap"]
    values = [
        snap["interval"],
        snap["factor"],
        snap["due"],
        snap["reps"],
        snap["lapses"],
        snap["left"],
        snap["type"],
        snap["queue"],
    ]
    res = await anki(
        "setSpecificValueOfCard",
        {
            "card": cid,
            "keys": RESTORE_KEYS,
            "newValues": values,
            "warning_check": True,
        },
    )
    if not (res and res[0] is True):
        raise HTTPException(status_code=502, detail=f"restore failed: {res}")

    # the answer triggered a double-Again auto-return (2026-09-16): the card
    # was forget'd, moved to PREVIEW_DECK and suspended AFTER the snapshot.
    # Field restore alone would leave a scheduled card sitting suspended in
    # the pool — also undo the deck move + suspend so it's a normal due card.
    if last.get("auto_return"):
        try:
            await anki("changeDeck", {"cards": [cid], "deck": RELEASE_DECK})
            await anki("unsuspend", {"cards": [cid]})
        except Exception:
            pass  # best-effort: the scheduling restore above is the critical part

    # put the card back into the round where it was (defensive: never
    # duplicate if it somehow never left)
    pos = min(last.get("index", 0), len(rd.get("pending", [])))
    if cid not in rd.get("pending", []):
        rd["pending"].insert(pos, cid)
    rd["done_count"] = max(0, rd.get("done_count", 0) - 1)
    if rd.get("status") == "complete":
        rd["status"] = "active"
    rd.pop("last", None)
    _round_write(rd)

    # the undone answer had triggered a double-Again auto-return: the streak
    # file was cleared by the hook, so put the counter back to just-below the
    # threshold — a fresh Again after the undo correctly re-triggers the return
    if last.get("auto_return") and PREVIEW_MODE:
        d = _streak_read()
        d[str(cid)] = max(1, NEW_AGAIN_LIMIT - 1)
        _streak_write(d)
    elif last.get("ease") == 1 and PREVIEW_MODE:
        # plain Again answer undone (no auto-return yet): give the streak
        # count back so the counter mirrors what actually happened
        d = _streak_read()
        k = str(cid)
        if k in d:
            d[k] -= 1
            if d[k] <= 0:
                d.pop(k)
            _streak_write(d)

    cards = await fetch_cards([cid])
    if not cards:
        raise HTTPException(status_code=404, detail="card vanished after restore")

    # propagate the corrected scheduling to the sync server
    fire_and_forget_sync()
    out: dict = {"restored": True, "card": cards[0], "index": pos}
    if last.get("auto_return"):
        try:
            out["returned_from_preview"] = {"cardId": cid, "pool": await preview_pool_count()}
        except Exception:
            out["returned_from_preview"] = {"cardId": cid, "pool": None}
    return out


# ---- preview mode: pool, rounds, endpoints --------------------------------

def _preview_guard():
    """Preview endpoints only exist when the feature flag is on."""
    if not PREVIEW_MODE:
        raise HTTPException(status_code=404, detail="preview mode disabled")


async def preview_pool_ids() -> list[int]:
    """Suspended new cards sitting in the preview deck, oldest first.

    Sorted by card id (a creation-timestamp ms epoch) rather than relying
    on Anki's current browser sort order.
    """
    ids = (
        await anki(
            "findCards",
            {"query": f'deck:"{PREVIEW_DECK}" is:new is:suspended'},
        )
        or []
    )
    return sorted(ids)


async def _deferred_today_cards(pool_ids: list[int]) -> set[int]:
    """Cards deferred TODAY (tag deferred-YYYYMMDD) — hidden from preview."""
    if not pool_ids:
        return set()
    tag = "deferred-" + datetime.now().strftime("%Y%m%d")
    ids = await anki(
        "findCards", {"query": f'deck:"{PREVIEW_DECK}" tag:{tag} is:new'}
    )
    return set(ids or [])


def _preview_read() -> dict | None:
    STATE_DIR.mkdir(exist_ok=True)
    if not PREVIEW_FILE.exists():
        return None
    try:
        rd = json.loads(PREVIEW_FILE.read_text())
        return rd if rd.get("status") in ("active", "complete") else None
    except Exception:
        return None


def _preview_write(rd: dict | None):
    STATE_DIR.mkdir(exist_ok=True)
    if rd is None:
        PREVIEW_FILE.unlink(missing_ok=True)
    else:
        tmp = PREVIEW_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rd))
        tmp.replace(PREVIEW_FILE)


async def preview_pool_count() -> int:
    return len(await preview_pool_ids())


# ---- double-Again auto-return state (user spec 2026-09-16) ---------------
# {card_id(str): consecutive-Again count in the card's FIRST learning cycle}.
# Lives in its own file (not round.json) because the two Agains usually land
# in DIFFERENT rounds — Anki's relearning steps re-deal the card minutes
# later, often into the next batch. Cleared by: any non-Again grade, undo,
# manual 移回预览池, delete, and the auto-return itself.

def _streak_read() -> dict[str, int]:
    STATE_DIR.mkdir(exist_ok=True)
    if not NEW_AGAIN_FILE.exists():
        return {}
    try:
        d = json.loads(NEW_AGAIN_FILE.read_text())
        return {str(k): int(v) for k, v in d.items() if isinstance(v, int)}
    except Exception:
        return {}


def _streak_write(d: dict[str, int]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = NEW_AGAIN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(NEW_AGAIN_FILE)


def _streak_clear(card_id: int) -> None:
    d = _streak_read()
    if str(card_id) in d:
        d.pop(str(card_id))
        _streak_write(d)


def _streak_note_again(card_id: int, card_is_new: bool) -> int:
    """Record one Again grade; returns the new streak length.

    The streak only tracks a NEW card's first learning cycle (初学): the
    counter OPENS while the card is still type==0 and KEEPS counting across
    its relearning steps (the second Again lands on a type==2 card, so the
    guard is 'already tracking', not 'still new'). Review cards that lapse
    never start a streak.
    """
    key = str(card_id)
    d = _streak_read()
    if card_is_new or key in d:
        d[key] = d.get(key, 0) + 1
        _streak_write(d)
        return d[key]
    return 0


async def _maybe_auto_return_to_preview(
    card_id: int, ease: int, info: dict | None
) -> dict | None:
    """Double-Again auto-return hook, called from /api/answer.

    Returns {"cardId", "pool"} when the card was moved back to the preview
    pool, else None. Fail-soft: any error degrades to 'no auto-return' —
    the grade itself already landed, review must never break here.
    """
    if not (NEW_AGAIN_RETURN and PREVIEW_MODE and ease == 1):
        return None
    try:
        streak = _streak_note_again(card_id, bool(info and info.get("type") == 0))
        if streak < NEW_AGAIN_LIMIT:
            return None
        _streak_clear(card_id)
        pool = (await _to_preview_pool_impl(card_id)).get("pool")
        # _to_preview_pool_impl leaves the round's undo slot alone (the card
        # already left pending when it was answered); just MARK the existing
        # slot so /api/undo knows to also move the card back out of the pool.
        rd = current_round()
        if rd is not None:
            last = rd.get("last")
            if isinstance(last, dict) and last.get("cardId") == card_id:
                last["auto_return"] = True
                _round_write(rd)
        return {"cardId": card_id, "pool": pool}
    except Exception:
        return None


async def release_yesterday_approved() -> int:
    """Next-day release (2026-09-07): unsuspend cards approved on a PREVIOUS
    calendar day.

    Approve now keeps the card SUSPENDED in RELEASE_DECK with a
    `released-YYYYMMDD` stamp tag. This routine runs at the entry points
    (session/state, session/start, preview/state, preview/start) and
    unsuspends every stamped card whose date is strictly before today —
    so a card previewed on day N first becomes gradeable on day N+1. The
    overnight gap turns the first grade from recognition (fluency illusion)
    into honest recall, which is what FSRS needs to seed stability.

    Fail-soft: any error leaves cards suspended (they release on a later
    run) — never blocks the caller.
    """
    try:
        # deck-scoped: a released- stamp on a card back in the preview pool
        # (undo path) must NOT release it there — the stamp is removed on
        # undo anyway, but scope the query too for belt-and-braces
        ids = await anki(
            "findCards",
            {
                "query": (
                    f'deck:"{RELEASE_DECK}" is:new is:suspended tag:released-*'
                )
            },
        ) or []
        if not ids:
            return 0
        # NOTE: cardsInfo does NOT return tags (always None) — tags come
        # from notesInfo. Verified against AnkiConnect 2026-09-07.
        infos = [i for i in await anki("cardsInfo", {"cards": ids}) or [] if i.get("cardId")]
        if not infos:
            return 0
        note_ids = list({i["note"] for i in infos if i.get("note")})
        ntags: dict[int, list[str]] = {}
        for n in await anki("notesInfo", {"notes": note_ids}) or []:
            if n.get("noteId"):
                ntags[n["noteId"]] = n.get("tags") or []
        today = _anki_day()
        ready = []
        for i in infos:
            if i.get("type") != 0:
                continue
            stamps = [
                t[len(RELEASED_TAG_PREFIX):]
                for t in ntags.get(i.get("note"), [])
                if t.startswith(RELEASED_TAG_PREFIX)
            ]
            if stamps and min(stamps) < today:
                ready.append(i["cardId"])
        if not ready:
            return 0
        await anki("unsuspend", {"cards": ready})
        fire_and_forget_sync()
        return len(ready)
    except Exception:
        return 0


async def _pending_release_today() -> int:
    """Cards approved TODAY — still suspended, will release at tomorrow's
    first entry point. Reported in the wire payload so the UI can explain
    why the just-approved cards are not in the new pool yet."""
    stamp = RELEASED_TAG_PREFIX + _anki_day()
    ids = await anki(
        "findCards",
        {"query": f'deck:"{RELEASE_DECK}" is:new is:suspended tag:{stamp}'},
    )
    return len(ids or [])


async def _release_budget_left(pending_release: int | None = None) -> int | None:
    """Remaining daily release budget (user spec 2026-09-16).

    RELEASE_DAILY_GOAL − cards approved today (the SAME number the preview
    start screen's progress bar shows, so cap and bar can never disagree).
    Pass an already-computed `pending_release` to avoid a second AnkiConnect
    round-trip. None when the goal is disabled (<=0) — no cap, legacy
    behavior. Deferred cards (明天再看) do NOT consume the budget (they
    aren't released); undoing an approval gives the budget back
    automatically because pending_release is recomputed from tags on every
    call.
    """
    if RELEASE_DAILY_GOAL <= 0:
        return None
    if pending_release is None:
        pending_release = await _pending_release_today()
    return max(0, RELEASE_DAILY_GOAL - pending_release)


@app.get("/api/preview/state")
async def preview_state():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _preview_state_impl()


async def _preview_state_impl():
    """Entry point for the preview UI: pool size + in-progress round resume."""
    _preview_guard()
    await release_yesterday_approved()
    try:
        due_left = len(
            await anki("findCards", {"query": "is:due -is:new"}) or []
        )
    except Exception:
        due_left = None

    rd = _preview_read()
    if rd is not None and rd.get("status") == "active" and rd.get("pending"):
        infos = await anki("cardsInfo", {"cards": rd["pending"]})
        # drop cards that left the pool since (released/deferred/moved)
        still = {
            i["cardId"]: i
            for i in infos or []
            if i.get("cardId")
            and PREVIEW_DECK in i.get("deckName", "")
            and i.get("type") == 0
        }
        pending = [c for c in rd["pending"] if c in still]
        dropped = len(rd["pending"]) - len(pending)
        rd["pending"] = pending
        rd["done"] = rd.get("done", 0) + dropped
        if pending:
            _preview_write(rd)
            cards = await fetch_cards(pending)
            return {
                "round": "active",
                "cards": cards,
                "done": rd.get("done", 0),
                "total": rd.get("total", rd.get("done", 0) + len(pending)),
                "pool": len(await preview_pool_ids()),
                "due_remaining": due_left,
                "can_undo": bool(rd.get("last")),
                # approve/defer split, so a resumed round (page refresh) can
                # still report honest numbers on the done / exit screens
                "approved": len(rd.get("approved") or []),
                "deferred": rd.get("deferred", 0),
                "mode": study_mode(rd.get("mode")),
            }
        # round drained — keep the approved tombstone (if any)
        approved = rd.get("approved") or []
        _preview_write(
            {
                "status": "complete",
                "approved": approved,
                "mode": study_mode(rd.get("mode")),
            }
            if approved
            else None
        )

    pool = await preview_pool_ids()
    deferred = await _deferred_today_cards(pool)
    available = len(pool) - len(deferred)
    return {
        "round": "none",
        "pool": len(pool),
        "available": available,
        "due_remaining": due_left,
    }


@app.post("/api/preview/start")
async def preview_start(mode: str | None = None):
    """Serialized under _state_lock (see its declaration).

    do_sync() runs OUTSIDE the lock: a cold sync can take minutes, and
    holding the state lock that long would block every other endpoint
    (including GET state). The lock only protects the pool read + deal.

    `mode` picks the preview batch size (quick=1, focus=5); it is stored in
    preview.json so a refresh / 'more' keeps the same pacing, and handed to
    the frontend so 'skip to review' starts a review round of the SAME mode.
    """
    _preview_guard()
    await do_sync()  # pull anything injected elsewhere
    async with _state_lock:
        return await _preview_start_impl(study_mode(mode))


async def _preview_start_impl(mode: str = DEFAULT_MODE):
    """Deal one preview batch: a random sample of pool cards not deferred today.

    Release-budget cap (user spec 2026-09-16): the batch is min(mode size,
    remaining daily budget) — once fewer cards are left than a full round,
    EVERY mode deals exactly the remainder, so the day lands on the goal
    instead of overshooting it. Budget exhausted → no deal at all
    (goal_reached), the day's previews are done.
    """
    _preview_guard()
    await release_yesterday_approved()
    budget = await _release_budget_left()
    pool = await preview_pool_ids()
    deferred = await _deferred_today_cards(pool)
    available = [c for c in pool if c not in deferred]
    if budget is not None and budget <= 0:
        return {
            "cards": [],
            "pool": len(pool),
            "available": len(available),
            "mode": mode,
            "goal_reached": True,
            "study_modes": _capped_study_modes(budget),
        }
    # random draw (2026-09-04 spec) — mirrors the review-round new draw
    per_round = _capped_preview_size(mode, budget)
    pending = random.sample(available, min(per_round, len(available)))
    if not pending:
        return {"cards": [], "pool": len(pool), "available": 0, "mode": mode}
    _preview_write(
        {
            "status": "active",
            "created": datetime.now().isoformat(timespec="seconds"),
            "total": len(pending),
            "done": 0,
            "pending": pending,
            "mode": mode,
        }
    )
    return {
        "cards": await fetch_cards(pending),
        "pool": len(pool),
        "available": len(pool) - len(deferred) - len(pending),
        "mode": mode,
        "preview_per_round": per_round,
        # capped table so the UI's size labels track the budget immediately
        "study_modes": _capped_study_modes(budget),
    }


@app.post("/api/preview/act")
async def preview_act(card_id: int, action: str):
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _preview_act_impl(card_id, action)


async def _preview_act_impl(card_id: int, action: str):
    """Per-card decision inside a preview round.

    approve (放行): move card to RELEASE_DECK and unsuspend — it becomes a
        regular new card in the dealable pool, from which every study round
        draws at random (no more 先看后考 priority deal).
    defer (明天再看): tag deferred-YYYYMMDD so today's preview skips it.
    Both are idempotent-ish; both verify the card is still in the round.
    """
    _preview_guard()
    if action not in ("approve", "defer"):
        raise HTTPException(status_code=400, detail="action must be approve|defer")
    rd = _preview_read()
    if not rd or card_id not in rd.get("pending", []):
        return {"ok": False, "reason": "stale"}

    infos = await anki("cardsInfo", {"cards": [card_id]})
    # AnkiConnect returns [{}] for a vanished id — filter before indexing,
    # else infos[0]["note"] is a KeyError → 500 (audit hardening spot)
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is None:
        raise HTTPException(status_code=404, detail="card vanished")
    # single-level undo slot (mirrors the review round's "last"): enough to
    # reverse this exact act — deck+suspend for approve, note+tag for defer
    rd["last"] = {
        "cardId": card_id,
        "index": rd["pending"].index(card_id),
        "action": action,
        "note": info["note"],
    }
    if action == "approve":
        # next-day release (2026-09-07): move to RELEASE_DECK but keep it
        # SUSPENDED with a released-YYYYMMDD stamp — release_yesterday_approved()
        # unsuspends it the next calendar day, so the first grading happens
        # ≥1 night after preview (honest recall, not recognition).
        await anki("changeDeck", {"cards": [card_id], "deck": RELEASE_DECK})
        stamp = RELEASED_TAG_PREFIX + _anki_day()
        await anki("addTags", {"notes": [info["note"]], "tags": stamp})
        # provenance tag: marks this note as having passed through the
        # preview flow. The pool sweeper (anki_pool_sweep.py) uses it to
        # tell legitimately-released cards apart from leaks that bypassed
        # the pool (e.g. added on the desktop client straight into 2026).
        await anki("addTags", {"notes": [info["note"]], "tags": "previewed"})
        rd.setdefault("approved", []).append(card_id)
    else:
        tag = "deferred-" + datetime.now().strftime("%Y%m%d")
        await anki("addTags", {"notes": [info["note"]], "tags": tag})
        # explicit counter: `done` also absorbs cards that left the pool
        # (deleted/released elsewhere), so done - approved is NOT the
        # deferred count. The resume payload reports both (2026-09-06).
        rd["deferred"] = rd.get("deferred", 0) + 1

    # deck move / tag change must reach the sync server (and thus the
    # user's phone + desktop) — same coalesced sync the answer path uses
    fire_and_forget_sync()

    # pending BEFORE this act — needed for the tombstone so undo can
    # rehydrate the round exactly as it stood (返回上一张)
    pending_before = rd.get("pending", [])
    rd["pending"] = [c for c in rd["pending"] if c != card_id]
    rd["done"] = rd.get("done", 0) + 1
    if not rd["pending"]:
        # tombstone: kept for the preview done-screen undo (the priority
        # deal was removed 2026-09-04 — approved cards just join the pool).
        # ALSO keep pending_before (= [card_id] here, the final card) plus
        # done/total: the undo slot indexes into this round's deal order,
        # and undoing the final act must restore the round's progress.
        _preview_write(
            {
                "status": "complete",
                "approved": rd.get("approved", []),
                "pending": pending_before,
                "done": rd.get("done", 0),
                "total": rd.get("total", rd.get("done", 0)),
                "last": rd.get("last"),
                "mode": study_mode(rd.get("mode")),
            }
        )
        remaining = await preview_pool_ids()
        deferred = await _deferred_today_cards(remaining)
        # refreshed budget + capped table (2026-09-16): the done screen's
        # 「再预览 N 张」 label and the start screens must reflect the
        # approvals from the round that just finished without a reload
        try:
            budget = await _release_budget_left()
        except Exception:
            budget = None
        return {
            "ok": True,
            "round_complete": True,
            "pool": len(remaining),
            "available": len(remaining) - len(deferred),
            "mode": study_mode(rd.get("mode")),
            "release_budget_left": budget,
            "study_modes": _capped_study_modes(budget),
        }
    _preview_write(rd)
    return {"ok": True, "round_complete": False}


@app.post("/api/preview/finish")
async def preview_finish():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _preview_finish_impl()


async def _preview_finish_impl():
    """End the preview round; untouched cards stay suspended in the pool.

    Cards already approved this session are kept as a tombstone so the
    done-screen undo keeps working.
    """
    _preview_guard()
    rd = _preview_read()
    approved = (rd or {}).get("approved") or []
    if approved:
        _preview_write(
            {
                "status": "complete",
                "approved": approved,
                "mode": study_mode((rd or {}).get("mode")),
            }
        )
    else:
        _preview_write(None)
    return {"ok": True, "pool": await preview_pool_count()}


@app.post("/api/preview/undo")
async def preview_undo():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _preview_undo_impl()


async def _preview_undo_impl():
    """Undo the last preview act (预览环节的"返回上一张").

    Exact reverse of /api/preview/act: an approved card goes back into the
    pool suspended at its original position (and leaves the approved list);
    a deferred card loses today's deferred tag. Single slot, same shape as
    the review-round undo. Refuses when the card moved on since (e.g. an
    approved card already answered in a review round) — reversing then
    would corrupt real scheduling state.
    """
    _preview_guard()
    rd = _preview_read()
    last = (rd or {}).get("last")
    if not rd or not last:
        raise HTTPException(status_code=409, detail="nothing to undo")
    cid = last["cardId"]
    infos = await anki("cardsInfo", {"cards": [cid]})
    info = (infos or [None])[0]
    if info is None or not info.get("cardId"):
        raise HTTPException(status_code=409, detail="card vanished")
    if last.get("action") == "approve":
        # only still-untouched new cards can be pulled back into the pool
        if info["type"] != 0 or info["deckName"] != RELEASE_DECK:
            raise HTTPException(status_code=409, detail="card moved on")
        await anki("changeDeck", {"cards": [cid], "deck": PREVIEW_DECK})
        await anki("suspend", {"cards": [cid]})
        # drop today's released- stamp (next-day release marker, 2026-09-07)
        # so the card never leaks into tomorrow's release batch from the pool
        stamp = RELEASED_TAG_PREFIX + _anki_day()
        await anki(
            "removeTags",
            {"notes": [last.get("note") or info["note"]], "tags": stamp},
        )
        rd["approved"] = [c for c in rd.get("approved", []) if c != cid]
    else:
        if info["type"] != 0 or PREVIEW_DECK not in info.get("deckName", ""):
            raise HTTPException(status_code=409, detail="card moved on")
        tag = "deferred-" + datetime.now().strftime("%Y%m%d")
        await anki(
            "removeTags", {"notes": [last.get("note") or info["note"]], "tags": tag}
        )
        rd["deferred"] = max(0, rd.get("deferred", 0) - 1)

    pending = rd.get("pending") or []
    pos = min(last.get("index", 0), len(pending))
    if cid not in pending:
        pending.insert(pos, cid)
    rd["pending"] = pending
    rd["done"] = max(0, rd.get("done", 0) - 1)
    rd["total"] = rd["done"] + len(pending)
    rd["status"] = "active"
    rd.pop("last", None)  # single-level slot, consumed
    _preview_write(rd)
    # deck move / suspend / tag change must reach the sync server
    fire_and_forget_sync()
    cards = await fetch_cards([cid])
    if not cards:
        raise HTTPException(status_code=404, detail="card vanished after restore")
    return {"restored": True, "card": cards[0], "index": pos, "action": last.get("action")}


@app.get("/api/note")
async def get_note(card_id: int):
    """Raw note fields behind a card, for the plain-text edit dialog."""
    infos = await anki("cardsInfo", {"cards": [card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is None:
        raise HTTPException(status_code=404, detail="card not found")
    note_id = info["note"]
    notes = await anki("notesInfo", {"notes": [note_id]})
    n = next((x for x in notes or [] if x.get("noteId")), None)
    if n is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {
        "cardId": card_id,
        "noteId": note_id,
        "modelName": n["modelName"],
        "fields": {name: fd["value"] for name, fd in n["fields"].items()},
        "tags": n.get("tags", []),
    }


class NoteUpdate(BaseModel):
    card_id: int
    fields: dict[str, str]
    tags: list[str] | None = None  # None = keep tags; list = replace entirely


@app.post("/api/note/update")
async def update_note(body: NoteUpdate):
    """Field edit (HTML round-trips verbatim into Anki) + optional tag replace."""
    infos = await anki("cardsInfo", {"cards": [body.card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is None:
        raise HTTPException(status_code=404, detail="card not found")
    note_id = info["note"]
    await anki(
        "updateNoteFields", {"note": {"id": note_id, "fields": body.fields}}
    )
    if body.tags is not None:
        await anki("updateNoteTags", {"note": note_id, "tags": body.tags})
    # hand back the re-rendered card so the UI refreshes without a reload
    fresh = await anki("cardsInfo", {"cards": [body.card_id]})
    f = next((i for i in fresh or [] if i.get("cardId")), None)
    if f is None:
        raise HTTPException(status_code=404, detail="card not found after update")
    return {
        "updated": True,
        "question": f["question"],
        "answer": f["answer"],
    }


# ---- card lifecycle: add / delete / back to preview pool ------------------
# Add (user spec 2026-09-04): fixed 问答题 note type (same as the add_cards.py
# injection pipeline); the new card is routed into the preview pool SUSPENDED,
# exactly like add_cards.py's default — it joins the real rotation only after
# a preview round approves it.
# Delete / to-preview are destructive per-card actions with confirmation
# dialogs on the frontend. Both keep any active round consistent: the card
# counts as handled (done_count +1) and the single-level undo slot is cleared
# when it targeted the removed card (undo cannot resurrect what's gone).

ADD_MODEL = os.getenv("ANKI_ADD_MODEL", "问答题")
# 挖空卡 (user spec 2026-09-19): cloze notes use Anki's 填空题 model —
# fields are data-driven via modelFieldNames, so a renamed collection model
# only needs the env var. Cloze markers {{cN::…}} are validated before add.
ADD_CLOZE_MODEL = os.getenv("ANKI_ADD_CLOZE_MODEL", "填空题")


def _round_remove_card(rd: dict | None, card_id: int) -> None:
    """A card left the review round without being answered (deleted or sent
    back to the preview pool): count it as handled so progress, the
    review/new split and the completion detection all stay correct."""
    if not rd or rd.get("status") != "active" or card_id not in rd.get("pending", []):
        return
    rd["pending"] = [c for c in rd["pending"] if c != card_id]
    rd["done_count"] = rd.get("done_count", 0) + 1
    if (rd.get("last") or {}).get("cardId") == card_id:
        rd.pop("last", None)
    if not rd["pending"]:
        rd["status"] = "complete"
    _round_write(rd)


def _preview_round_remove_card(rd: dict | None, card_id: int) -> None:
    """Same bookkeeping for an in-flight preview round."""
    if not rd or rd.get("status") != "active" or card_id not in rd.get("pending", []):
        return
    rd["pending"] = [c for c in rd["pending"] if c != card_id]
    rd["done"] = rd.get("done", 0) + 1
    if (rd.get("last") or {}).get("cardId") == card_id:
        rd.pop("last", None)
    if not rd["pending"]:
        # round drained by removal — keep the approved tombstone so the
        # preview done-screen undo for earlier acts keeps working
        _preview_write({"status": "complete", "approved": rd.get("approved", [])})
    else:
        _preview_write(rd)


@app.get("/api/card/add/info")
async def add_info(kind: str = "qa"):
    """Note type + field names for the add-card dialog (data-driven labels).

    kind=cloze returns the 挖空 model (填空题) instead of the default 问答题.
    """
    model = ADD_CLOZE_MODEL if kind == "cloze" else ADD_MODEL
    names = await anki("modelFieldNames", {"modelName": model})
    return {"model_name": model, "fields": names or [], "kind": kind}


class NoteAdd(BaseModel):
    fields: dict[str, str]
    tags: list[str] = []
    # 挖空卡 (user spec 2026-09-19): kind="cloze" creates a 填空题 note
    # instead of the default 问答题 — same preview-pool routing.
    kind: str = "qa"
    # 渐进制卡 provenance (user spec 2026-09-19): when the card was written
    # from a reading chunk, {path, chunk_key} records the source so the note
    # id lands in that chunk's cards_created (溯源: which cards came from
    # which fragment). Omitted for cards added outside reading rounds.
    reading_source: dict | None = None


@app.post("/api/card/add")
async def add_card(body: NoteAdd):
    """Create one card (ADD_MODEL, or ADD_CLOZE_MODEL when kind=cloze) and
    route it into the preview pool.

    Preview mode on: lands in PREVIEW_DECK suspended — invisible to the
    scheduler until a preview round approves it (先看后考, same route as
    add_cards.py's default). Preview mode off: stays in RELEASE_DECK as a
    regular new card.
    """
    if not any(html_to_text(v) for v in body.fields.values()):
        raise HTTPException(status_code=400, detail="卡片内容不能为空")
    model = ADD_CLOZE_MODEL if body.kind == "cloze" else ADD_MODEL
    # 挖空卡 must carry at least one cloze marker — otherwise Anki creates a
    # note with ZERO cards and findCards below returns [] (a confusing 502).
    if body.kind == "cloze":
        joined = " ".join(body.fields.values())
        if "{{c" not in joined:
            raise HTTPException(status_code=400, detail="挖空卡至少要有一个 {{c1::…}} 挖空")
    try:
        note_ids = await anki(
            "addNotes",
            {
                "notes": [
                    {
                        "deckName": RELEASE_DECK,
                        "modelName": model,
                        "fields": body.fields,
                        "tags": body.tags,
                        # collection-wide duplicate check (防呆)
                        "options": {"allowDuplicate": False, "duplicateScope": "collection"},
                    }
                ]
            },
        )
    except HTTPException as e:
        # AnkiConnect raises on duplicates ("cannot create note because it
        # is a duplicate") instead of returning [None] — map it to a clean 409
        if "duplicate" in str(e.detail).lower():
            raise HTTPException(status_code=409, detail="添加失败：与已有卡片重复")
        raise
    if not note_ids or note_ids[0] is None:
        raise HTTPException(status_code=409, detail="添加失败：与已有卡片重复")
    note_id = note_ids[0]
    card_ids = await anki("findCards", {"query": f"nid:{note_id}"}) or []
    if not card_ids:
        raise HTTPException(status_code=502, detail="note created but no card found")
    pool = None
    if PREVIEW_MODE:
        await anki("changeDeck", {"cards": card_ids, "deck": PREVIEW_DECK})
        await anki("suspend", {"cards": card_ids})
        pool = await preview_pool_count()
    # 渐进制卡 provenance: record the note id on its source chunk (fail-soft —
    # reading bookkeeping must never break card creation)
    if READING_MODE and body.reading_source:
        try:
            _reading_record_card(body.reading_source, note_id)
        except Exception:
            pass
    fire_and_forget_sync()
    return {"noteId": note_id, "cardIds": card_ids, "pool": pool}


@app.post("/api/card/delete")
async def delete_card(card_id: int):
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _delete_card_impl(card_id)


async def _delete_card_impl(card_id: int):
    """Delete the note behind a card (all of its cards go with it)."""
    infos = await anki("cardsInfo", {"cards": [card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is None:
        raise HTTPException(status_code=404, detail="card not found")
    note_id = info["note"]
    await anki("deleteNotes", {"notes": [note_id]})
    # bookkeeping AFTER a successful delete — never strand round state
    _round_remove_card(current_round(), card_id)
    _preview_round_remove_card(_preview_read(), card_id)
    _streak_clear(card_id)  # a deleted card can't carry an Again streak
    fire_and_forget_sync()
    return {"deleted": True}


@app.post("/api/card/to-preview")
async def to_preview_pool(card_id: int):
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _to_preview_pool_impl(card_id)


async def _to_preview_pool_impl(card_id: int):
    """Send a card back to the preview pool for re-learning (user spec).

    The card's scheduling history is CLEARED first (forgetCards → fresh new
    card): the pool query is `is:new is:suspended`, and 重新学习 means
    starting over. Without the reset the card would sit suspended forever,
    invisible to both the pool and the scheduler.
    """
    _preview_guard()
    infos = await anki("cardsInfo", {"cards": [card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is None:
        raise HTTPException(status_code=404, detail="card not found")
    if info.get("deckName") == PREVIEW_DECK:
        raise HTTPException(status_code=409, detail="already in the preview pool")
    await anki("forgetCards", {"cards": [card_id]})
    # forgetCards keeps reps/lapses by default (Anki's own Forget behavior);
    # the user spec wants the counts ZEROED too (清零次数) — wipe explicitly
    await anki(
        "setSpecificValueOfCard",
        {
            "card": card_id,
            "keys": ["reps", "lapses", "left"],
            "newValues": [0, 0, 0],
            "warning_check": True,
        },
    )
    await anki("changeDeck", {"cards": [card_id], "deck": PREVIEW_DECK})
    await anki("suspend", {"cards": [card_id]})
    # strip any released-YYYYMMDD stamps (next-day release markers): a card
    # re-approved later gets a FRESH stamp, and a stale old stamp would make
    # release_yesterday_approved() release it same-day (min(stamps) < today).
    # Tags come from notesInfo — cardsInfo does NOT return them.
    ntags = []
    try:
        ninfo = [n for n in await anki("notesInfo", {"notes": [info["note"]]}) or []
                 if n.get("noteId")]
        ntags = ninfo[0].get("tags") or [] if ninfo else []
    except Exception:
        pass
    stale_stamps = [t for t in ntags if t.startswith(RELEASED_TAG_PREFIX)]
    if stale_stamps:
        await anki(
            "removeTags", {"notes": [info["note"]], "tags": " ".join(stale_stamps)}
        )
    _round_remove_card(current_round(), card_id)
    _preview_round_remove_card(_preview_read(), card_id)
    _streak_clear(card_id)  # manual return = fresh start; drop any streak
    fire_and_forget_sync()
    return {"moved": True, "pool": await preview_pool_count()}


# ---- reading mode (渐进制卡, user spec 2026-09-19) -------------------------
# The user reads their own markdown notes chunk by chunk and writes cards by
# hand. Chunk states (human-marked): todo → active(正在制卡) → done(制卡完成),
# plus skipped(无需制卡) — done and skipped both unlock the file's next chunk.
# Within one file only the FRONTIER chunk (first not done/skipped) is ever
# dealt; an `active` chunk IS the frontier, so unfinished work automatically
# resurfaces at the head of the next reading round.
#
# Files are opt-in via the 阅读清单 (manual priority order = list order).
# Chunking reuses notes-rag's chunker.py (imported, NOT copied — single
# source of truth) over the ~/anki-notes corpus.
#
# Chunk identity: "{line_start}:{heading_path joined by >}". File edits shift
# line numbers, so on drift the stored states migrate: exact key first, then
# heading-path match (split parts of one section migrate in line order),
# anything unmatched becomes an orphan (chunk falls back to todo, old state
# parked in entry["orphans"] and surfaced as a count for a future repair UI).
#
# All mutating endpoints run under _state_lock (same serialization as the
# preview round); reads that may persist migrations take it too.

def _reading_guard():
    """Reading endpoints only exist when the feature flag is on."""
    if not READING_MODE:
        raise HTTPException(status_code=404, detail="reading mode disabled")


_chunker_mod = None


def _chunker():
    """Lazy-import notes-rag's chunker module (env NOTES_RAG_DIR)."""
    global _chunker_mod
    if _chunker_mod is None:
        if NOTES_RAG_DIR not in sys.path:
            sys.path.insert(0, NOTES_RAG_DIR)
        import chunker as _c

        _chunker_mod = _c
    return _chunker_mod


def _read_note_text(path: str) -> str | None:
    """Traversal-safe read of one corpus file (relative path under NOTES_DIR)."""
    try:
        root = NOTES_DIR.resolve()
        p = (NOTES_DIR / path).resolve()
        if not p.is_relative_to(root) or p.suffix.lower() != ".md" or not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _corpus_files() -> list[str]:
    """All non-empty .md files under NOTES_DIR (relative paths, sorted).

    Same exclusion rules as notes-rag's sync (SKIP_DIRS + empty files), so
    the corpus view matches what the note panel can retrieve.
    """
    if not NOTES_DIR.is_dir():
        return []
    out = []
    for p in sorted(NOTES_DIR.rglob("*.md")):
        rel = p.relative_to(NOTES_DIR)
        if any(part in NOTES_SKIP_DIRS for part in rel.parts):
            continue
        try:
            if p.stat().st_size == 0:
                continue
        except OSError:
            continue
        out.append(str(rel))
    return out


# path -> (content sha, chunks); re-chunking only happens after a file edit
_chunk_cache: dict[str, tuple[str, list[dict]]] = {}

# Reading-only filter: the shared chunker keeps heading-only sections (e.g.
# a file's top "# 标题" line before the first "## 小节") — useful anchors for
# RAG, but a pointless "read this + make cards" step. Drop chunks whose body
# (heading lines stripped) is shorter than this.
MIN_READING_BODY = 10


def _reading_worthwhile(ch: dict) -> bool:
    body = re.sub(r"^#{1,6}\s+.*$", "", ch.get("text", ""), flags=re.M).strip()
    return len(body) >= MIN_READING_BODY


def _chunks_for(path: str) -> tuple[list[dict], str] | None:
    """(chunks, content-sha) for one corpus file; None when missing/unreadable."""
    text = _read_note_text(path)
    if text is None:
        return None
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    hit = _chunk_cache.get(path)
    if hit and hit[0] == sha:
        return hit[1], sha
    c = _chunker()
    chunks = [ch for ch in c.chunk_markdown(text, path, c.file_year(path))
              if _reading_worthwhile(ch)]
    _chunk_cache[path] = (sha, chunks)
    return chunks, sha


def _chunk_key(ch: dict) -> str:
    return f'{ch["line_start"]}:{">".join(ch["heading_path"])}'


def _migrate_entry(entry: dict, chunks: list[dict], sha: str) -> None:
    """Re-key stored chunk states after the file changed (see header comment).

    No-op when the stored file_sha matches. Mutates entry in place; the
    CALLER persists reading.json.
    """
    if entry.get("file_sha") == sha:
        return
    states: dict = entry.get("chunks") or {}
    new_keys = {_chunk_key(c) for c in chunks}
    by_hp: dict[str, list[str]] = {}
    for c in chunks:
        by_hp.setdefault(">".join(c["heading_path"]), []).append(_chunk_key(c))
    consumed: set[str] = set()
    migrated: dict = {}
    orphans = list(entry.get("orphans") or [])

    def _line_of(k: str) -> int:
        head = k.split(":", 1)[0]
        return int(head) if head.isdigit() else 0

    for key in sorted(states, key=_line_of):
        st = states[key]
        if key in new_keys and key not in consumed:
            migrated[key] = st
            consumed.add(key)
            continue
        hp = key.split(":", 1)[1] if ":" in key else ""
        cand = next(
            (k for k in by_hp.get(hp, []) if k not in consumed and k not in migrated),
            None,
        )
        if cand:
            migrated[cand] = st
            consumed.add(cand)
        else:
            orphans.append({"chunk_key": key, **st})
    entry["chunks"] = migrated
    entry["orphans"] = orphans
    entry["file_sha"] = sha


def _file_view(entry: dict):
    """(ordered, summary) for one reading-list entry.

    ordered = [(chunk, key, state_dict)] in file order, or None when the file
    vanished from the corpus. Migrates stored states on drift (caller persists).
    """
    loaded = _chunks_for(entry["path"])
    if loaded is None:
        return None, {
            "path": entry["path"],
            "title": Path(entry["path"]).stem,
            "missing": True,
            "total_chunks": 0,
            "todo": 0,
            "active": 0,
            "done": 0,
            "skipped": 0,
            "frontier": None,
            "orphans": len(entry.get("orphans") or []),
            "cards_created": 0,
        }
    chunks, sha = loaded
    _migrate_entry(entry, chunks, sha)
    states = entry.get("chunks") or {}
    ordered = [(ch, _chunk_key(ch), states.get(_chunk_key(ch)) or {}) for ch in chunks]
    return ordered, _file_summary(entry, ordered)


def _file_summary(entry: dict, ordered) -> dict:
    if ordered is None:
        return {
            "path": entry["path"],
            "title": Path(entry["path"]).stem,
            "missing": True,
            "total_chunks": 0,
            "todo": 0,
            "active": 0,
            "done": 0,
            "skipped": 0,
            "frontier": None,
            "orphans": len(entry.get("orphans") or []),
            "cards_created": 0,
        }
    counts = {"todo": 0, "active": 0, "done": 0, "skipped": 0}
    cards = 0
    frontier = None
    for ch, key, st in ordered:
        s = st.get("status", "todo")
        counts[s if s in counts else "todo"] += 1
        cards += len(st.get("cards_created") or [])
        if frontier is None and s not in ("done", "skipped"):
            frontier = {
                "chunk_key": key,
                "status": s,
                "title": ch.get("title") or Path(entry["path"]).stem,
                "line_start": ch["line_start"],
            }
    return {
        "path": entry["path"],
        "title": Path(entry["path"]).stem,
        "missing": False,
        "total_chunks": len(ordered),
        **counts,
        "frontier": frontier,
        "orphans": len(entry.get("orphans") or []),
        "cards_created": cards,
    }


def _chunk_payload(ch: dict, key: str, st: dict, summary: dict) -> dict:
    return {
        "path": ch["file"],
        "chunk_key": key,
        "title": ch.get("title") or Path(ch["file"]).stem,
        "heading_path": ch.get("heading_path") or [],
        "line_start": ch["line_start"],
        "line_end": ch["line_end"],
        "text": ch["text"],
        "status": st.get("status", "todo"),
        "cards_created": list(st.get("cards_created") or []),
        "file_chunks": summary.get("total_chunks", 0),
        "file_done": summary.get("done", 0),
        "file_skipped": summary.get("skipped", 0),
    }


def _reading_read() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    if not READING_FILE.exists():
        return {"list": [], "archive": {}}
    try:
        d = json.loads(READING_FILE.read_text())
        if not isinstance(d, dict):
            raise ValueError("bad shape")
        d.setdefault("list", [])
        d.setdefault("archive", {})
        rd = d.get("round")
        if isinstance(rd, dict) and _round_expired(rd):
            d["round"] = None  # half-finished reading round older than 24h
        return d
    except Exception:
        return {"list": [], "archive": {}}


def _reading_write(d: dict | None):
    STATE_DIR.mkdir(exist_ok=True)
    if d is None:
        READING_FILE.unlink(missing_ok=True)
    else:
        tmp = READING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False))
        tmp.replace(READING_FILE)


def _find_entry(data: dict, path: str) -> dict | None:
    return next((e for e in data.get("list", []) if e.get("path") == path), None)


def _deal_reading(data: dict, per_round: int) -> list[dict]:
    """Deal up to `per_round` CHUNKS (not files) — user decision 2026-09-19.

    Three phases, files always in 阅读清单 priority order:
      0. every `active` chunk resurfaces first (design rule: 正在制卡
         chunks come back every round until done/skipped — with multi-chunk
         rounds a deep active chunk could otherwise fall outside the window);
      1. breadth: each file's frontier (first not-done/skipped) chunk;
      2. depth: while the budget isn't filled (fewer files than per_round),
         the same files contribute their NEXT dealable chunks in file order,
         so a single-file reading list still gets a full round (quick=2 /
         focus=5) instead of exactly one chunk.
    Done/skipped chunks are never dealt.
    """
    payloads: list[dict] = []
    dealt: set[tuple[str, str]] = set()
    # views = [path, ordered, summary] per dealable file
    views: list[list] = []
    for entry in data.get("list", []):
        ordered, summary = _file_view(entry)  # may migrate entry (caller saves)
        if not ordered or not summary.get("frontier"):
            continue
        views.append([entry.get("path", ""), ordered, summary])

    def _dealable(st: dict) -> bool:
        return st.get("status", "todo") not in ("done", "skipped")

    def _take(path: str, ch: dict, key: str, st: dict, summary: dict) -> None:
        payloads.append(_chunk_payload(ch, key, st, summary))
        dealt.add((path, key))

    for path, ordered, summary in views:  # phase 0: active resurfaces first
        for ch, key, st in ordered:
            if len(payloads) >= per_round:
                break
            if st.get("status", "todo") == "active" and (path, key) not in dealt:
                _take(path, ch, key, st, summary)
    for path, ordered, summary in views:  # phase 1: frontier per file
        if len(payloads) >= per_round:
            break
        for ch, key, st in ordered:
            if not _dealable(st):
                continue
            # the frontier is dealt exactly once — if phase 0 already took
            # it (active), this file contributes nothing to the breadth pass
            if (path, key) not in dealt:
                _take(path, ch, key, st, summary)
            break
    for path, ordered, summary in views:  # phase 2: deepen, priority order
        for ch, key, st in ordered:
            if len(payloads) >= per_round:
                return payloads
            if (path, key) in dealt or not _dealable(st):
                continue
            _take(path, ch, key, st, summary)
    return payloads


def _reading_round_payload(data: dict) -> dict | None:
    """Wire shape of the stored round; None when there is none.

    An active round rehydrates its pending chunks from disk. Chunks that
    drifted away (file edited mid-round) or whose file left the list are
    dropped and counted as done — the round must never strand.
    """
    rd = data.get("round")
    if not isinstance(rd, dict):
        return None
    if rd.get("status") == "complete":
        return {
            "status": "complete",
            "done": rd.get("done", 0),
            "total": rd.get("total", 0),
            "stats": rd.get("stats", {}),
            "mode": study_mode(rd.get("mode")),
        }
    pending_chunks = []
    for item in rd.get("pending", []):
        entry = _find_entry(data, item.get("path", ""))
        if entry is None:
            continue
        ordered, summary = _file_view(entry)
        match = next(
            ((ch, key, st) for ch, key, st in (ordered or [])
             if key == item.get("chunk_key")),
            None,
        )
        if match is None:
            continue
        ch, key, st = match
        pending_chunks.append(_chunk_payload(ch, key, st, summary))
    dropped = len(rd.get("pending", [])) - len(pending_chunks)
    if dropped:
        rd["done"] = rd.get("done", 0) + dropped
        rd["pending"] = [
            {"path": p["path"], "chunk_key": p["chunk_key"]} for p in pending_chunks
        ]
        if not rd["pending"]:
            rd["status"] = "complete"
    if rd.get("status") == "complete":
        return {
            "status": "complete",
            "done": rd.get("done", 0),
            "total": rd.get("total", 0),
            "stats": rd.get("stats", {}),
            "mode": study_mode(rd.get("mode")),
        }
    return {
        "status": "active",
        "chunks": pending_chunks,
        "done": rd.get("done", 0),
        "total": rd.get("total", len(pending_chunks)),
        "stats": rd.get("stats", {}),
        "mode": study_mode(rd.get("mode")),
    }


def _reading_record_card(src: dict | None, note_id: int) -> None:
    """Append a just-created note id to the source chunk's cards_created.

    ALSO auto-marks a `todo` chunk as `active` (user decision 2026-09-19:
    the 开始制卡 button is gone — making a card/cloze from a chunk IS the
    declaration that you're working on it; the chip shows 正在制卡 and the
    chunk resurfaces first next round). done/skipped chunks are left alone.

    Fail-soft by contract (caller wraps in try): reading bookkeeping must
    never break card creation.
    """
    path = (src or {}).get("path")
    key = (src or {}).get("chunk_key")
    if not path or not key:
        return
    data = _reading_read()
    entry = _find_entry(data, path)
    if entry is None:
        return
    st = entry.setdefault("chunks", {}).setdefault(key, {})
    if st.get("status", "todo") == "todo":
        st["status"] = "active"
        st["updated_at"] = datetime.now().isoformat(timespec="seconds")
    created = st.setdefault("cards_created", [])
    if note_id not in created:
        created.append(note_id)
    _reading_write(data)


async def _reading_extras() -> dict:
    """Reading fields for /api/session/state (mirrors the preview extras).

    reading_round rides along ONLY while active (same policy as preview_round)
    so a page reload resumes the reading segment; the completed tombstone is
    reachable via GET /api/reading/state.
    """
    out: dict = {"reading_mode": READING_MODE}
    if not READING_MODE:
        return out
    try:
        data = _reading_read()
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry)
            summaries.append(summary)
        out["reading_list_size"] = len(data.get("list", []))
        out["reading_available"] = sum(1 for s in summaries if s.get("frontier"))
        out["reading_active"] = sum(s.get("active", 0) for s in summaries)
        rp = _reading_round_payload(data)
        if rp is not None and rp.get("status") == "active":
            out["reading_round"] = rp
        _reading_write(data)  # persist any drift migrations
    except Exception:
        pass  # fail-soft: session/state must never break on reading state
    return out


@app.get("/api/reading/status")
async def reading_status():
    """阅读清单 overview + the stored round (lock: _file_view may migrate)."""
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry)
            summaries.append(summary)
        rp = _reading_round_payload(data)
        _reading_write(data)
        return {
            "reading_mode": True,
            "list": summaries,
            "round": rp,
            "available": sum(1 for s in summaries if s.get("frontier")),
            "study_modes": STUDY_MODES,
        }


@app.get("/api/reading/corpus")
async def reading_corpus():
    """Every indexable .md in the corpus, flagged by reading-list membership."""
    _reading_guard()
    data = _reading_read()
    listed = {e["path"] for e in data.get("list", [])}
    return {
        "files": [
            {"path": p, "title": Path(p).stem, "in_list": p in listed}
            for p in _corpus_files()
        ]
    }


class ReadingPathBody(BaseModel):
    path: str


class ReadingReorderBody(BaseModel):
    order: list[str] | None = None  # full priority rewrite
    path: str | None = None         # …or move ONE file (top=True → to front,
    top: bool = False               #    else one position up)


@app.post("/api/reading/list/add")
async def reading_list_add(body: ReadingPathBody):
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        if _find_entry(data, body.path):
            raise HTTPException(status_code=409, detail="already in the reading list")
        loaded = _chunks_for(body.path)
        if loaded is None:
            raise HTTPException(status_code=404, detail="note file not found in the corpus")
        chunks, sha = loaded
        if not chunks:
            raise HTTPException(status_code=400, detail="文件里没有可读的片段")
        # re-adding a previously removed file restores its old progress
        archived = (data.get("archive") or {}).pop(body.path, None) or {}
        data["list"].append(
            {
                "path": body.path,
                "added_at": datetime.now().isoformat(timespec="seconds"),
                "file_sha": archived.get("file_sha", sha),
                "chunks": archived.get("chunks", {}),
                "orphans": archived.get("orphans", []),
            }
        )
        _reading_write(data)
        return {"ok": True, "order": [e["path"] for e in data["list"]]}


@app.post("/api/reading/list/remove")
async def reading_list_remove(body: ReadingPathBody):
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="not in the reading list")
        data["list"] = [e for e in data["list"] if e.get("path") != body.path]
        # park the progress so a later re-add doesn't start from scratch
        data.setdefault("archive", {})[body.path] = {
            "file_sha": entry.get("file_sha"),
            "chunks": entry.get("chunks", {}),
            "orphans": entry.get("orphans", []),
        }
        rd = data.get("round")
        if isinstance(rd, dict) and rd.get("status") == "active":
            before = len(rd.get("pending", []))
            rd["pending"] = [p for p in rd.get("pending", []) if p.get("path") != body.path]
            rd["done"] = rd.get("done", 0) + (before - len(rd["pending"]))
            if not rd["pending"]:
                rd["status"] = "complete"
        _reading_write(data)
        return {"ok": True, "order": [e["path"] for e in data["list"]]}


@app.post("/api/reading/list/reorder")
async def reading_list_reorder(body: ReadingReorderBody):
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        if body.order is not None:
            by_path = {e["path"]: e for e in data.get("list", [])}
            seen = set(body.order)
            data["list"] = [by_path[p] for p in body.order if p in by_path]
            # defensive: entries missing from `order` keep their relative
            # spot at the end instead of vanishing
            data["list"] += [e for e in by_path.values() if e["path"] not in seen]
        elif body.path:
            entry = _find_entry(data, body.path)
            if entry is None:
                raise HTTPException(status_code=404, detail="not in the reading list")
            rest = [e for e in data["list"] if e.get("path") != body.path]
            if body.top:
                data["list"] = [entry] + rest
            else:
                idx = next(
                    (i for i, e in enumerate(data["list"]) if e.get("path") == body.path),
                    0,
                )
                if idx > 0:
                    data["list"][idx - 1], data["list"][idx] = (
                        data["list"][idx],
                        data["list"][idx - 1],
                    )
        else:
            raise HTTPException(status_code=400, detail="provide order[] or path")
        _reading_write(data)
        return {"ok": True, "order": [e["path"] for e in data["list"]]}


@app.get("/api/reading/state")
async def reading_state():
    """Resume payload: the stored round (active OR complete tombstone) plus
    the list overview — the reading screens' single entry point on load."""
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry)
            summaries.append(summary)
        rp = _reading_round_payload(data)
        _reading_write(data)
        return {
            "reading_mode": True,
            "round": rp,
            "list": summaries,
            "available": sum(1 for s in summaries if s.get("frontier")),
        }


@app.post("/api/reading/start")
async def reading_start(mode: str | None = None):
    """Deal one reading segment: STUDY_MODES[mode]["read"] frontier chunks."""
    async with _state_lock:
        _reading_guard()
        return _reading_start_impl(study_mode(mode))


def _reading_start_impl(mode: str = DEFAULT_MODE):
    data = _reading_read()
    per_round = STUDY_MODES[mode].get("read", 0)
    payloads = _deal_reading(data, per_round) if per_round > 0 else []
    if not payloads:
        _reading_write(data)  # persist migrations even on an empty deal
        return {"chunks": [], "mode": mode, "empty": True, "study_modes": STUDY_MODES}
    data["round"] = {
        "status": "active",
        "created": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "total": len(payloads),
        "done": 0,
        "stats": {"done": 0, "skipped": 0, "next": 0},
        "pending": [
            {"path": p["path"], "chunk_key": p["chunk_key"]} for p in payloads
        ],
    }
    _reading_write(data)
    return {
        "chunks": payloads,
        "mode": mode,
        "empty": False,
        "study_modes": STUDY_MODES,
    }


@app.post("/api/reading/act")
async def reading_act(path: str, chunk_key: str, action: str):
    async with _state_lock:
        _reading_guard()
        return _reading_act_impl(path, chunk_key, action)


def _reading_act_impl(path: str, chunk_key: str, action: str):
    """Per-chunk decision inside a reading round.

    mark_active: todo → active (正在制卡). Does NOT advance the round — the
        user stays on the chunk making cards; it resurfaces next round until
        completed/skipped.
    complete: → done (制卡完成). Advances; unlocks the file's next chunk.
    skip:     → skipped (无需制卡). Advances; unlocks the next chunk too.
    next:     no status change (下一张, 稍后继续). Advances the round only —
        the chunk stays frontier and comes back next round.
    """
    if action not in READING_ACTIONS:
        raise HTTPException(
            status_code=400, detail="action must be mark_active|complete|skip|next"
        )
    data = _reading_read()
    rd = data.get("round")
    if not isinstance(rd, dict) or rd.get("status") != "active":
        raise HTTPException(status_code=409, detail="no active reading round")
    target = {"path": path, "chunk_key": chunk_key}
    if target not in rd.get("pending", []):
        return {"ok": False, "reason": "stale"}
    entry = _find_entry(data, path)
    if entry is None:
        raise HTTPException(status_code=404, detail="file not in the reading list")
    ordered, _ = _file_view(entry)
    keys = {key for _, key, _ in ordered or []}

    def _drop_and_maybe_complete() -> bool:
        rd["pending"] = [p for p in rd.get("pending", []) if p != target]
        rd["done"] = rd.get("done", 0) + 1
        if not rd["pending"]:
            rd["status"] = "complete"
            return True
        return False

    if chunk_key not in keys:
        # file drifted mid-round and this exact chunk is gone — don't strand
        # the round on it; the frontier logic re-picks on the next deal
        complete = _drop_and_maybe_complete()
        _reading_write(data)
        return {"ok": False, "reason": "drifted", "round_complete": complete}

    states = entry.setdefault("chunks", {})
    st = states.setdefault(chunk_key, {})
    if action == "mark_active":
        if st.get("status", "todo") == "todo":
            st["status"] = "active"
    elif action == "complete":
        st["status"] = "done"
    elif action == "skip":
        st["status"] = "skipped"
    st["updated_at"] = datetime.now().isoformat(timespec="seconds")

    round_complete = False
    if action in ("complete", "skip", "next"):
        stats_key = {"complete": "done", "skip": "skipped", "next": "next"}[action]
        stats = rd.setdefault("stats", {})
        stats[stats_key] = stats.get(stats_key, 0) + 1
        round_complete = _drop_and_maybe_complete()
    _reading_write(data)
    return {
        "ok": True,
        "status": st.get("status", "todo"),
        "round_complete": round_complete,
        "stats": rd.get("stats", {}),
        "done": rd.get("done", 0),
        "total": rd.get("total", 0),
    }


@app.post("/api/reading/finish")
async def reading_finish():
    """Mid-round exit: the round is cleared, chunk states are untouched
    (untouched chunks stay todo/active and are re-dealt next round)."""
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        stats = (data.get("round") or {}).get("stats", {})
        data["round"] = None
        _reading_write(data)
        return {"ok": True, "stats": stats}


@app.get("/api/reading/file")
async def reading_file(path: str):
    """Raw markdown for the reading right-hand panel (served directly from
    the corpus — independent of the notes-rag service being up)."""
    _reading_guard()
    text = _read_note_text(path)
    if text is None:
        raise HTTPException(status_code=404, detail="note file not found")
    return {"path": path, "text": text}


# ---- media upload + tag helpers --------------------------------------------

ALLOWED_MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp",
    ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".mp4", ".webm",
}
UPLOAD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB, generous for screenshots/photos


@app.post("/api/media/upload")
async def media_upload(file: UploadFile = File(...)):
    """Store an uploaded file in the Anki media collection via AnkiConnect.

    Returns the stored filename; the editor inserts it as <img src="name">
    (Anki media references are bare filenames, served back at /media/).
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 25 MB)")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_MEDIA_EXT:
        raise HTTPException(status_code=400, detail=f"unsupported type: {ext or '(none)'}")
    # timestamp + random suffix → never clobbers an existing media file
    stem = datetime.now().strftime("paste-%Y%m%d-%H%M%S")
    filename = f"{stem}-{uuid.uuid4().hex[:6]}{ext}"
    await anki(
        "storeMediaFile",
        {"filename": filename, "data": base64.b64encode(data).decode("ascii")},
        timeout=60,
    )
    return {"filename": filename}


@app.get("/api/tags")
async def list_tags():
    """All tags in the collection, for autocomplete in the edit dialog."""
    return {"tags": await anki("getTags") or []}


# ---- note panel (知识成体系 Phase 1: card → source note sections) ----------

@app.get("/api/note/sections")
async def note_sections(note_id: int, top_k: int = 3):
    """Top-k note sections for a card, via notes-rag (:8791).

    The frontend fetches this LAZILY per current card (not bundled into
    fetch_cards): a review round shouldn't wait on note retrieval, and only
    the card on screen needs its note. notes-rag embeds on cache miss, so
    the first lookup of a fresh card can take a few seconds.
    """
    sections = await fetch_note_sections(note_id, top_k)
    return {"sections": sections}


@app.get("/api/notes/raw")
async def notes_raw(path: str):
    """Raw markdown of one note file, relayed from notes-rag (traversal-safe
    there — it only ever reads under ~/anki-notes)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{NOTES_RAG}/file", params={"path": path})
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail="note file not found")
            if 400 <= r.status_code < 500:
                # traversal / bad path — propagate as 400, never serve content
                raise HTTPException(status_code=400, detail="invalid note path")
            r.raise_for_status()
            return r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"notes-rag unreachable: {e}")


@app.get("/media/{name:path}")
async def media(name: str):
    safe = os.path.basename(name)
    p = MEDIA_DIR / safe
    if not p.is_file():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(p)


# ---- serve the new SolidJS review frontend (review-web-v2/dist) -----------
# SPA fallback: all non-API paths serve index.html
if REVIEW_WEB_V2_DIST.is_dir():
    # mount static assets FIRST (before catch-all) — Starlette matches in registration order
    app.mount("/assets", StaticFiles(directory=REVIEW_WEB_V2_DIST / "assets"), name="review-web-v2-assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        """Serve static files from review-web-v2/dist, with SPA fallback to index.html."""
        file_path = REVIEW_WEB_V2_DIST / path
        if path and file_path.is_file() and file_path.resolve().is_relative_to(REVIEW_WEB_V2_DIST):
            return FileResponse(file_path)
        # SPA fallback
        index = REVIEW_WEB_V2_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="not found")



