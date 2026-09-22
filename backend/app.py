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
    reads their own markdown notes (~/anki-notes, chunked by the vendored
    chunker.py) and writes cards by hand. Per chunk: todo → active(正在制卡)
    → done(制卡完成), or skipped(无需制卡). Within a file only the frontier
    chunk is dealt, so later chunks stay locked until the frontier is
    finished; an `active` chunk resurfaces first every round. Files are
    opt-in via the 阅读清单 (manual priority). State: state/reading.db
    (SQLite; migrated automatically from the legacy reading.json).

    Reading gate (user spec 2026-09-19 round 3): an `active` chunk whose
    created cards have NOT all left the preview pool yet (still in 预览池
    or still suspended = not truly released) is HELD — it is not dealt and
    blocks its file's frontier until every one of its cards is thawed
    (out of the pool AND unsuspended, i.e. the next-day release happened).
    Chunks without cards are never gated. AnkiConnect errors fail OPEN
    (reading must never be blocked by a dead backend).
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
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
# (decision #4). Chunks come from the vendored backend/chunker.py over the
# notes corpus (ANKI_NOTES_DIR).
#
# Gated on ANKI_READING_MODE like preview: off ⇒ endpoints 404, the wire
# reports reading_mode=false and the frontend renders the legacy UI.
READING_MODE = os.getenv("ANKI_READING_MODE", "").lower() in ("1", "true", "yes", "on")
NOTES_DIR = Path(os.getenv("ANKI_NOTES_DIR", str(Path.home() / "anki-notes")))
READING_FILE = STATE_DIR / "reading.json"  # legacy; migration source only
READING_DB_FILE = Path(os.getenv("ANKI_READING_DB", str(STATE_DIR / "reading.db")))
# corpus scan exclusions (editor/tool dirs + the note-image store, never
# note content). _assets holds reading-mode image uploads (P5, 2026-09-23)
# and is served by basename at /api/reading/media/<name>.
NOTES_SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", "_assets"}
READING_ACTIONS = ("mark_active", "complete", "skip", "next", "promote", "demote")


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
    """Strip HTML to plain text (used by the empty-field card guard).

    Skips <style>/<script> content — rendered Anki questions embed the
    card CSS in a <style> block, which must not pollute the extracted text.
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


# anki-rag (similar cards, :8789) and anki-explain (AI explanations, :8788)
# were RETIRED 2026-09-21 (user decision: RAG 匹配准确度达不到要求, explain
# 没用). The left column's 相关卡片 list is EXACT segment provenance
# (/api/reading/source cards_created), not similarity search.


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
    # NOTE: similar-cards (anki-rag) + AI explanation (anki-explain)
    # enrichment removed 2026-09-21 (both projects retired at user request —
    # 相关卡片 moved to the left column as EXACT segment provenance;
    # priorKnowledge enrichment was already removed 2026-09-17).
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
# Chunking uses the vendored chunker.py over the notes corpus (ANKI_NOTES_DIR).
#
# Segment identity (user spec 2026-09-20 round 4): DB-issued seg_id per file
# (PK path+seg_id, per-file counter, never reused). The chunker is now a
# SEEDING tool only — identity no longer derives from line numbers or
# heading paths, so edits never drift the identity. Structure (ranges,
# status, lineage) lives in the segments table; .md files stay pure text
# (no in-file markers). The app is the ONLY writer: in-app edits update
# file + segments in one transaction. External drift (file_sha mismatch)
# trips the re-anchoring fuse: segments relocate by exact fingerprint
# (first-line index + window sha, bounded full-scan fallback); failures
# park in rorphans (card-holding ones demote to container so provenance
# survives) and the file is flagged needs_resync — out of dealing until
# POST /api/reading/reseed confirms.
#
# All mutating endpoints run under _state_lock (same serialization as the
# preview round); reads that may persist migrations take it too.

def _reading_guard():
    """Reading endpoints only exist when the feature flag is on."""
    if not READING_MODE:
        raise HTTPException(status_code=404, detail="reading mode disabled")


_chunker_mod = None


def _chunker():
    """The vendored chunker module (backend/chunker.py, same dir as app.py)."""
    global _chunker_mod
    if _chunker_mod is None:
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

    Excludes NOTES_SKIP_DIRS + empty files, so the corpus view matches what
    the reading list can queue.
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


def _seed_segments(chunks: list[dict], lines: list[str], start_id: int = 1) -> list[dict]:
    """Fresh segments (status=todo) for chunker output — the ONLY place the
    chunker touches identity: it seeds seg rows, then never decides anything
    again (round 4). seg_ids are per-file, issued from `start_id`."""
    now = datetime.now().isoformat(timespec="seconds")
    segs = []
    for i, ch in enumerate(chunks):
        st, en = ch["line_start"], ch["line_end"]
        segs.append({
            "seg_id": start_id + i,
            "start_line": st,
            "end_line": en,
            "fingerprint": _seg_fingerprint(lines, st, en),
            "first_line": lines[st - 1] if 0 <= st - 1 < len(lines) else "",
            "title": ch.get("title") or "",
            "heading_path": ch.get("heading_path") or [],
            "status": "todo",
            "parent_seg_id": None,
            "updated_at": now,
        })
    return segs


def _segments_from_legacy(path: str, states: dict, orphans: list) -> tuple[list[dict], str | None]:
    """Seed segments for one file, carrying legacy round-3 states over.

    `states`: {chunk_key: {status, updated_at, cards_created?}} — the old
    rchunks+rcards (or reading.json chunks) shape. Legacy keys match seeded
    segments by chunker-derived key equality (exact only; a file that
    drifted since round 3 orphans rather than mis-binds). Unmatched states
    park in `orphans` (mutated in place). Returns (segments, sha);
    ([], None) when the file left the corpus.
    """
    loaded = _chunks_for(path)
    text = _read_note_text(path)
    if loaded is None or text is None:
        for key, st in states.items():
            orphans.append({"chunk_key": key, **(st or {})})
        return [], None
    chunks, sha = loaded
    segs = _seed_segments(chunks, text.split("\n"))
    by_key = {_chunk_key(ch): seg for seg, ch in zip(segs, chunks)}
    for key, st in states.items():
        st = st or {}
        seg = by_key.get(key)
        if seg is None:
            orphans.append({"chunk_key": key, **st})
            continue
        if st.get("status"):
            seg["status"] = st["status"]
        if st.get("updated_at"):
            seg["updated_at"] = st["updated_at"]
        if st.get("cards_created"):
            seg["cards_created"] = list(st["cards_created"])
    return segs, sha


def _next_seg_id(entry: dict) -> int:
    """Next per-file segment id (issued, never reused — ids of deleted or
    containerized segments stay retired so provenance lookups can't collide)."""
    ids = [s.get("seg_id") or 0 for s in entry.get("segments") or []]
    for o in entry.get("orphans") or []:
        sid = (o or {}).get("seg_id")
        if isinstance(sid, int):
            ids.append(sid)
    return (max(ids) + 1) if ids else 1


def _seg_fingerprint(lines: list[str], start: int, end: int) -> str:
    """sha of the [start, end] 1-based inclusive line window (re-anchor fuse)."""
    if not lines or start < 1 or end < start:
        return ""
    window = "\n".join(lines[start - 1 : min(end, len(lines))])
    return hashlib.sha256(window.encode("utf-8")).hexdigest()[:32]


def _relocate_span(
    lines: list[str], fp: str, first_line: str, span: int, hint_start: int
) -> int | None:
    """New start_line for a segment whose window sha is `fp`; None when lost.

    Exact-fingerprint search only (no fuzzy matching — an edited segment is
    an identity crisis and must surface, not silently re-bind): hint position
    first, then occurrences of the stored first line, then a bounded full
    scan. O(lines × span) per lost-position segment — fine at corpus scale.
    """
    n = len(lines)
    if not fp or span < 1 or n < span:
        return None
    tried: set[int] = set()

    def _hit(st: int) -> bool:
        return st + span - 1 <= n and _seg_fingerprint(lines, st, st + span - 1) == fp

    if 1 <= hint_start <= n - span + 1:
        tried.add(hint_start)
        if _hit(hint_start):
            return hint_start
    if first_line:
        for i, l in enumerate(lines[:5000]):
            if l == first_line and (i + 1) not in tried:
                tried.add(i + 1)
                if _hit(i + 1):
                    return i + 1
    for st in range(1, n - span + 2):
        if st in tried:
            continue
        if _hit(st):
            return st
    return None


def _reanchor_entry(entry: dict, lines: list[str], new_sha: str) -> None:
    """Re-locate stored segments after an EXTERNAL file edit (fuse path —
    the app itself edits file+segments in one transaction and never drifts).

    Pure line shifts re-anchor cleanly (fingerprint intact). A segment whose
    content changed can't match anywhere: with cards_created it DEMOTES to a
    clamped container (provenance survives — the card's source range still
    renders, marked stale); without cards it parks in orphans. Any loss flags
    the file needs_resync: out of dealing until POST /api/reading/reseed.
    Containers that lost their own fingerprint recompute from relocated
    children (their range is the union of the children's by construction).
    """
    n = len(lines)
    segs = entry.get("segments") or []
    orphans = list(entry.get("orphans") or [])
    damaged = False

    for s in segs:  # pass 1: leaves
        if s.get("status") == "container":
            continue
        span = s["end_line"] - s["start_line"] + 1
        st = _relocate_span(
            lines, s.get("fingerprint", ""), s.get("first_line", ""), span, s["start_line"]
        )
        if st is None:
            s["_lost"] = True
            damaged = True
        else:
            s["start_line"] = st
            s["end_line"] = st + span - 1
            s["first_line"] = lines[st - 1] if st - 1 < n else ""

    for s in segs:  # pass 2: containers — own fp, else union of live children
        if s.get("status") != "container":
            continue
        span = s["end_line"] - s["start_line"] + 1
        st = _relocate_span(
            lines, s.get("fingerprint", ""), s.get("first_line", ""), span, s["start_line"]
        )
        if st is not None:
            s["start_line"] = st
            s["end_line"] = st + span - 1
            s["first_line"] = lines[st - 1] if st - 1 < n else ""
            continue
        kids = [
            c for c in segs
            if c.get("parent_seg_id") == s["seg_id"] and not c.get("_lost")
        ]
        if kids:
            s["start_line"] = min(k["start_line"] for k in kids)
            s["end_line"] = max(k["end_line"] for k in kids)
            s["fingerprint"] = _seg_fingerprint(lines, s["start_line"], s["end_line"])
            s["first_line"] = lines[s["start_line"] - 1] if n else ""
        else:
            s["_lost"] = True
            damaged = True

    kept: list[dict] = []
    for s in segs:
        if not s.pop("_lost", False):
            kept.append(s)
            continue
        if s.get("cards_created"):
            # provenance container: clamp the stale range into the new file
            s["status"] = "container"
            s["start_line"] = max(1, min(s["start_line"], n or 1))
            s["end_line"] = max(s["start_line"], min(s["end_line"], n or 1))
            s["fingerprint"] = _seg_fingerprint(lines, s["start_line"], s["end_line"])
            s["first_line"] = lines[s["start_line"] - 1] if n else ""
            s["stale"] = True
            kept.append(s)
        else:
            payload = {k: v for k, v in s.items()}
            # keep seg_id INSIDE the payload — _next_seg_id scans orphans so a
            # parked id is never reissued (provenance collision guard)
            orphans.append({"chunk_key": f"seg:{payload.get('seg_id')}", **payload})
    entry["segments"] = kept
    entry["orphans"] = orphans
    entry["file_sha"] = new_sha
    if damaged:
        entry["needs_resync"] = True


def _file_view(entry: dict, gated: set | None = None):
    """(ordered, summary) for one reading-list entry.

    ordered = [(chunk_view, key, seg_dict)] for LEAF segments in file order
    (containers excluded — they are history/anchors, never dealt), or None
    when the file vanished from the corpus. key = str(seg_id): the wire's
    chunk_key field is now an opaque segment id (round 4). Re-anchors
    segments on external drift; caller persists.

    `gated` = set of (path, key) pairs whose created cards have not all
    left the preview pool yet (user spec 2026-09-19 round 3, B·二段重推):
    a gated segment is NOT the frontier — it blocks its file (later
    segments stay locked behind it) and is excluded from dealing until its
    cards are truly released (out of 预览池 AND unsuspended).
    """
    gated = gated or set()
    path = entry.get("path", "")
    text = _read_note_text(path)
    if text is None:
        return None, _file_summary(entry, None, gated)
    lines = text.split("\n")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if entry.get("file_sha") != sha:
        _reanchor_entry(entry, lines, sha)
    if entry.get("needs_resync"):
        return [], _file_summary(entry, [], gated)
    ordered = []
    segs = sorted(
        entry.get("segments") or [],
        key=lambda s: (s.get("start_line", 0), s.get("seg_id", 0)),
    )
    for s in segs:
        if s.get("status") == "container":
            continue
        start = max(1, min(s.get("start_line", 1), len(lines)))
        end = max(start, min(s.get("end_line", start), len(lines)))
        ch = {
            "file": path,
            "title": s.get("title") or Path(path).stem,
            "heading_path": s.get("heading_path") or [],
            "line_start": start,
            "line_end": end,
            "text": "\n".join(lines[start - 1 : end]),
        }
        ordered.append((ch, str(s["seg_id"]), s))
    return ordered, _file_summary(entry, ordered, gated)


def _file_summary(entry: dict, ordered, gated: set | None = None) -> dict:
    gated = gated or set()
    path = entry.get("path", "")
    base = {
        "path": path,
        "title": Path(path).stem,
        "orphans": len(entry.get("orphans") or []),
        "needs_resync": bool(entry.get("needs_resync")),
    }
    if ordered is None:
        return {
            **base,
            "missing": True,
            "total_chunks": 0,
            "todo": 0,
            "active": 0,
            "done": 0,
            "skipped": 0,
            "background": 0,
            "frontier": None,
            "gated": 0,
            "gated_frontier": None,
            "cards_created": 0,
        }
    counts = {"todo": 0, "active": 0, "done": 0, "skipped": 0, "background": 0}
    cards = 0
    frontier = None
    gated_count = 0
    gated_frontier = None
    blocked = base["needs_resync"]  # a flagged file deals nothing
    for ch, key, st in ordered:
        s = st.get("status", "todo")
        counts[s if s in counts else "todo"] += 1
        cards += len(st.get("cards_created") or [])
        # background (un-scheduled context) and done/skipped never deal and
        # never block the frontier — the frontier is the first DEALABLE one
        if s in ("done", "skipped", "background"):
            continue
        is_gated = (path, key) in gated
        if is_gated:
            gated_count += 1
        if frontier is not None or blocked:
            continue  # frontier already found / file already held
        if is_gated:
            blocked = True
            gated_frontier = {
                "chunk_key": key,
                "status": s,
                "title": ch.get("title") or Path(path).stem,
                "line_start": ch["line_start"],
            }
            continue  # held: later chunks stay locked behind it
        frontier = {
            "chunk_key": key,
            "status": s,
            "title": ch.get("title") or Path(path).stem,
            "line_start": ch["line_start"],
        }
    return {
        **base,
        "missing": False,
        "total_chunks": len(ordered),
        **counts,
        "frontier": frontier,
        "gated": gated_count,
        "gated_frontier": gated_frontier,
        "cards_created": cards,
    }


def _chunk_payload(ch: dict, key: str, st: dict, summary: dict,
                   segs_by_id: dict | None = None) -> dict:
    # ancestor cards (三栏左栏「相关卡片」, 2026-09-21): a split parent
    # KEEPS the cards made against its pre-cut range (provenance rule), so a
    # child segment's "same-content cards" = its own + every ancestor's
    # cards_created. Surfacing them in the reading UI is what stops
    # duplicate card-making after a cut (the job anki-rag used to do badly).
    ancestor_cards: list[int] = []
    if segs_by_id:
        pid = st.get("parent_seg_id")
        seen: set = set()
        while pid is not None and pid not in seen:
            seen.add(pid)
            p = segs_by_id.get(pid)
            if p is None:
                break
            for nid in p.get("cards_created") or []:
                if nid not in ancestor_cards:
                    ancestor_cards.append(nid)
            pid = p.get("parent_seg_id")
    return {
        "path": ch["file"],
        "chunk_key": key,
        "seg_id": int(key) if str(key).isdigit() else None,
        "parent_seg_id": st.get("parent_seg_id"),
        "title": ch.get("title") or Path(ch["file"]).stem,
        "heading_path": ch.get("heading_path") or [],
        "line_start": ch["line_start"],
        "line_end": ch["line_end"],
        "text": ch["text"],
        "status": st.get("status", "todo"),
        "cards_created": list(st.get("cards_created") or []),
        "ancestor_cards": ancestor_cards,
        "file_chunks": summary.get("total_chunks", 0),
        "file_done": summary.get("done", 0),
        "file_skipped": summary.get("skipped", 0),
    }


# ---- reading state store (SQLite, user spec 2026-09-19 round 3) ------------
# The user asked to move reading state off a single reading.json blob (dozens
# of files × dozens of chunks would rewrite the whole document on every
# action). Tables:
#   rfiles  — one row per reading-list file AND per archived file
#             (in_list=1 listed / 0 archived; pos = priority order)
#   rchunks — per-chunk state (status, updated_at)
#   rcards  — provenance: note ids created from a chunk (ordered by rowid)
#   rorphans— drift-parked chunk states (JSON payload per orphan)
#   rround  — single row (id=1): the active/complete round as JSON (the round
#             is a small ephemeral document — keeping it as one JSON row
#             avoids a 6th table for pending items)
# All access goes through _reading_read/_reading_write which assemble/
# disassemble the SAME dict shape the old JSON file had, so the dealing and
# summary logic is untouched. Every call is a fresh short-lived connection
# (stdlib sqlite3 is thread-safe this way; the app serializes mutations with
# _state_lock anyway). WAL mode keeps readers (status endpoints) snappy.
# First run migrates reading.json → tables once (renamed reading.json.bak
# afterwards, so it can never migrate twice).

def _reading_conn() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(READING_DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _reading_ensure_schema(conn)
    return conn


def _reading_ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rfiles (
            path      TEXT PRIMARY KEY,
            in_list   INTEGER NOT NULL DEFAULT 1,
            pos       INTEGER NOT NULL DEFAULT 0,
            added_at  TEXT,
            file_sha  TEXT
        );
        CREATE TABLE IF NOT EXISTS rcards (
            path      TEXT NOT NULL,
            seg_id    INTEGER NOT NULL,
            note_id   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rorphans (
            path      TEXT NOT NULL,
            chunk_key TEXT NOT NULL,
            data      TEXT
        );
        CREATE TABLE IF NOT EXISTS rround (
            id   INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT
        );
        CREATE TABLE IF NOT EXISTS rmeta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS segments (
            path          TEXT NOT NULL,
            seg_id        INTEGER NOT NULL,
            start_line    INTEGER NOT NULL,
            end_line      INTEGER NOT NULL,
            fingerprint   TEXT NOT NULL DEFAULT '',
            first_line    TEXT NOT NULL DEFAULT '',
            title         TEXT NOT NULL DEFAULT '',
            heading_path  TEXT NOT NULL DEFAULT '[]',
            status        TEXT NOT NULL DEFAULT 'todo',
            parent_seg_id INTEGER,
            updated_at    TEXT,
            PRIMARY KEY (path, seg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_segments_parent ON segments(path, parent_seg_id);
        """
    )
    # rcards indexes only once the table is round-4 shape: a legacy DB still
    # has rcards(path, chunk_key, note_id) until _reading_migrate_round4
    # renames it — creating seg_id indexes on it would raise and brick every
    # reading call BEFORE the migration gets a chance to run.
    rcards_cols = {r[1] for r in conn.execute("PRAGMA table_info(rcards)")}
    if "seg_id" in rcards_cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rcards_seg ON rcards(path, seg_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rcards_note ON rcards(note_id)"
        )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rfiles)")}
    if "needs_resync" not in cols:
        conn.execute(
            "ALTER TABLE rfiles ADD COLUMN needs_resync INTEGER NOT NULL DEFAULT 0"
        )


def _store_entry(
    conn: sqlite3.Connection,
    path: str,
    in_list: int,
    pos: int,
    added_at: str | None,
    file_sha: str | None,
    segments: list,
    orphans: list,
    needs_resync: bool = False,
) -> None:
    """Upsert one file entry (listed or archived) with its segments/cards/
    orphans. Caller runs inside a transaction (`with conn:`)."""
    conn.execute(
        "INSERT INTO rfiles(path, in_list, pos, added_at, file_sha, needs_resync) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET in_list=excluded.in_list, "
        "pos=excluded.pos, added_at=excluded.added_at, "
        "file_sha=excluded.file_sha, needs_resync=excluded.needs_resync",
        (path, in_list, pos, added_at, file_sha, 1 if needs_resync else 0),
    )
    conn.execute("DELETE FROM segments WHERE path = ?", (path,))
    conn.execute("DELETE FROM rcards WHERE path = ?", (path,))
    conn.execute("DELETE FROM rorphans WHERE path = ?", (path,))
    seg_rows = []
    card_rows = []
    for s in segments or []:
        s = dict(s or {})
        seg_rows.append((
            path,
            s.get("seg_id"),
            s.get("start_line", 1),
            s.get("end_line", s.get("start_line", 1)),
            s.get("fingerprint", ""),
            s.get("first_line", ""),
            s.get("title", ""),
            json.dumps(s.get("heading_path") or [], ensure_ascii=False),
            s.get("status", "todo"),
            s.get("parent_seg_id"),
            s.get("updated_at"),
        ))
        for nid in s.get("cards_created") or []:
            card_rows.append((path, s.get("seg_id"), nid))
    if seg_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO segments(path, seg_id, start_line, end_line, "
            "fingerprint, first_line, title, heading_path, status, parent_seg_id, "
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            seg_rows,
        )
    if card_rows:
        conn.executemany(
            "INSERT INTO rcards(path, seg_id, note_id) VALUES(?,?,?)",
            card_rows,
        )
    orphan_rows = []
    for o in orphans or []:
        o = dict(o or {})
        key = o.pop("chunk_key", "")
        orphan_rows.append((path, key, json.dumps(o, ensure_ascii=False)))
    if orphan_rows:
        conn.executemany(
            "INSERT INTO rorphans(path, chunk_key, data) VALUES(?,?,?)",
            orphan_rows,
        )


def _reading_migrate_json() -> None:
    """One-time migration of the legacy reading.json into the SQLite store.

    Runs only when the DB is brand new (no rmeta 'migrated' marker) AND a
    readable reading.json exists. The JSON is renamed to reading.json.bak
    afterwards so the legacy file never shadows the DB again.
    """
    try:
        conn = _reading_conn()
    except Exception:
        return
    try:
        with conn:
            row = conn.execute(
                "SELECT value FROM rmeta WHERE key = 'migrated'"
            ).fetchone()
            if row is not None:
                return
            conn.execute(
                "INSERT OR REPLACE INTO rmeta(key, value) VALUES('migrated', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )
        if not READING_FILE.exists():
            return
        try:
            d = json.loads(READING_FILE.read_text())
        except Exception:
            return
        if not isinstance(d, dict):
            return
        with conn:
            for i, e in enumerate(d.get("list") or []):
                p = e.get("path", "")
                orphans = list(e.get("orphans") or [])
                segs, sha = _segments_from_legacy(
                    p, e.get("chunks") or {}, orphans
                )
                _store_entry(
                    conn, p, 1, i,
                    e.get("added_at"), sha or e.get("file_sha"),
                    segs, orphans,
                )
            for i, (p, a) in enumerate(sorted((d.get("archive") or {}).items())):
                a = a or {}
                orphans = list(a.get("orphans") or [])
                segs, sha = _segments_from_legacy(p, a.get("chunks") or {}, orphans)
                _store_entry(
                    conn, p, 0, i, None, sha or a.get("file_sha"),
                    segs, orphans,
                )
            rd = d.get("round")
            if isinstance(rd, dict):
                conn.execute(
                    "INSERT INTO rround(id, data) VALUES(1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                    (json.dumps(rd, ensure_ascii=False),),
                )
    finally:
        conn.close()
    try:
        READING_FILE.replace(READING_FILE.with_suffix(".json.bak"))
    except OSError:
        pass


def _reading_migrate_round4() -> None:
    """One-time rchunks(+rcards) → segments migration (round 4).

    Runs only when the DB predates round 4 (no rmeta 'round4_migrated') and
    an rchunks table exists. Order matters: legacy rows are READ first, then
    rchunks/rcards are RENAMED to *_r3bak (zero-delete rollback: revert the
    app and rename them back), the schema is re-ensured (fresh seg_id-shaped
    rcards), and segments are seeded with legacy states re-bound by exact
    chunker-key equality; unmatched states park in rorphans.
    """
    try:
        conn = _reading_conn()
    except Exception:
        return
    try:
        with conn:
            row = conn.execute(
                "SELECT value FROM rmeta WHERE key = 'round4_migrated'"
            ).fetchone()
            if row is not None:
                return
            has_rchunks = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rchunks'"
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO rmeta(key, value) VALUES('round4_migrated', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )
            if not has_rchunks:
                return  # fresh DB — nothing to migrate
            file_rows = conn.execute(
                "SELECT path, in_list, pos, added_at, file_sha FROM rfiles"
            ).fetchall()
            chunk_rows = conn.execute(
                "SELECT path, chunk_key, status, updated_at FROM rchunks"
            ).fetchall()
            card_rows = conn.execute(
                "SELECT path, chunk_key, note_id FROM rcards ORDER BY rowid"
            ).fetchall()
            orphan_rows = conn.execute(
                "SELECT path, chunk_key, data FROM rorphans ORDER BY rowid"
            ).fetchall()
        # assemble legacy per-file states (the old _reading_read shape)
        states_by_path: dict[str, dict] = {}
        for path, key, status, updated in chunk_rows:
            st: dict = {}
            if status is not None:
                st["status"] = status
            if updated is not None:
                st["updated_at"] = updated
            states_by_path.setdefault(path, {})[key] = st
        for path, key, note_id in card_rows:
            st_c = states_by_path.get(path, {}).get(key)
            if st_c is not None:
                st_c.setdefault("cards_created", []).append(note_id)
        orphans_by_path: dict[str, list] = {}
        for path, key, data in orphan_rows:
            try:
                payload = json.loads(data) if data else {}
            except Exception:
                payload = {}
            orphans_by_path.setdefault(path, []).append({"chunk_key": key, **payload})
        # park the legacy tables (zero-delete rollback), then recreate the
        # round-4 rcards shape before any _store_entry insert touches it
        conn.execute("ALTER TABLE rchunks RENAME TO rchunks_r3bak")
        conn.execute("ALTER TABLE rcards RENAME TO rcards_r3bak")
        _reading_ensure_schema(conn)
        with conn:
            for path, in_list, pos, added_at, file_sha in file_rows:
                orphans = orphans_by_path.get(path, [])
                segs, sha = _segments_from_legacy(
                    path, states_by_path.get(path, {}), orphans
                )
                _store_entry(
                    conn, path, in_list, pos, added_at,
                    sha if segs else file_sha,
                    segs, orphans,
                )
    except Exception:
        pass  # never block startup on migration; the flag prevents retries
    finally:
        conn.close()


if READING_MODE:
    _reading_migrate_json()
    _reading_migrate_round4()


def _reading_read() -> dict:
    """Assemble the working state dict from the SQLite store.

    Round 4 shape: entries carry `segments` (list of segment dicts incl.
    cards_created) instead of the legacy `chunks` dict. Consumers (dealing,
    summaries, round rehydration) work off _file_view which hides the shape.
    """
    conn = _reading_conn()
    try:
        files = conn.execute(
            "SELECT path, in_list, pos, added_at, file_sha, needs_resync FROM rfiles "
            "ORDER BY in_list DESC, pos, path"
        ).fetchall()
        seg_rows = conn.execute(
            "SELECT path, seg_id, start_line, end_line, fingerprint, first_line, "
            "title, heading_path, status, parent_seg_id, updated_at FROM segments "
            "ORDER BY path, start_line, seg_id"
        ).fetchall()
        card_rows = conn.execute(
            "SELECT path, seg_id, note_id FROM rcards ORDER BY rowid"
        ).fetchall()
        orphan_rows = conn.execute(
            "SELECT path, chunk_key, data FROM rorphans ORDER BY rowid"
        ).fetchall()
        rd_row = conn.execute("SELECT data FROM rround WHERE id = 1").fetchone()
    finally:
        conn.close()

    segs_by_path: dict[str, list] = {}
    seg_index: dict[tuple[str, int], dict] = {}
    for (path, seg_id, start_line, end_line, fp, first_line, title, hp_json,
         status, parent_seg_id, updated) in seg_rows:
        try:
            hp = json.loads(hp_json) if hp_json else []
        except Exception:
            hp = []
        s: dict = {
            "seg_id": seg_id,
            "start_line": start_line,
            "end_line": end_line,
            "fingerprint": fp or "",
            "first_line": first_line or "",
            "title": title or "",
            "heading_path": hp,
            "status": status or "todo",
            "parent_seg_id": parent_seg_id,
            "updated_at": updated,
        }
        segs_by_path.setdefault(path, []).append(s)
        seg_index[(path, seg_id)] = s
    for path, seg_id, note_id in card_rows:
        seg = seg_index.get((path, seg_id))
        if seg is not None:
            seg.setdefault("cards_created", []).append(note_id)
    orphans_by_path: dict[str, list] = {}
    for path, key, data in orphan_rows:
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            payload = {}
        orphans_by_path.setdefault(path, []).append({"chunk_key": key, **payload})

    def _entry(path: str, added_at: str | None, file_sha: str | None,
               needs_resync: int | None) -> dict:
        e = {
            "path": path,
            "file_sha": file_sha,
            "segments": segs_by_path.get(path, []),
            "orphans": orphans_by_path.get(path, []),
        }
        if needs_resync:
            e["needs_resync"] = True
        if added_at is not None:
            e["added_at"] = added_at
        return e

    listed = [
        _entry(path, added_at, file_sha, needs_resync)
        for path, in_list, _pos, added_at, file_sha, needs_resync in files
        if in_list
    ]
    archive = {
        path: {
            "file_sha": file_sha,
            "segments": segs_by_path.get(path, []),
            "orphans": orphans_by_path.get(path, []),
            **({"needs_resync": True} if needs_resync else {}),
        }
        for path, in_list, _pos, _added, file_sha, needs_resync in files
        if not in_list
    }
    d: dict = {"list": listed, "archive": archive}
    rd = None
    if rd_row and rd_row[0]:
        try:
            parsed = json.loads(rd_row[0])
            if isinstance(parsed, dict):
                rd = parsed
        except Exception:
            rd = None
    if isinstance(rd, dict) and _round_expired(rd):
        rd = None  # half-finished reading round older than 24h
    d["round"] = rd
    return d


def _reading_write(d: dict | None):
    """Persist the working state dict back to SQLite (one transaction).

    Row-level upserts keep the store structured (queryable, indexable, no
    single-blob rewrite); at reading-list scale (dozens of files, hundreds
    of segments) a full sync per action is sub-millisecond and runs under
    _state_lock, so dict → tables can never race itself.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = _reading_conn()
    try:
        with conn:
            if d is None:
                for t in ("rfiles", "segments", "rcards", "rorphans", "rround"):
                    conn.execute(f"DELETE FROM {t}")
                return
            listed = d.get("list") or []
            archive = d.get("archive") or {}
            keep = [e.get("path", "") for e in listed] + list(archive.keys())
            marks = ",".join("?" * len(keep)) if keep else "''"
            for t in ("rfiles", "segments", "rcards", "rorphans"):
                conn.execute(
                    f"DELETE FROM {t} WHERE path NOT IN ({marks})", keep
                )
            for i, e in enumerate(listed):
                _store_entry(
                    conn, e.get("path", ""), 1, i,
                    e.get("added_at"), e.get("file_sha"),
                    e.get("segments") or [], e.get("orphans") or [],
                    bool(e.get("needs_resync")),
                )
            for i, (p, a) in enumerate(sorted(archive.items())):
                _store_entry(
                    conn, p, 0, i, None, (a or {}).get("file_sha"),
                    (a or {}).get("segments") or [], (a or {}).get("orphans") or [],
                    bool((a or {}).get("needs_resync")),
                )
            rd = d.get("round")
            if isinstance(rd, dict):
                conn.execute(
                    "INSERT INTO rround(id, data) VALUES(1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                    (json.dumps(rd, ensure_ascii=False),),
                )
            else:
                conn.execute("DELETE FROM rround")
    finally:
        conn.close()


def _find_entry(data: dict, path: str) -> dict | None:
    return next((e for e in data.get("list", []) if e.get("path") == path), None)


async def _reading_gates(data: dict) -> set[tuple[str, str]]:
    """(path, chunk_key) pairs currently HELD by the preview-pool gate.

    B·二段重推 (user spec 2026-09-19 round 3): a chunk whose created cards
    have not ALL left the preview pipeline is not re-dealt, and it blocks
    its file's frontier until they have. 「过了预览池」= truly thawed:
    NOT in 预览池 AND NOT suspended — approval alone (which parks the card
    in 2026 suspended with a released-YYYYMMDD stamp) does not lift the
    gate; the next-day unsuspend does. A card sent BACK to the pool
    (double-Again auto-return / manual 移回预览池) closes the gate again.

    Only each file's frontier chunk is checked (a deeper chunk can't be
    dealt while the frontier stands, so gating it would be dead work).
    Notes that no longer exist (user deleted the card) never hold —
    a chunk must not be gated forever by a deleted note. AnkiConnect
    errors fail OPEN (empty set): reading is never blocked by a dead
    backend. Preview mode off → no pool → nothing can be held.
    """
    if not PREVIEW_MODE:
        return set()
    targets: list[tuple[str, str, list[int]]] = []
    for entry in data.get("list", []):
        path = entry.get("path", "")
        ordered, _ = _file_view(entry)
        if not ordered:
            continue
        for _ch, key, st in ordered:
            st = st or {}
            if st.get("status", "todo") in ("done", "skipped", "background", "container"):
                continue
            notes = st.get("cards_created") or []
            if notes:
                # EVERY live segment with cards is a gate candidate, not just
                # the frontier: with depth dealing a deeper segment can hold
                # cards while an earlier todo one is the frontier (user made
                # cards for segment 3, pressed 下一张 on segment 2).
                targets.append((path, key, list(notes)))
    note_ids = sorted({n for _, _, notes in targets for n in notes})
    if not note_ids:
        return set()
    try:
        infos = await anki("notesInfo", {"notes": note_ids})
        by_note: dict[int, list[int]] = {}
        card_ids: list[int] = []
        for n in infos or []:
            nid = n.get("noteId")
            if not nid:
                continue  # AnkiConnect returns [{}] for deleted notes
            cids = n.get("cards") or []
            by_note[nid] = cids
            card_ids += cids
        if not card_ids:
            return set()
        cinfos = [
            i for i in await anki("cardsInfo", {"cards": card_ids}) or []
            if i.get("cardId")
        ]
    except Exception:
        return set()  # fail OPEN — never strand reading on a backend error
    released: dict[int, bool] = {
        i["cardId"]: PREVIEW_DECK not in i.get("deckName", "")
        and i.get("queue") != -1
        for i in cinfos
    }
    gated: set[tuple[str, str]] = set()
    for path, key, notes in targets:
        held = False
        for nid in notes:
            cids = by_note.get(nid)
            if cids is None or not cids:
                continue  # deleted note / note without cards — never holds
            if not all(released.get(cid, True) for cid in cids):
                held = True
                break
        if held:
            gated.add((path, key))
    return gated


def _deal_reading(data: dict, per_round: int, gated: set | None = None) -> list[dict]:
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
    Done/skipped chunks are never dealt. GATED chunks (cards still inside
    the preview pipeline — B·二段重推, 2026-09-19 round 3) are never dealt
    either, and a gated frontier locks its whole file (the summary's
    frontier is already gate-aware, so blocked files drop out of `views`).
    """
    gated = gated or set()
    payloads: list[dict] = []
    dealt: set[tuple[str, str]] = set()
    # views = [path, ordered, summary, segs_by_id] per dealable file —
    # segs_by_id resolves parent_seg_id chains for ancestor_cards
    views: list[list] = []
    for entry in data.get("list", []):
        ordered, summary = _file_view(entry, gated)  # may migrate (caller saves)
        if not ordered or not summary.get("frontier"):
            continue
        segs_by_id = {s.get("seg_id"): s for s in entry.get("segments") or []}
        views.append([entry.get("path", ""), ordered, summary, segs_by_id])

    def _dealable(path: str, key: str, st: dict) -> bool:
        return st.get("status", "todo") not in (
            "done", "skipped", "background", "container"
        ) and (path, key) not in gated

    def _take(path: str, ch: dict, key: str, st: dict, summary: dict,
              segs_by_id: dict) -> None:
        payloads.append(_chunk_payload(ch, key, st, summary, segs_by_id))
        dealt.add((path, key))

    for path, ordered, summary, by_id in views:  # phase 0: active first
        for ch, key, st in ordered:
            if len(payloads) >= per_round:
                break
            if (
                st.get("status", "todo") == "active"
                and (path, key) not in dealt
                and (path, key) not in gated
            ):
                _take(path, ch, key, st, summary, by_id)
    for path, ordered, summary, by_id in views:  # phase 1: frontier per file
        if len(payloads) >= per_round:
            break
        for ch, key, st in ordered:
            if not _dealable(path, key, st):
                continue
            # the frontier is dealt exactly once — if phase 0 already took
            # it (active), this file contributes nothing to the breadth pass
            if (path, key) not in dealt:
                _take(path, ch, key, st, summary, by_id)
            break
    for path, ordered, summary, by_id in views:  # phase 2: deepen, priority order
        for ch, key, st in ordered:
            if len(payloads) >= per_round:
                return payloads
            if (path, key) in dealt or not _dealable(path, key, st):
                continue
            _take(path, ch, key, st, summary, by_id)
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
        segs_by_id = {s.get("seg_id"): s for s in entry.get("segments") or []}
        pending_chunks.append(_chunk_payload(ch, key, st, summary, segs_by_id))
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
    """Append a just-created note id to the source segment's cards_created.

    ALSO auto-marks a `todo` segment as `active` (user decision 2026-09-19:
    the 开始制卡 button is gone — making a card/cloze from a segment IS the
    declaration that you're working on it; the chip shows 正在制卡 and the
    segment resurfaces first next round). done/skipped/background segments
    are left alone; a `container` segment KEEPS the provenance (cards made
    against a pre-split range still anchor to it) but never flips to active.

    src shape: {path, chunk_key} where chunk_key = str(seg_id) on the round-4
    wire (the reading UI) or a legacy "line:heading" key (old callers) — the
    latter re-binds by exact seeded-key match when possible.

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
    segs = entry.setdefault("segments", [])
    target = None
    if str(key).isdigit():
        target = next(
            (s for s in segs if str(s.get("seg_id")) == str(key)), None
        )
    else:
        # legacy chunk_key from a pre-round-4 client — match by seeded key
        loaded = _chunks_for(path)
        if loaded is not None:
            chunks, _sha = loaded
            for ch in chunks:
                if _chunk_key(ch) == key:
                    st_line = ch["line_start"]
                    target = next(
                        (s for s in segs
                         if s.get("start_line") == st_line
                         and s.get("status") != "container"),
                        None,
                    )
                    break
    if target is None:
        return
    if target.get("status", "todo") in ("todo",):
        target["status"] = "active"
        target["updated_at"] = datetime.now().isoformat(timespec="seconds")
    created = target.setdefault("cards_created", [])
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
        gated = await _reading_gates(data)
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry, gated)
            summaries.append(summary)
        out["reading_list_size"] = len(data.get("list", []))
        out["reading_available"] = sum(1 for s in summaries if s.get("frontier"))
        # funnel signal: only NOT-held 正在制卡 chunks resurface next round
        # (a gated active chunk waits for its cards to clear the preview
        # pipeline — counting it here would pull the user into a reading
        # start screen that deals nothing)
        out["reading_active"] = sum(
            max(0, s.get("active", 0) - s.get("gated", 0)) for s in summaries
        )
        out["reading_gated"] = sum(s.get("gated", 0) for s in summaries)
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
        gated = await _reading_gates(data)
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry, gated)
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


# ---- file browser (文件 section, user spec 2026-09-19 round 3) --------------
# Read-only corpus browsing for the left-rail 文件 page. Deliberately NOT
# gated on READING_MODE: browsing notes works even with the reading feature
# flag off. Same corpus rules + traversal-safe reader as reading mode.

@app.get("/api/files/list")
async def files_list():
    return {
        "files": [{"path": p, "title": Path(p).stem} for p in _corpus_files()]
    }


@app.get("/api/files/raw")
async def files_raw(path: str):
    text = _read_note_text(path)
    if text is None:
        raise HTTPException(status_code=404, detail="note file not found")
    return {"path": path, "text": text}


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
        # re-adding a previously removed file restores its old progress;
        # otherwise seed fresh segments from the chunker (round 4: the
        # chunker's only identity job is seeding — see _seed_segments)
        archived = (data.get("archive") or {}).pop(body.path, None) or {}
        if archived.get("segments"):
            segments = archived.get("segments", [])
            entry_sha = archived.get("file_sha", sha)
        else:
            text = _read_note_text(body.path) or ""
            segments = _seed_segments(chunks, text.split("\n"))
            entry_sha = sha
        entry_new = {
            "path": body.path,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "file_sha": entry_sha,
            "segments": segments,
            "orphans": archived.get("orphans", []),
        }
        if archived.get("needs_resync"):
            entry_new["needs_resync"] = True
        data["list"].append(entry_new)
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
            "segments": entry.get("segments", []),
            "orphans": entry.get("orphans", []),
            **({"needs_resync": True} if entry.get("needs_resync") else {}),
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
        gated = await _reading_gates(data)
        summaries = []
        for entry in data.get("list", []):
            _, summary = _file_view(entry, gated)
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
        return await _reading_start_impl(study_mode(mode))


async def _reading_start_impl(mode: str = DEFAULT_MODE):
    data = _reading_read()
    per_round = STUDY_MODES[mode].get("read", 0)
    gated = await _reading_gates(data)
    payloads = _deal_reading(data, per_round, gated) if per_round > 0 else []
    if not payloads:
        _reading_write(data)  # persist migrations even on an empty deal
        return {
            "chunks": [],
            "mode": mode,
            "empty": True,
            "study_modes": STUDY_MODES,
            # why nothing was dealt: all_gated = every remaining chunk is
            # waiting on its cards to clear the preview pipeline (the UI can
            # then say 「等卡片过预览池」 instead of 「清单读完了」)
            "gated": len(gated),
            "all_gated": bool(gated),
        }
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
            status_code=400,
            detail="action must be mark_active|complete|skip|next|promote|demote",
        )
    data = _reading_read()
    rd = data.get("round")
    in_round = isinstance(rd, dict) and rd.get("status") == "active"
    if not in_round and action in ("mark_active", "complete", "skip", "next"):
        raise HTTPException(status_code=409, detail="no active reading round")
    target = {"path": path, "chunk_key": chunk_key}
    if in_round and target not in rd.get("pending", []) and action != "mark_active":
        return {"ok": False, "reason": "stale"}
    entry = _find_entry(data, path)
    if entry is None:
        raise HTTPException(status_code=404, detail="file not in the reading list")

    def _drop_and_maybe_complete() -> bool:
        if not in_round:
            return False
        rd["pending"] = [p for p in rd.get("pending", []) if p != target]
        rd["done"] = rd.get("done", 0) + 1
        if not rd["pending"]:
            rd["status"] = "complete"
            return True
        return False

    segs = entry.setdefault("segments", [])
    seg = next((s for s in segs if str(s.get("seg_id")) == str(chunk_key)), None)
    if seg is None:
        # file drifted mid-round and this exact segment is gone — don't
        # strand the round on it; the frontier logic re-picks on the next deal
        complete = _drop_and_maybe_complete()
        _reading_write(data)
        return {"ok": False, "reason": "drifted", "round_complete": complete}
    status = seg.get("status", "todo")
    if status == "container":
        raise HTTPException(status_code=409, detail="container segments are history")
    if action == "mark_active":
        if status == "todo":
            seg["status"] = "active"
    elif action == "complete":
        seg["status"] = "done"
    elif action == "skip":
        seg["status"] = "skipped"
    elif action == "promote":
        # background → todo (升格排期); legal outside a round
        if status != "background":
            _reading_write(data)
            return {"ok": False, "reason": "not background", "status": status}
        seg["status"] = "todo"
    elif action == "demote":
        # todo/active → background (降格回上下文)
        if status not in ("todo", "active"):
            _reading_write(data)
            return {"ok": False, "reason": "not demotable", "status": status}
        seg["status"] = "background"
    seg["updated_at"] = datetime.now().isoformat(timespec="seconds")

    round_complete = False
    if in_round and action in ("complete", "skip", "next"):
        stats_key = {"complete": "done", "skip": "skipped", "next": "next"}[action]
        stats = rd.setdefault("stats", {})
        stats[stats_key] = stats.get(stats_key, 0) + 1
        round_complete = _drop_and_maybe_complete()
    _reading_write(data)
    return {
        "ok": True,
        "status": seg.get("status", "todo"),
        "round_complete": round_complete,
        "stats": (rd or {}).get("stats", {}),
        "done": (rd or {}).get("done", 0),
        "total": (rd or {}).get("total", 0),
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


class ReadingSplitBody(BaseModel):
    path: str
    seg_id: int
    # 1-based inclusive line ranges INSIDE the parent segment, sorted,
    # disjoint. Each becomes a `todo` child. Gap disposition follows
    # `gap_policy` (spec §3.3, user spec 2026-09-22「切到哪里=书签」):
    #   bookmark (DEFAULT, 进度声明): gap BEFORE the first selection →
    #     background (已读); gap AFTER the last selection → todo (未读,
    #     keeps queueing — a big segment is a book consumed by repeated
    #     cuts); an empty tail (body < MIN_READING_BODY) degrades to
    #     background; middle gaps (multi-selection) → background.
    #   extract (提炼宣言, original r4): ALL gaps → background.
    # Pure DB operation — the file is untouched.
    selections: list[dict]
    gap_policy: str = "bookmark"


@app.post("/api/reading/split")
async def reading_split(body: ReadingSplitBody):
    """Recursive split: parent → container + 2k+1 children (round 4).

    Rules (spec 2026-09-20 round 4 + gap_policy 2026-09-22):
      - parent keeps its seg_id and EXACT range forever → cards already
        anchored to it stay valid (provenance never drifts on split);
      - parent becomes `container` (never dealt, read-only history);
      - children get fresh seg_ids, parent_seg_id = parent;
      - selected ranges → todo (scheduled);
      - gaps → `extract`: all background. `bookmark` (default): gap after
        the LAST selection → todo (未读尾巴继续排队 — the cut is a bookmark;
        the preview-pool gate on the selection's cards automatically holds
        the tail until they're digested), other gaps → background;
      - a child can be split again — recursion is just interval refinement.
    """
    async with _state_lock:
        _reading_guard()
        gap_policy = body.gap_policy
        if gap_policy not in ("bookmark", "extract"):
            raise HTTPException(status_code=400, detail="gap_policy must be bookmark|extract")
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="file not in the reading list")
        if entry.get("needs_resync"):
            raise HTTPException(status_code=409, detail="file needs reseed first")
        segs = entry.get("segments") or []
        parent = next(
            (s for s in segs if s.get("seg_id") == body.seg_id), None
        )
        if parent is None:
            raise HTTPException(status_code=404, detail="segment not found")
        if parent.get("status") == "container":
            raise HTTPException(status_code=409, detail="segment already split")
        text = _read_note_text(body.path)
        if text is None:
            raise HTTPException(status_code=404, detail="note file not found")
        lines = text.split("\n")
        p_start, p_end = parent["start_line"], parent["end_line"]
        # normalize + validate selections inside the parent range
        sels = []
        for sel in body.selections:
            try:
                a = int(sel.get("start_line"))  # type: ignore[arg-type]
                b = int(sel.get("end_line"))    # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="selections need int start_line/end_line")
            if a < 1 or b < a or b > len(lines):
                raise HTTPException(status_code=400, detail=f"selection out of file range: {a}-{b}")
            sels.append((max(a, p_start), min(b, p_end)))
        sels = [(a, b) for a, b in sels if a <= b]
        sels.sort()
        merged: list[tuple[int, int]] = []
        for a, b in sels:
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        if not merged:
            raise HTTPException(status_code=400, detail="no valid selection inside the segment")
        now = datetime.now().isoformat(timespec="seconds")
        nid = _next_seg_id(entry)
        children: list[dict] = []

        def _mk(a: int, b: int, status: str) -> None:
            nonlocal nid
            children.append({
                "seg_id": nid,
                "start_line": a,
                "end_line": b,
                "fingerprint": _seg_fingerprint(lines, a, b),
                "first_line": lines[a - 1] if a - 1 < len(lines) else "",
                "title": parent.get("title") or "",
                "heading_path": list(parent.get("heading_path") or []),
                "status": status,
                "parent_seg_id": parent["seg_id"],
                "updated_at": now,
            })
            nid += 1

        cursor = p_start
        for a, b in merged:
            if a > cursor:
                _mk(cursor, a - 1, "background")
            _mk(a, b, "todo")
            cursor = b + 1
        tail_seg_id = None
        if cursor <= p_end:
            # gap AFTER the last selection: bookmark keeps it queued (todo,
            # 未读 — the cut is a bookmark, not a discard); extract sinks it.
            tail_status = "background"
            if gap_policy == "bookmark":
                tail_text = "\n".join(lines[cursor - 1 : p_end])
                if _reading_worthwhile({"text": tail_text}):
                    tail_status = "todo"
                # an empty tail (< MIN_READING_BODY, e.g. trailing blanks)
                # degrades to background — never deal a pointless card
            _mk(cursor, p_end, tail_status)
            if tail_status == "todo":
                tail_seg_id = children[-1]["seg_id"]
        if not any(c["status"] == "todo" for c in children if c["seg_id"] != tail_seg_id):
            raise HTTPException(status_code=400, detail="selections cover nothing new")
        # containerize the parent; cards_created STAY on it (provenance rule)
        parent["status"] = "container"
        parent["updated_at"] = now
        segs.extend(children)
        # the parent may be mid-round: REPLACE it in pending with its todo
        # children (in place — the split children take the parent's position
        # so a mid-round split lands on the new smaller cards immediately)
        # so the round doesn't strand on a container.
        # bookmark 尾段 EXCLUDED (user spec 2026-09-22): the tail is 未读
        # 「之后再推」— it stays in the FILE queue (frontier deals it next
        # round, gated behind the selection's cards), NOT in this round.
        rd = data.get("round")
        if isinstance(rd, dict) and rd.get("status") == "active":
            pend = rd.get("pending", [])
            key = str(parent["seg_id"])
            at = next(
                (i for i, p in enumerate(pend)
                 if p.get("path") == body.path and p.get("chunk_key") == key),
                None,
            )
            if at is not None:
                todo_children = [
                    {"path": body.path, "chunk_key": str(c["seg_id"])}
                    for c in children
                    if c["status"] == "todo" and c["seg_id"] != tail_seg_id
                ]
                pend[at : at + 1] = todo_children
                rd["pending"] = pend
                rd["total"] = rd.get("done", 0) + len(pend)
        _reading_write(data)
        # full chunk payloads for the ON-SCREEN todo children (三栏前端,
        # 2026-09-21): the caller replaces the on-screen parent chunk with
        # these IN PLACE (reading round splice / trace detour), no extra
        # round-trip needed. The bookmark tail is excluded — it is not part
        # of this round; it comes back through the frontier later.
        ordered_new, summary_new = _file_view(entry)
        by_key = {key: (ch, st) for ch, key, st in (ordered_new or [])}
        segs_by_id = {s.get("seg_id"): s for s in entry.get("segments") or []}
        child_payloads = []
        for c in children:
            if c["status"] != "todo" or c["seg_id"] == tail_seg_id:
                continue
            hit = by_key.get(str(c["seg_id"]))
            if hit is None:
                continue
            ch, st = hit
            child_payloads.append(
                _chunk_payload(ch, str(c["seg_id"]), st, summary_new, segs_by_id)
            )
        return {
            "ok": True,
            "parent_seg_id": parent["seg_id"],
            "gap_policy": gap_policy,
            "children": [
                {"seg_id": c["seg_id"], "start_line": c["start_line"],
                 "end_line": c["end_line"], "status": c["status"],
                 "tail": c["seg_id"] == tail_seg_id}
                for c in children
            ],
            "child_chunks": child_payloads,
        }


class ReadingEditBody(BaseModel):
    path: str
    seg_id: int
    new_text: str


@app.post("/api/reading/edit")
async def reading_edit(body: ReadingEditBody):
    """In-app segment edit (user spec 2026-09-23): replace one segment's
    line range in the .md file and re-anchor EVERY segment in the same
    transaction — the app stays the only writer, so the drift fuse never
    trips (contrast: external vim edits go through _reanchor_entry).

    Rules:
      - containers are read-only history (409) — edit their children;
      - segments strictly AFTER the edited range shift by delta lines;
        ANCESTOR containers that span the range stretch (end += delta)
        and recompute their fingerprint; everything before is untouched;
      - pure shifts never break fingerprints (content is identical), so
        sibling/ancestor provenance survives line-count changes exactly;
      - the file is written atomically (tmp + os.replace) BEFORE the
        state write; file_sha updates in the same transaction;
      - round membership is untouched (seg_id/status unchanged) — the
        on-screen chunk is replaced from the returned payload.
    """
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="file not in the reading list")
        if entry.get("needs_resync"):
            raise HTTPException(status_code=409, detail="file needs reseed first")
        segs = entry.get("segments") or []
        seg = next((s for s in segs if s.get("seg_id") == body.seg_id), None)
        if seg is None:
            raise HTTPException(status_code=404, detail="segment not found")
        if seg.get("status") == "container":
            raise HTTPException(status_code=409, detail="container segments are read-only")
        new_text = body.new_text.replace("\r\n", "\n").replace("\r", "\n")
        if not new_text.strip():
            raise HTTPException(status_code=400, detail="segment cannot be empty")
        text = _read_note_text(body.path)
        if text is None:
            raise HTTPException(status_code=404, detail="note file not found")
        lines = text.split("\n")
        st = max(1, min(int(seg["start_line"]), len(lines) or 1))
        en = max(st, min(int(seg["end_line"]), len(lines)))
        new_lines = new_text.split("\n")
        delta = len(new_lines) - (en - st + 1)
        merged = lines[: st - 1] + new_lines + lines[en:]
        n = len(merged)

        # the edited segment: new range + fingerprint; refresh the title
        # when the user rewrote the heading line (breadcrumb is cosmetic —
        # heading_path keeps its ancestors)
        seg["start_line"] = st
        seg["end_line"] = st + len(new_lines) - 1
        seg["fingerprint"] = _seg_fingerprint(merged, st, seg["end_line"])
        seg["first_line"] = merged[st - 1] if st - 1 < n else ""
        c = _chunker()
        hm = c.HEADING_RE.match(seg["first_line"])
        if hm:
            new_title = hm.group(2).strip()
            hp = list(seg.get("heading_path") or [])
            if hp and hp[-1] == seg.get("title"):
                hp[-1] = new_title
                seg["heading_path"] = hp
            seg["title"] = new_title
        seg["updated_at"] = datetime.now().isoformat(timespec="seconds")

        # every OTHER segment: containment-aware shift
        for s in segs:
            if s is seg:
                continue
            s_st, s_en = s.get("start_line", 1), s.get("end_line", 1)
            if s_st > en:          # entirely after the edit → pure shift
                s["start_line"] = s_st + delta
                s["end_line"] = max(s_st + delta, s_en + delta)
            elif s_en >= en and s_st <= st:
                # spans the edited range (ancestor container) → stretch +
                # recompute the fingerprint over the new window
                s["end_line"] = max(s_st, s_en + delta)
                s["fingerprint"] = _seg_fingerprint(merged, s["start_line"], s["end_line"])
                s["first_line"] = merged[s["start_line"] - 1] if n else ""
            # segments ending before the edit: untouched
        for o in entry.get("orphans") or []:
            # parked payloads carry line info too — shift so a future
            # reseed view isn't misleading (best-effort, they have no fp)
            if isinstance(o, dict) and (o.get("start_line") or 0) > en:
                o["start_line"] = o["start_line"] + delta
                o["end_line"] = o.get("end_line", o["start_line"]) + delta

        # atomic file write, then state (file first: a failed write must
        # leave the state untouched; a failed state write leaves the file
        # ahead and the next _file_view trips the re-anchor fuse — which
        # re-locates pure-shift segments cleanly)
        p = NOTES_DIR / body.path
        tmp = p.with_suffix(p.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp.write_text("\n".join(merged), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"file write failed: {e}")
        entry["file_sha"] = hashlib.sha256("\n".join(merged).encode("utf-8")).hexdigest()
        _reading_write(data)

        # fresh payload for the on-screen chunk (same idiom as split's
        # child_chunks): _file_view reads the NEW file text back
        ordered_new, summary_new = _file_view(entry)
        segs_by_id = {s.get("seg_id"): s for s in entry.get("segments") or []}
        payload = None
        for ch, key, st_ in ordered_new or []:
            if key == str(seg["seg_id"]):
                payload = _chunk_payload(ch, key, st_, summary_new, segs_by_id)
                break
        return {
            "ok": True,
            "delta": delta,
            "line_start": seg["start_line"],
            "line_end": seg["end_line"],
            "file_sha": entry["file_sha"],
            "chunk": payload,
        }


@app.post("/api/reading/reseed")
async def reading_reseed(body: ReadingPathBody):
    """Clear needs_resync after external drift: re-anchor what still matches
    and re-seed the rest. Existing segments whose fingerprint relocates keep
    their status; everything else is rebuilt as fresh `todo` segments (cards
    on lost segments stay anchored to stale containers — provenance never
    deleted, per round-4 rule).
    """
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="file not in the reading list")
        loaded = _chunks_for(body.path)
        text = _read_note_text(body.path)
        if loaded is None or text is None:
            raise HTTPException(status_code=404, detail="note file not found")
        chunks, sha = loaded
        lines = text.split("\n")
        # try to relocate every existing segment by fingerprint. Containers
        # always survive (history anchors + lineage even without cards);
        # leaves survive when they relocate or when they hold cards (stale
        # container — provenance never deleted, per round-4 rule).
        surviving = []
        for s in entry.get("segments") or []:
            span = s["end_line"] - s["start_line"] + 1
            st = _relocate_span(
                lines, s.get("fingerprint", ""), s.get("first_line", ""), span, s["start_line"]
            )
            if s.get("status") == "container":
                if st is not None:
                    s["start_line"] = st
                    s["end_line"] = st + span - 1
                    s["first_line"] = lines[st - 1] if st - 1 < len(lines) else ""
                else:
                    s["stale"] = True
                surviving.append(s)
            elif st is not None:
                s["start_line"] = st
                s["end_line"] = st + span - 1
                s["first_line"] = lines[st - 1] if st - 1 < len(lines) else ""
                surviving.append(s)
            elif s.get("cards_created"):
                # keep as a stale container so rcards lookups still resolve
                s["status"] = "container"
                s["stale"] = True
                surviving.append(s)
        # parked orphans get one re-anchor chance (a segment orphaned by an
        # earlier drift often matches again once the file is fixed/restored —
        # restoring it keeps the split family's tiling gap-free)
        kept_orphans = []
        for o in entry.get("orphans") or []:
            sid = o.get("seg_id")
            if isinstance(sid, int) and o.get("fingerprint"):
                span = o["end_line"] - o["start_line"] + 1
                st = _relocate_span(
                    lines, o.get("fingerprint", ""), o.get("first_line", ""),
                    span, o.get("start_line", 1),
                )
                if st is not None:
                    restored = {k: v for k, v in o.items() if k != "chunk_key"}
                    restored["start_line"] = st
                    restored["end_line"] = st + span - 1
                    restored["first_line"] = lines[st - 1] if st - 1 < len(lines) else ""
                    restored["status"] = restored.get("status", "todo")
                    if restored["status"] not in (
                        "todo", "active", "done", "skipped", "background", "container"
                    ):
                        restored["status"] = "todo"
                    surviving.append(restored)
                    continue
            kept_orphans.append(o)
        entry["orphans"] = kept_orphans
        covered: set[int] = set()
        for s in surviving:
            covered.update(range(s["start_line"], s["end_line"] + 1))
        entry["segments"] = surviving  # id counter must see survivors
        nid = _next_seg_id(entry)
        fresh = []
        for ch in chunks:
            a, b = ch["line_start"], ch["line_end"]
            if any(l in covered for l in range(a, b + 1)):
                continue  # overlaps a surviving segment — leave it alone
            fresh.append({
                "seg_id": nid,
                "start_line": a,
                "end_line": b,
                "fingerprint": _seg_fingerprint(lines, a, b),
                "first_line": lines[a - 1] if a - 1 < len(lines) else "",
                "title": ch.get("title") or "",
                "heading_path": ch.get("heading_path") or [],
                "status": "todo",
                "parent_seg_id": None,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
            nid += 1
        entry["segments"] = surviving + fresh
        entry["needs_resync"] = False
        entry["file_sha"] = sha
        _reading_write(data)
        return {"ok": True, "segments": len(entry["segments"]), "fresh": len(fresh)}


@app.get("/api/reading/source")
async def reading_source(note_id: int):
    """Reverse provenance lookup (round 4): note → source segment.

    Used by the review/preview UI to show a card's TRUE source (rcards is
    exact history, not a similarity guess) and by the
    add-card dialogs to inherit reading_source for cards made while
    reviewing (卡 a 复习时制的卡 b 也归到 a 的片段). 404 = orphan card
    (pre-round-4, desktop-Anki-made, or source segment orphaned) — the UI
    then shows 无来源.
    """
    _reading_guard()
    data = _reading_read()
    for entry in list(data.get("list") or []) + [
        {"path": p, **a} for p, a in (data.get("archive") or {}).items()
    ]:
        for s in entry.get("segments") or []:
            if note_id in (s.get("cards_created") or []):
                path = entry.get("path", "")
                # breadcrumb: walk parent_seg_id up to the seeded root
                by_id = {x.get("seg_id"): x for x in entry.get("segments") or []}
                trail = []
                cur = s
                seen = set()
                while cur is not None and cur.get("seg_id") not in seen:
                    seen.add(cur.get("seg_id"))
                    trail.append(cur)
                    pid = cur.get("parent_seg_id")
                    cur = by_id.get(pid) if pid is not None else None
                trail.reverse()
                text = _read_note_text(path) or ""
                lines = text.split("\n")
                a = max(1, min(s["start_line"], len(lines) or 1))
                b = max(a, min(s["end_line"], len(lines) or a))
                return {
                    "path": path,
                    "seg_id": s["seg_id"],
                    "status": s.get("status", "todo"),
                    "stale": bool(s.get("stale")),
                    "line_start": a,
                    "line_end": b,
                    "text": "\n".join(lines[a - 1 : b]) if lines else "",
                    # every note id created from THIS segment (三栏左栏
                    # 「相关卡片」, 2026-09-21) — feed to /api/reading/cards
                    "cards_created": list(s.get("cards_created") or []),
                    "breadcrumb": [
                        {
                            "seg_id": t.get("seg_id"),
                            "status": t.get("status", "todo"),
                            "title": t.get("title") or "",
                            "line_start": t.get("line_start"),
                            "line_end": t.get("line_end"),
                            # full sibling list per crumb so the left column
                            # can show 同一片段的卡片 for cards made against
                            # an ANCESTOR (pre-split provenance rule):
                            # the container keeps the cards forever
                            "cards_created": list(t.get("cards_created") or []),
                        }
                        for t in trail
                    ],
                }
    raise HTTPException(status_code=404, detail="no reading source for this note")


@app.get("/api/reading/file")
async def reading_file(path: str):
    """Raw markdown for the reading right-hand panel (served directly from
    the corpus)."""
    _reading_guard()
    text = _read_note_text(path)
    if text is None:
        raise HTTPException(status_code=404, detail="note file not found")
    return {"path": path, "text": text}


# ---- reading-mode note images (P5, user spec 2026-09-23) ---------------------
# Notes reference images by BARE BASENAME (same convention as Anki's
# collection.media): `![](paste-….png)` → GET /api/reading/media/<name>.
# Files live in NOTES_DIR/_assets (editor uploads) or anywhere else under
# NOTES_DIR (manually dropped / Obsidian-style attachments). Serving by
# basename keeps .md files pure (user preference: 文件存纯净文本) and makes
# notes relocatable. The frontend markdown-it image rule rewrites
# non-URL srcs to this route.
NOTE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}
NOTES_ASSETS_DIR = NOTES_DIR / "_assets"


def _find_note_image(name: str) -> Path | None:
    """Traversal-safe basename lookup of one note image under NOTES_DIR.
    _assets first (the editor's upload store), then a corpus-wide scan."""
    safe = os.path.basename(name)
    if not safe or Path(safe).suffix.lower() not in NOTE_IMAGE_EXTS:
        return None
    root = NOTES_DIR.resolve()
    hit = (NOTES_ASSETS_DIR / safe).resolve()
    if hit.is_file() and hit.is_relative_to(root):
        return hit
    try:
        for p in NOTES_DIR.rglob(safe):
            rp = p.resolve()
            if rp.is_file() and rp.is_relative_to(root):
                return rp
    except OSError:
        pass
    return None


@app.get("/api/reading/media/{name:path}")
async def reading_media(name: str):
    """Serve one note-corpus image by basename (see _find_note_image)."""
    _reading_guard()
    p = _find_note_image(name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(p)


@app.post("/api/reading/media/upload")
async def reading_media_upload(file: UploadFile = File(...)):
    """Store an uploaded image in NOTES_DIR/_assets (reading-mode editor).
    Returns the bare filename to insert as `![](<filename>)` — basename
    references, served back at /api/reading/media/. Same clobber-proof
    naming as /api/media/upload (timestamp + random suffix)."""
    _reading_guard()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 25 MB)")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in NOTE_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported image type: {ext or '(none)'}")
    stem = datetime.now().strftime("note-%Y%m%d-%H%M%S")
    filename = f"{stem}-{uuid.uuid4().hex[:6]}{ext}"
    try:
        NOTES_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        (NOTES_ASSETS_DIR / filename).write_bytes(data)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    return {"filename": filename}


def _field_value(v) -> str:
    """AnkiConnect notesInfo fields are {value, order} dicts; the fake-Anki
    test harness stores plain strings — accept both."""
    if isinstance(v, dict):
        return v.get("value", "") or ""
    return v or ""


@app.get("/api/reading/cards")
async def reading_cards(notes: str):
    """Card details behind a chunk's cards_created note ids (user spec
    2026-09-20: 阅读页左栏显示本片段已制的卡片, styled like the review UI's
    相关卡片). Lazy per-chunk fetch — dealing payloads stay light.

    Read-only over AnkiConnect notesInfo. Deleted notes are silently
    skipped (AnkiConnect returns {} rows for unknown ids). Order follows
    the request order (provenance order = creation order).
    """
    _reading_guard()
    try:
        note_ids = [int(x) for x in notes.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="notes must be comma-separated ids")
    note_ids = [n for n in note_ids if n > 0][:100]
    if not note_ids:
        return {"cards": []}
    try:
        rows = await anki("notesInfo", {"notes": note_ids})
    except Exception:
        # dead AnkiConnect must not break the reading page — the frontend
        # falls back to the plain count line
        return {"cards": [], "degraded": True}
    by_id = {n["noteId"]: n for n in rows or [] if n.get("noteId")}
    out = []
    for nid in note_ids:
        n = by_id.get(nid)
        if n is None:
            continue  # deleted note
        model = n.get("modelName") or ""
        fields = {k: _field_value(v) for k, v in (n.get("fields") or {}).items()}
        out.append({
            "noteId": nid,
            "model": model,
            "kind": "cloze" if model == ADD_CLOZE_MODEL else "qa",
            "fields": fields,
            "tags": n.get("tags") or [],
            "numCards": len(n.get("cards") or []),
        })
    return {"cards": out}


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



