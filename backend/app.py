"""Anki review web app — backend (v2).

Session model (single daily flow, user spec 2026-09-24 → 2026-10-09):
  - ONE pacing tier "daily" = one reading round + a review batch of the
    ENTIRE dealable new pool + ALL due cards (D / divisor, divisor default
    1, D = the day's due snapshot). The day is meant to be done in ONE
    round (一天只做一轮, user spec 2026-10-09); new-card VOLUME is paced on
    the inflow side by RELEASE_DAILY_GOAL (36/day, lowered from 45). Sizes
    env-overridable (ANKI_DAILY_NEW>0 re-caps the draw /
    ANKI_REVIEW_DUE_DIVISOR>1 splits the due pile across rounds).
  - the reading round is LIST-DRIVEN (user spec 2026-10-06; raised to TWO
    segments per file on 2026-10-09): every file in the 阅读清单 contributes
    UP TO TWO segments per round (slot 1 = its first non-gated `active`,
    else its frontier; slot 2 = the next non-gated dealable segment), so a
    6-file list deals up to 12 segments — 每篇文章推两次. A freshly seeded
    whole-file entry has only one dealable segment and deals 1; files with
    bookmark-split children deal 2. No padding from other files.
  - whole-round interleaving: one reading slot followed by its evenly
    allocated review block; policy and cursor persist in flow.json.
    The manual PREVIEW STAGE is RETIRED
    (user spec 2026-09-27). New cards still land in the preview pool
    SUSPENDED when they are made, but nobody approves them by hand anymore:
    auto_release_pool() moves every pool card whose note was created on an
    EARLIER Anki day into the release deck unsuspended, so today's cards
    become gradeable new cards tomorrow. The overnight gap is the point
    (measured 2026-09-07: same-day grading after first contact is
    recognition, not recall — 61% vs 88% first-review pass).
  - daily release cap RELEASE_DAILY_GOAL (default 36):
    auto-release never unsuspends more than the cap per Anki day, so a
    heavy card-making day can't flood the new-card queue (user spec
    2026-09-27: 放行太多 → 复习负担太大). Oldest cards release first; the
    overflow waits for the next day. The ledger lives in
    state/auto_release.json ({day, released}).
  - new cards: NO hourly quota — every round draws up to `new` cards
    UNIFORMLY AT RANDOM from the dealable new pool (user spec 2026-09-04).
  - manual sync is default; legacy automatic sync is explicit opt-in
  - round state persists in state/round.json: refreshing the page resumes
    the in-progress round instead of dealing a fresh one (GET /api/session/state)
  - READING MODE (渐进制卡, user spec 2026-09-19, gated ANKI_READING_MODE):
    a LIST-DRIVEN reading round interleaves with review — up to TWO
    segments per 阅读清单 file (user spec 2026-10-06, raised 1→2 on
    2026-10-09: 每篇文章推两次，一天只做一轮).
    The user reads their own markdown notes (~/anki-notes, whole-file
    seeding + recursive bookmark splits) and writes cards by hand. Per
    segment: todo → active(正在制卡) → done(制卡完成), or skipped(无需制卡).
    Each file contributes AT MOST TWO segments per round (slot 1 = its first
    non-gated `active`, else its frontier; slot 2 = the next non-gated
    dealable segment in line order), so a freshly seeded whole-file entry
    deals 1 and a file with split children deals 2.
    Files are opt-in via the 阅读清单 (manual priority).
    State: state/reading.db (SQLite).

    Reading gate (user spec 2026-09-19 round 3; SEGMENT-LEVEL since the
    2026-09-29 option-B ruling): a segment whose created cards have NOT all
    left the preview pool yet (still in 预览池 or still suspended = not truly
    released) is HELD — it is not re-dealt until every one of its cards is
    thawed, but it NO LONGER blocks its file: the frontier skips over it and
    later segments keep dealing normally. With the 2026-09-27 auto-release
    this means: cards made today hold THEIR OWN segment overnight (no
    same-day re-deal = no recognition-not-recall grading), while the rest of
    the file flows on. Segments without cards are never gated. AnkiConnect
    errors fail OPEN (reading must never be blocked by a dead backend).
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# All external endpoints and filesystem paths are overridable via env vars
# so the same code runs in any deployment layout. Defaults match the repo
# layout: backend/ and frontend/ side by side under the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent

ANKICONNECT = os.getenv("ANKICONNECT_URL", "http://127.0.0.1:8765")
ANKI_BACKEND = os.getenv("ANKI_BACKEND", "connect").lower()
if ANKI_BACKEND not in ("connect", "pylib"):
    raise RuntimeError("ANKI_BACKEND must be connect or pylib")
_pylib = None
if ANKI_BACKEND == "pylib":
    from anki_backend import PylibClient, BackendError
    collection_path = os.getenv("ANKI_COLLECTION_PATH")
    if not collection_path:
        raise RuntimeError("pylib requires ANKI_COLLECTION_PATH (existing collection.anki2)")
    _pylib = PylibClient(
        collection_path,
        sync_endpoint=os.getenv("ANKI_SYNC_ENDPOINT"),
        sync_username=os.getenv("ANKI_SYNC_USERNAME"),
        sync_password=os.getenv("ANKI_SYNC_PASSWORD"),
        sync_hkey=os.getenv("ANKI_SYNC_HKEY"),
    )

# Anki collection.media directory (served back at /media/<name>)
MEDIA_DIR = Path(os.getenv("ANKI_MEDIA_DIR", str(_pylib.path.with_suffix(".media") if _pylib else _REPO_ROOT / "collection.media")))
# built SolidJS frontend (served as the SPA; skipped if absent)
REVIEW_WEB_V2_DIST = Path(
    os.getenv("REVIEW_DIST_DIR", str(_REPO_ROOT / "frontend" / "dist"))
)
STATE_DIR = Path(os.getenv("ANKI_STATE_DIR", str(Path(__file__).parent / "state")))
ROUND_FILE = STATE_DIR / "round.json"

ROUND_EXPIRE_HOURS = 24  # a half-finished round older than this is discarded

# ---- single daily pacing (user spec 2026-09-24 → 2026-10-09) ----
# One round shape, run ONCE a day (一天只做一轮): ONE reading round (every
# listed file contributes up to TWO segments — no fixed size) + a review
# batch of ALL new + ALL due, where the due size is D / REVIEW_DUE_DIVISOR
# and D = the due-card count SNAPSHOTTED at the day's first review deal
# (daily.json, Anki-day keyed). Divisor defaults to 1 (whole pile) and
# new defaults to 0 (whole dealable pool — the 36/day inflow cap
# RELEASE_DAILY_GOAL is the real volume control). The manual preview stage
# was retired 2026-09-27: new cards auto-release from the pool overnight
# (see auto_release_pool). The old quick/focus tiers are retired; `mode`
# survives on the wire as an opaque compat field and every unknown value
# normalizes to "daily". review=0 in the static table means "dynamic" and
# new=0 means "unlimited" — the wire copy carries the computed sizes.
# Sizes env-overridable.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

STUDY_MODES: dict[str, dict[str, int]] = {
    "daily": {
        # 0 = UNLIMITED (user spec 2026-10-09): the round deals the ENTIRE
        # dealable new pool — 当天放行的新卡一次做完，不积压. The daily
        # new-card volume is paced on the INFLOW side by RELEASE_DAILY_GOAL
        # (36/day, lowered from 45 — the old target was rarely finished).
        # Steady state: draw == releases == ≤36. Set ANKI_DAILY_NEW>0 to
        # re-cap the per-round draw.
        "new": _env_int("ANKI_DAILY_NEW", 0),
        "review": 0,  # dynamic: due snapshot / divisor (divisor default 1 = ALL due)
    },
}
DEFAULT_MODE = "daily"
# One round a day (user spec 2026-10-09): the day's ENTIRE due pile is dealt
# in that single round — 今天的账今天清，不积压. The old default 3 (ceil(D/3)
# per round × 早中晚三轮) is retired; set ANKI_REVIEW_DUE_DIVISOR=3 to get the
# legacy three-round pacing back.
REVIEW_DECK = os.getenv("ANKI_REVIEW_DECK", "").strip()

REVIEW_DUE_DIVISOR = max(1, _env_int("ANKI_REVIEW_DUE_DIVISOR", 1))

# Daily due snapshot (user spec 2026-09-24): D is captured ONCE per Anki day
# at the first review deal, so 早/中/晚 rounds each take ceil(D/3) and the
# day's due pile drains evenly instead of a live count decaying round over
# round (live ceil(due/3) three times only covers ~70%).
DAILY_FILE = STATE_DIR / "daily.json"


def _daily_read() -> dict:
    try:
        d = json.loads(DAILY_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _daily_write(d: dict | None) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    if d is None:
        DAILY_FILE.unlink(missing_ok=True)
        return
    tmp = DAILY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(DAILY_FILE)


async def _daily_review_size(snapshot: bool = False) -> int:
    """ceil(D/3) for the current Anki day.

    snapshot=True (review deal): capture D NOW if today has no snapshot yet.
    snapshot=False (wire display / status): never persist — show the size
    from today's snapshot, else from the live due count (so an incidental
    status poll can't lock in a bogus D). AnkiConnect failure → 0 reviews
    (new cards still deal); no snapshot is written on failure."""
    day = _anki_day()
    d = _daily_read()
    if d.get("day") == day and d.get("review_deck", "") == REVIEW_DECK and isinstance(d.get("due"), int):
        due = d["due"]
    else:
        try:
            ids = await anki("findCards", {"query": due_review_query()})
            due = len(ids or [])
        except Exception:
            return 0
        if snapshot:
            _daily_write({
                "day": day,
                "review_deck": REVIEW_DECK,
                "due": due,
                "snapshot_at": datetime.now().isoformat(timespec="seconds"),
            })
    if due <= 0:
        return 0
    return -(-due // REVIEW_DUE_DIVISOR)  # ceil division

# Daily cap for new-card releases: auto_release_pool() never unsuspends more
# than this many pool cards per Anki day, so a heavy card-making day can't
# flood the new-card queue and blow up the review burden (放行太多 → 复习负担
# 太大). Oldest cards release first; overflow waits for the next day. <=0
# disables the cap. Env-overridable. Default 36 = the user's daily new-card
# budget (lowered from 45 on 2026-10-09 — the 45 target was rarely finished;
# with ONE round a day the round deals the ENTIRE released batch, so the cap
# alone paces the new-card flow).
RELEASE_DAILY_GOAL = _env_int("ANKI_RELEASE_DAILY_GOAL", 36)
# ledger for the daily cap: {"day": YYYYMMDD, "released": N} — Anki-day
# keyed like the due snapshot, survives restarts, resets on a new day.
AUTO_RELEASE_FILE = STATE_DIR / "auto_release.json"

def study_mode(name: str | None) -> str:
    """Validate/normalize a mode name; falls back to the default.

    The quick/focus tiers are retired (single daily pacing, 2026-09-24);
    every legacy/unknown value normalizes to "daily", so a persisted
    round.json/preview.json mode from before the change still resolves."""
    return name if name in STUDY_MODES else DEFAULT_MODE

# ---- preview pool (先看后考 → 次日自动放行, user spec 2026-09-27) ----
# Newly made cards land in PREVIEW_DECK SUSPENDED, so they are invisible
# to the scheduler and to normal rounds the day they are made. The manual
# preview stage (approve/defer per card) is RETIRED: auto_release_pool()
# (below) runs at the session entry points and unsuspends every pool card
# whose note was created on an EARLIER Anki day into RELEASE_DECK, capped
# at RELEASE_DAILY_GOAL per day. The first grading therefore happens ≥1
# night after the card was written — the overnight gap is what makes it
# honest recall instead of recognition (measured 2026-09-07: same-day
# first-review pass 61% vs 88% for the pre-preview cohort).
#
# The whole feature is gated on ANKI_PREVIEW_MODE. With it unset, the pool
# endpoints report preview_mode=false and the frontend renders the exact
# legacy UI (system-level rollback).
# Data-level rollback: pool cards are untouched NEW cards; unsuspend them
# and move them out of the pool and the collection is exactly as before.
PREVIEW_MODE = os.getenv("ANKI_PREVIEW_MODE", "").lower() in ("1", "true", "yes", "on")
PREVIEW_DECK = os.getenv("ANKI_PREVIEW_DECK", "预览池")
RELEASE_DECK = os.getenv("ANKI_PREVIEW_RELEASE_DECK", "2026")
PREVIEW_FILE = STATE_DIR / "preview.json"  # legacy round state; no longer written

# ---- reading mode (渐进制卡, user spec 2026-09-19) ----
# Progressive card-making: the user reads their own markdown notes chunk by
# chunk and writes cards by hand (AI batch card-making proved too unreliable
# — fixing bad cards costs more than writing them). Memory scheduling stays
# with Anki/FSRS; the reading side is a plain priority queue + a 4-state
# machine (todo → active → done, plus skipped), NO SuperMemo-style fragment
# rescheduling in v1.
#
# Ordering rule (user spec 2026-10-06, LIST-DRIVEN): a round deals EXACTLY
# ONE segment per 阅读清单 file — its first non-gated `active` segment, else
# its frontier (first non-gated todo). All card-making quotas are retired
# (the old per-round chunk budget AND the 2026-10-05 daily goal), so an
# N-file list deals up to N segments (每篇文章都推一遍) with no floor and no
# cap; a single-file list deals a single segment. An `active` segment
# occupies its file's one slot (resurfaces every round until done/skipped).
# done/skipped/background segments are never dealt. A GATED segment
# (preview-pool hold) is skipped but does NOT lock the rest of its file
# (segment-level gate, option B 2026-09-29) — if a file's only candidates
# are gated, that file simply contributes nothing this round.
#
# 开始制卡 is GONE from the UI (2026-09-19 round 2): chunks auto-mark
# `active` when a card or cloze is created from them (_reading_record_card).
# The mark_active API action remains (harmless, used by tests/scripts).
#
# Files are opt-in (阅读清单): the corpus mixes study notes with misc logs,
# so nothing is auto-queued; the user adds files and orders them by hand
# (decision #2). Chunks come from the vendored backend/chunker.py over the
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


def _capped_study_modes(review_size: int | None = None,
                        new_size: int | None = None) -> dict[str, dict[str, int]]:
    """Wire copy of STUDY_MODES, adapted to live numbers:

    - `review` is DYNAMIC (single daily pacing 2026-09-24): ceil(D/3) from
      the daily due snapshot. review_size=None leaves the static 0
      placeholder (AnkiConnect unavailable — the frontend then hides the
      number instead of showing a bogus 0).
    - `new` may be UNLIMITED (static 0, user spec 2026-10-09 — the round
      takes the whole dealable pool): new_size resolves it to the live pool
      count for display. new_size=None likewise keeps the 0 placeholder.

    The frontend renders sizes from this table only, so the rules live in
    exactly one place. (The old budget-capped `preview` size died with the
    preview stage, 2026-09-27.)
    """
    out = {}
    for name, sizes in STUDY_MODES.items():
        row = dict(sizes)
        if review_size is not None:
            row["review"] = review_size
        if row.get("new", 0) <= 0 and new_size is not None:
            row["new"] = new_size
        out[name] = row
    return out


async def _wire_study_modes() -> dict[str, dict[str, int]]:
    """_capped_study_modes with the dynamic review size resolved (display
    mode — never persists a snapshot). new=0 (unlimited) resolves to the
    live dealable new pool so the start-screen plan shows a real number."""
    try:
        review_size = await _daily_review_size(snapshot=False)
    except Exception:
        review_size = None
    new_size = None
    if STUDY_MODES[DEFAULT_MODE].get("new", 0) <= 0:
        try:
            ids = await anki("findCards", {"query": new_card_query()})
            new_size = len(ids or [])
        except Exception:
            new_size = None
    return _capped_study_modes(review_size, new_size)

# Anki's day rollover is 4 AM (collection default, container TZ fixed to
# Asia/Shanghai). Release comparisons use the ANKI day, not the calendar
# day, so a card made at 23:50 is not "released" at 00:05 — it waits until
# the scheduler's own next day (04:00), guaranteeing a real overnight gap
# before the first grading.
ANKI_ROLLOVER_HOUR = int(os.getenv("ANKI_ROLLOVER_HOUR", "4"))


def _anki_day() -> str:
    """Current Anki day (YYYYMMDD): calendar day, rolled over at 4 AM."""
    return (datetime.now() - timedelta(hours=ANKI_ROLLOVER_HOUR)).strftime("%Y%m%d")

# card fields snapshotted before answering (for /api/undo) and the Anki
# attribute names used to restore them via setSpecificValueOfCard
SNAPSHOT_FIELDS = ("interval", "factor", "due", "reps", "lapses", "left", "type", "queue")
RESTORE_KEYS = ["ivl", "factor", "due", "reps", "lapses", "left", "type", "queue"]

async def _periodic_backup(stop, *, interval=None):
    if interval is None:
        interval = max(60, float(os.getenv("ANKI_BACKUP_INTERVAL", "1800")))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await anki("createBackup", {"folder": str(BACKUP_DIR)})
        except Exception:
            log.error("Anki periodic backup failed")


BACKUP_DIR = Path(os.getenv("ANKI_BACKUP_DIR", str(STATE_DIR / "anki-backups")))


@asynccontextmanager
async def _lifespan(app_instance):
    task = None
    backup_stop = asyncio.Event()
    try:
        if _pylib is not None:
            await anki("findCards", {"query": ""})
            recovery = await anki("engineRoundRecovery")
            if recovery and recovery.get("after"):
                existing = _round_read()
                def equivalent_round(left, right):
                    if left is None or right is None:
                        return left == right
                    # Legacy zero counters are equivalent to absent counters;
                    # nonzero statistics still require an exact journal match.
                    return {**left, 'answered_count':left.get('answered_count',0), 'new_answered':left.get('new_answered',0)} == {**right, 'answered_count':right.get('answered_count',0), 'new_answered':right.get('new_answered',0)}
                if equivalent_round(existing, recovery["before"]) or equivalent_round(existing, recovery["after"]):
                    _round_write(recovery["after"])
                    if recovery.get("undo"):
                        await anki("engineUndoAck")
                else:
                    raise RuntimeError("native answer journal conflicts with business round; manual recovery required")
            await anki("createBackup", {"folder": str(BACKUP_DIR)})
            task = asyncio.create_task(_periodic_backup(backup_stop))
        _files_relocator().recover()
        yield
    finally:
        backup_stop.set()
        if _sync_timer is not None:
            _sync_timer.cancel()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if _pylib is not None:
            await _pylib.close()


app = FastAPI(title="anki-review", lifespan=_lifespan)
# Ordinary APIs use same-origin browser access and explicit Host allowlisting.
# Non-browser local tools need no Origin or token; admin/sync retain their own guards.
class APIAccessGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith(('/api/', '/media/')):
            return await self.app(scope, receive, send)
        from urllib.parse import urlsplit
        from starlette.responses import JSONResponse
        headers = httpx.Headers(scope.get('headers', []))
        host = headers.get('host', '')
        allowed = {'localhost', '127.0.0.1', '::1'}
        allowed.update(x.strip().lower() for x in os.getenv('ANKI_TRUSTED_HOSTS', '').split(',') if x.strip())
        try:
            parsed = urlsplit('//' + host)
            hostname = parsed.hostname
            port = parsed.port
            import ipaddress
            try:
                local_ip = ipaddress.ip_address(hostname).is_loopback
            except (ValueError, TypeError):
                local_ip = False
            valid_host = bool(hostname and (hostname.lower() in allowed or local_ip) and not parsed.username
                              and not parsed.password and not parsed.path and not parsed.query
                              and not parsed.fragment and host.count(',') == 0)
        except ValueError:
            valid_host = False
        origin = headers.get('origin')
        valid_origin = origin is None
        if origin is not None:
            try:
                o = urlsplit(origin)
                scheme = scope.get('scheme', 'http')
                default_port = 443 if scheme == 'https' else 80
                valid_origin = (valid_host and o.scheme == scheme and o.hostname and o.hostname.lower() == hostname.lower()
                                and (o.port or default_port) == (port or default_port)
                                and not o.path and not o.query and not o.fragment and not o.username and not o.password)
            except ValueError:
                valid_origin = False
        # Fetch metadata protects browser requests without Origin, including GET.
        site = headers.get('sec-fetch-site')
        if not valid_host or not valid_origin or site not in (None, 'none', 'same-origin'):
            return await JSONResponse({'detail': 'untrusted API host or browser origin'}, status_code=403)(scope, receive, send)
        # No cross-origin CORS permission. Same-origin preflight is harmless.
        if scope['method'] == 'OPTIONS':
            return await JSONResponse({}, status_code=200)(scope, receive, send)
        return await self.app(scope, receive, send)

app.add_middleware(APIAccessGuard)

_sync_lock = asyncio.Lock()
_sync_pending = False  # coalesce fire-and-forget syncs
_sync_timer = None     # throttled tail: call_later handle (None = no tail armed)
_sync_last_finish = 0.0  # loop.time() when the last sync completed (throttle base)

# Serializes the read-modify-write cycles on round.json in the per-card
# mutation endpoints (answer, undo, delete). The user reviews from
# MULTIPLE devices (phone + desktop over
# tailscale); without this lock two concurrent mutations both read the old
# pending list and the later write silently drops the earlier one — an
# answered card comes back, undo slots point at stale indices. Single
# uvicorn process, so an asyncio.Lock is sufficient. Lock order is always
# _state_lock -> _sync_lock (do_sync); never the reverse, so no deadlock.
_state_lock = asyncio.Lock()


log = logging.getLogger("ir4anki")
# uvicorn's default logging config sets up its own loggers but leaves the
# root logger handler-less — without this, INFO lines below would be
# silently dropped (only WARNING+ would reach stderr via lastResort).
# journald (systemd user unit) collects stderr: journalctl --user -u ir4anki
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

# Every AnkiConnect call goes through one global queue (L2). AnkiConnect
# runs actions on Anki's main thread; concurrent HTTP requests pile up
# inside the add-on and, together with frequent `sync` (which locks the
# collection), made desktop Anki freeze. Serializing here means requests
# wait in OUR queue (no timeout burn — the clock starts after acquiring)
# instead of stacking up inside Anki. Lock order, never reversed:
#   _state_lock -> _sync_lock -> _anki_lock
_anki_lock = asyncio.Lock()

# Slow-call threshold for a WARNING line (ms). Everything is logged at
# INFO with its duration regardless — journald collects it all
# (journalctl --user -u ir4anki).
ANKI_SLOW_MS = int(os.getenv("ANKI_SLOW_MS", "2000"))

# Minimum seconds between background syncs (L1). Every answer used to
# fire a sync; with local Anki the answers come fast and syncs ran
# back-to-back, keeping the collection locked most of the time. Now a
# fire inside the cooldown window is remembered as a "tail": exactly one
# delayed sync runs when the window expires. <=0 disables throttling
# (old every-fire behaviour, still coalesced). Explicit do_sync() calls
# (session finish, release) are NEVER throttled.
SYNC_MIN_INTERVAL = float(os.getenv("ANKI_SYNC_MIN_INTERVAL", "120"))


async def anki(action: str, params: dict | None = None, timeout: float = 30):
    payload = {"action": action, "version": 6, "params": params or {}}
    async with _anki_lock:
        t0 = time.monotonic()
        try:
            if _pylib is not None:
                try:
                    result = await _pylib.call(action, params, timeout)
                except BackendError as e:
                    raise HTTPException(status_code=409 if action == "nativeUndo" else 502, detail=str(e)) from None
                ms = (time.monotonic() - t0) * 1000
                log.info("anki pylib %s %.0fms", action, ms)
                return result
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(ANKICONNECT, json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            log.error("anki %s FAILED after %.0fms: %s", action, ms, e)
            raise
    ms = (time.monotonic() - t0) * 1000
    if data.get("error"):
        log.error("anki %s AnkiConnect error: %s", action, data["error"])
        raise HTTPException(status_code=502, detail=f"AnkiConnect: {data['error']}")
    if ms >= ANKI_SLOW_MS:
        log.warning("anki %s slow: %.0fms", action, ms)
    else:
        log.info("anki %s %.0fms", action, ms)
    return data.get("result")


SYNC_POLICY = os.getenv("ANKI_SYNC_POLICY", "manual")
if SYNC_POLICY not in ("manual", "legacy"):
    raise RuntimeError("ANKI_SYNC_POLICY must be manual or legacy")


async def do_sync() -> bool:
    """One sync through AnkiConnect. Forced paths call this directly;
    fire_and_forget_sync() throttles the per-answer background path."""
    global _sync_pending, _sync_timer, _sync_last_finish
    if SYNC_POLICY == "manual":
        return False
    try:
        async with _sync_lock:
            t0 = time.monotonic()
            result = await anki("sync", timeout=300)
            if isinstance(result, dict) and result.get("deferred"):
                return False
        log.info("sync done in %.1fs", time.monotonic() - t0)
        return True
    except Exception as e:
        log.warning("sync failed: %s", e)
        return False
    finally:
        # absorb any throttled tail — the collection was just synced
        if _sync_timer is not None:
            _sync_timer.cancel()
            _sync_timer = None
        _sync_last_finish = asyncio.get_running_loop().time()
        _sync_pending = False


def fire_and_forget_sync():
    """Coalesced + throttled background sync: at most one pending at a
    time, and at most one per SYNC_MIN_INTERVAL seconds (tail delayed,
    never dropped)."""
    global _sync_pending, _sync_timer
    if SYNC_POLICY == "manual":
        return
    if _sync_pending:
        return
    _sync_pending = True
    loop = asyncio.get_running_loop()
    wait = SYNC_MIN_INTERVAL - (loop.time() - _sync_last_finish)
    if SYNC_MIN_INTERVAL <= 0 or wait <= 0:
        loop.create_task(do_sync())
    else:
        _sync_timer = loop.call_later(wait, lambda: loop.create_task(do_sync()))


# ---- new-card drawing ------------------------------------------------------
# No hourly quota since 2026-09-04 (user spec): every round independently
# draws up to the active mode's `new` count UNIFORMLY AT RANDOM from the
# dealable new pool. The old hour-keyed quota.json is no longer read or
# written.


def review_scope_query(query: str) -> str:
    """Anki deck searches include descendants; escape search syntax literally."""
    if not REVIEW_DECK:
        return query
    deck = REVIEW_DECK.replace("\\", "\\\\").replace('"', '\\"')
    deck = deck.replace("*", "\\*").replace("_", "\\_")
    return f'{query} deck:"{deck}"'.strip()


def due_review_query() -> str:
    return review_scope_query("is:due -is:new")


def new_card_query() -> str:
    """Preserve the existing preview suspension exclusion and limit recall."""
    return review_scope_query("is:new -is:suspended" if PREVIEW_MODE else "is:new")


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
        with tmp.open("w") as f:
            json.dump(rd, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(ROUND_FILE)  # atomic
        fd = os.open(STATE_DIR, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


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
    if _round_expired(rd) and not _flow_read() and not (_pylib is not None and (rd.get("last") or {}).get("snap", {}).get("native_token")):
        _round_write(None)
        return None
    return rd


async def _write_new_round(rd):
    import uuid
    rd.setdefault("id", uuid.uuid4().hex)
    rd.setdefault("answered_count", 0)
    rd.setdefault("new_answered", 0)
    previous = _round_read()
    if _pylib is not None and await _round_can_undo(previous):
        import copy
        rd["last"] = copy.deepcopy(previous["last"])
        rd["last"].update(index=0, from_prior_round=True)
        await anki("engineRoundTransition", {"before":previous,"after":rd})
    _round_write(rd)


async def restore_round_cards(rd: dict, *, current_only=False, render=True) -> list[dict]:
    """Fetch the round's still-pending cards.

    Cards answered on another device since the round started are dropped
    (counted as done) so they don't resurface. Review cards are checked
    against Anki's own due logic (prop:due<=0, since their `due` values
    are collection-day numbers); new cards are kept while still unlearned
    (prop:due does not match the new queue).
    """
    import copy
    before = copy.deepcopy(rd)
    ids = rd.get("pending", [])
    if not ids:
        return []
    due_ids = set(await anki("findCards", {"query": "cid:" + ",".join(map(str, ids)) + " prop:due<=0"}) or [])
    scoped_ids = set(await anki("findCards", {"query": review_scope_query(
        "cid:" + ",".join(map(str, ids)))}) or []) if REVIEW_DECK else set(ids)
    infos = await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": ids})
    # filter [{}] empty rows (vanished cards) before indexing
    info_by_id = {i["cardId"]: i for i in infos or [] if i.get("cardId")}
    keep = [
        cid
        for cid in ids
        if cid in info_by_id and cid in scoped_ids
        and info_by_id[cid].get("queue", 0) >= 0
        and (info_by_id[cid]["type"] == 0 or cid in due_ids)
    ]
    dropped = len(ids) - len(keep)
    if dropped:
        rd.setdefault("answered_count", _answer_count(before))
        rd["done_count"] = rd.get("done_count", 0) + dropped
    rd["pending"] = keep
    if not keep:
        rd["status"] = "complete"
    if rd != before and _pylib is not None and await _round_can_undo(before):
        await anki("engineRoundTransition", {"before":before,"after":rd})
    _round_write(rd)
    if not render:
        return []
    shown = keep[:1] if current_only else keep
    if _pylib is not None:
        infos = await anki("cardsInfo", {"cards": shown}) if shown else []
    return cards_from_infos(shown, infos)


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
    return cards_from_infos(ids, infos)


def cards_from_infos(ids, infos):
    # cardsInfo returns cards sorted by id — re-order to match the REQUEST
    # order (matters for due-order batches).
    # Defensive: adapters return empty rows for vanished cards/missing notes —
    # never index those rows directly (KeyError → 500).
    by_id = {i["cardId"]: i for i in infos or [] if i.get("cardId")}
    ordered = [by_id[cid] for cid in ids if cid in by_id]
    out = []
    for info in ordered:
        out.append(
            {
                "cardId": info["cardId"],
                "noteId": info.get("note"),
                "fields": info.get("fields"),
                "tags": info.get("tags"),
                "ord": info.get("ord"),
                "kind": info.get("kind"),
                "question": info["question"],
                "answer": info["answer"],
                "deckName": info["deckName"],
                "modelName": info["modelName"],
                "css": info.get("css", ""),
                "isNew": info["type"] == 0,
                "due": info["due"],
                "next_intervals": info.get("next_intervals"),
                "memory_state": info.get("memory_state"),
            }
        )
    # NOTE: similar-cards (anki-rag) + AI explanation (anki-explain)
    # enrichment removed 2026-09-21 (both projects retired at user request —
    # 相关卡片 moved to the left column as EXACT segment provenance;
    # priorKnowledge enrichment was already removed 2026-09-17).
    return out


async def build_batch(review_count: int, allow_new: bool, new_count: int = 0, *, render=True):
    """Assemble one study batch.

    New cards are INTERLEAVED evenly among the review cards (like Anki's
    mixed new/review ordering). They used to be appended at the very end,
    so a user who stopped after the 10 reviews never reached them — that
    was the "never pushes new cards" bug (2026-08-26).

    New-card draw (2026-09-04 spec): up to `new_count` cards sampled
    UNIFORMLY AT RANDOM from the whole dealable pool — no hourly quota, no
    priority deal, so nothing starves.
    """
    cards: list[dict] = []
    async def fetch_dealt(ids):
        if render or _pylib is None:
            return await fetch_cards(ids)
        infos = await anki('cardsMetadata', {'cards': ids})
        return [{**info, 'isNew': info['type'] == 0} for info in infos if info.get('cardId')]

    # review cards: due but not new, most overdue first
    due_ids = await anki("findCards", {"query": due_review_query()}) or []
    if due_ids:
        infos = await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": due_ids})
        # filter [{}] empty rows before indexing "due"
        infos = [i for i in infos or [] if i.get("cardId")]
        infos.sort(key=lambda i: i["due"])
        cards += await fetch_dealt([i["cardId"] for i in infos[:review_count]])

    # new cards: random sample within the per-round cap, spread evenly
    if allow_new:
        new_ids = await anki("findCards", {"query": new_card_query()}) or []
        # new_count <= 0 = UNLIMITED (user spec 2026-10-09): take the whole
        # dealable pool — the day's released batch is finished in one round.
        take = len(new_ids) if new_count <= 0 else min(new_count, len(new_ids))
        if take > 0:
            drawn = new_ids if take == len(new_ids) else random.sample(new_ids, take)
            new_cards = await fetch_dealt(drawn)
            n_reviews = len(cards)
            if n_reviews:
                for i, c in enumerate(new_cards):
                    # evenly spaced slots; +i accounts for earlier inserts
                    pos = round((i + 1) * n_reviews / (len(new_cards) + 1)) + i
                    cards.insert(min(len(cards), pos), c)
            else:
                cards += new_cards

    due_remaining = len(await anki("findCards", {"query": due_review_query()}) or [])
    new_total = len(await anki("findCards", {"query": new_card_query()}) or [])
    return cards, due_remaining, new_total


# ---- API ------------------------------------------------------------------

@app.get("/api/status")
async def status():
    try:
        due = await anki("findCards", {"query": due_review_query()})
        new = await anki("findCards", {"query": new_card_query()})
        out = {
            "anki": "ok",
            "anki_backend": ANKI_BACKEND,
            "due_review": len(due or []),
            "new_total": len(new or []),
            "new_per_round": STUDY_MODES[DEFAULT_MODE]["new"],
            "preview_mode": PREVIEW_MODE,
            "reading_mode": READING_MODE,
            # single daily tier (2026-09-24) — the frontend renders the
            # start-screen plan from this table, never hardcoded
            "study_modes": await _wire_study_modes(),
            "default_mode": DEFAULT_MODE,
            # daily auto-release cap (2026-09-27): how many pool cards may
            # still be released into the new queue today
            "release_daily_goal": RELEASE_DAILY_GOAL if PREVIEW_MODE else None,
            "release_budget_left": _release_budget_left() if PREVIEW_MODE else None,
        }
        if PREVIEW_MODE:
            out["preview_pool"] = len(await preview_pool_ids() or [])
        if READING_MODE:
            try:
                async with _state_lock:
                    out.update(await _reading_extras())
            except Exception:
                pass  # fail-soft: status must never break on reading state
        return out
    except Exception as e:
        return {"anki": "error", "anki_backend": ANKI_BACKEND, "detail": str(e)}


@app.post("/api/session/start")
async def start_session(mode: str | None = None):
    """Deal under _state_lock; the (potentially slow) sync runs outside it
    so other devices' state polls are never blocked for minutes.

    `mode` is vestigial (single daily tier since 2026-09-24); unknown/absent
    falls back to DEFAULT_MODE."""
    if _flow_read():
        raise HTTPException(status_code=409, detail="use the whole-round flow endpoint")
    synced = await do_sync()  # do_sync acquires _sync_lock itself
    async with _state_lock:
        if _flow_read():
            raise HTTPException(status_code=409, detail="use the whole-round flow endpoint")
        return await _start_session_impl(synced, study_mode(mode))


async def _start_session_impl(synced: bool, mode: str = DEFAULT_MODE, *, render=True):
    if PREVIEW_MODE:
        # next-day auto-release (2026-09-27): pool cards made on an earlier
        # day become gradeable new cards now, so this deal sees them.
        await auto_release_pool()
    m = STUDY_MODES[mode]
    # dynamic review size (2026-09-24): ceil(D/3) with D = today's due
    # snapshot, captured on the day's FIRST review deal (snapshot=True)
    review_n = await _daily_review_size(snapshot=True)
    cards, due_remaining, new_total = await build_batch(
        review_n, allow_new=True, new_count=m["new"], render=render
    )
    if cards:
        await _write_new_round(
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
    return {
        "synced": synced,
        "cards": cards,
        "due_remaining": due_remaining,
        "new_per_round": m["new"],
        "new_total": new_total,
        "mode": mode,
        "study_modes": await _wire_study_modes(),
    }


@app.get("/api/session/state")
async def session_state():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _session_state_impl()


async def _session_state_impl(*, current_only=False, render_card=True, reading_state=None):
    """Page-load entry point: resume an unfinished round instead of auto-starting."""
    if current_only:
        # Answer responses carry live round/flow progress, not a repeated
        # corpus census or global pool/statistics refresh on every grade.
        rd = current_round()
        if rd and rd.get('status') == 'active':
            cards = await restore_round_cards(rd, current_only=True, render=render_card)
        else:
            cards = []
        reading = {'reading_mode': READING_MODE}
        _, rp = reading_state if reading_state is not None else _reading_current_round()
        if rp and rp.get('status') == 'active':
            reading['reading_round'] = rp
        return {'state': rd.get('status', 'none') if rd else 'none',
                'cards': cards, 'done': (rd or {}).get('done_count', 0),
                'total': (rd or {}).get('total', 0),
                'new_in_batch': (rd or {}).get('new_count'),
                'mode': study_mode((rd or {}).get('mode')),
                'can_undo': await _round_can_undo(rd) if rd else False,
                'due_remaining': None, 'new_total': None,
                'preview_mode': PREVIEW_MODE, **reading}
    if PREVIEW_MODE:
        # next-day auto-release (2026-09-27): pool cards made on an earlier
        # day become gradeable new cards now, so every count below reflects
        # today's true new-card pool
        await auto_release_pool()
    try:
        due = await anki("findCards", {"query": due_review_query()})
        due_left = len(due or [])
    except Exception:
        due_left = None
    try:
        new_ids = await anki("findCards", {"query": new_card_query()})
        new_total = len(new_ids or [])
    except Exception:
        new_total = None

    # preview-pool extras (the frontend decides from preview_mode whether to
    # render the pool chip at all — feature flag off ⇒ exact legacy shape).
    # The manual preview ROUND (state/start/act/undo/finish + resume) died
    # 2026-09-27: releases are automatic, so there is nothing to resume.
    preview: dict = {
        "preview_mode": PREVIEW_MODE,
        # pacing table for the start screen (single daily tier, 2026-09-24) —
        # the UI renders today's plan from this, never hardcoded numbers.
        "study_modes": await _wire_study_modes(),
        "default_mode": DEFAULT_MODE,
        # daily auto-release cap (user spec 2026-09-27) + how many slots are
        # left today, so the UI can explain an overflowing pool
        "release_daily_goal": RELEASE_DAILY_GOAL if PREVIEW_MODE else None,
    }
    if PREVIEW_MODE:
        try:
            preview["pending_release"] = await _pending_release_today()
        except Exception:
            preview["pending_release"] = None
        preview["release_budget_left"] = _release_budget_left()
        try:
            pool = await preview_pool_ids()
            preview["preview_pool"] = len(pool)
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
            "can_undo": await _round_can_undo(rd),
            "mode": mode,
            **preview,
            **reading,
        }

    cards = await restore_round_cards(rd, current_only=current_only)
    if rd.get("status") == "complete" or not cards:
        return {
            "state": "complete",
            "done": rd.get("done_count", 0),
            "total": rd.get("total", 0),
            "new_in_batch": rd.get("new_count"),
            "due_remaining": due_left,
            "new_per_round": STUDY_MODES[study_mode(rd.get("mode"))]["new"],
            "new_total": new_total,
            "can_undo": await _round_can_undo(rd),
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
        "can_undo": await _round_can_undo(rd),
        "mode": mode,
        **preview,
        **reading,
    }


@app.post("/api/session/more")
async def session_more():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        if _flow_read():
            raise HTTPException(status_code=409, detail="use the whole-round flow endpoint")
        return await _session_more_impl()


async def _session_more_impl():
    """'Keep going' — another full batch: reviews + a fresh random new draw.

    Legacy endpoint (the single-flow UI 2026-09-24 no longer offers 继续复习);
    kept working for API compat. Uses the day's due SNAPSHOT (snapshot=False
    — 'more' always follows a 'start' that captured it, and a display-only
    read can't corrupt the day's divisor base).
    """
    if PREVIEW_MODE:
        await auto_release_pool()
    rd = current_round()
    mode = study_mode((rd or {}).get("mode"))
    m = STUDY_MODES[mode]
    review_n = await _daily_review_size(snapshot=False)
    cards, due_remaining, new_total = await build_batch(
        review_n, allow_new=True, new_count=m["new"]
    )
    if cards:
        await _write_new_round(
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
    return {
        "cards": cards,
        "due_remaining": due_remaining,
        "new_per_round": m["new"],
        "new_total": new_total,
        "mode": mode,
        "study_modes": await _wire_study_modes(),
    }


@app.post("/api/session/finish")
async def session_finish():
    """State write under _state_lock; the (slow) sync runs outside it."""
    async with _state_lock:
        if _flow_read():
            raise HTTPException(status_code=409, detail="use the whole-round exit endpoint")
        rd = current_round()
        if not (_pylib is not None and (rd or {}).get("last")):
            _round_write(None)
    synced = await do_sync()
    return {"synced": synced}


EXPORT_DIR = Path(os.getenv("ANKI_EXPORT_DIR", str(STATE_DIR / "anki-exports")))


def _engine_authorize(request):
    import secrets
    token = os.getenv("ANKI_ENGINE_API_TOKEN")
    if not token or not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
        raise HTTPException(status_code=403, detail="engine administration requires configured bearer token")
    if _pylib is None:
        raise HTTPException(status_code=409, detail="native engine capability unavailable in connect mode")


@app.get("/api/engine/status")
async def engine_status():
    if _pylib is None:
        return {"backend":"connect", "sync_deferred":False, "undo_available":False,
                "capabilities":dict.fromkeys(["undo","intervals","memory_state","stats","backup","export"],False)}
    return {**await anki("engineStatus"), "sync_policy": SYNC_POLICY}


@app.get("/api/engine/stats")
async def engine_stats(request: Request):
    _engine_authorize(request)
    return await anki("engineStats")


class EngineSyncBody(BaseModel):
    commit_undo: bool = False


@app.post("/api/engine/sync")
async def engine_sync(request: Request, body: EngineSyncBody):
    _engine_authorize(request)
    async with _state_lock:
        async with _sync_lock:
            result = await anki("sync", {"commit_undo":body.commit_undo}, timeout=300)
        return result if isinstance(result, dict) else {"synced":True,"deferred":False}



class LocalSyncBody(BaseModel):
    # Confirmation is mandatory; full-sync direction is deliberately unsupported.
    commit_undo: Literal[True]

    model_config = {"extra": "forbid"}


def _local_sync_authorize(request):
    """Trust only direct same-origin browser requests on a loopback listener.

    Never use forwarded headers as evidence of locality. A custom header and
    JSON plus exact Origin validation prevent form/CSRF and wildcard CORS abuse.
    Host validation prevents DNS rebinding to a loopback peer.
    """
    import ipaddress
    from urllib.parse import urlsplit

    def loopback(host):
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    try:
        target = urlsplit(f"{request.url.scheme}://{host}")
        source = urlsplit(origin)
        valid_origin = (
            source.scheme in ("http", "https") and source.netloc == host
            and source.scheme == request.url.scheme and not source.path
            and not source.query and not source.fragment and not source.username
            and loopback(target.hostname) and target.port == source.port
        )
    except (ValueError, TypeError):
        valid_origin = False
    peer = request.client.host if request.client else ""
    try:
        local_peer = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local_peer = False
    if (not local_peer or not valid_origin
        or any(h.lower() == "forwarded" or h.lower().startswith("x-forwarded-") for h in request.headers)
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
        or request.headers.get("x-ir4anki-local-sync") != "confirm"
        or request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"):
        raise HTTPException(status_code=403, detail="manual sync requires a direct same-origin loopback browser request")
    if _pylib is None:
        raise HTTPException(status_code=409, detail="native engine capability unavailable in connect mode")


@app.post("/api/local/sync")
async def local_sync(request: Request, body: LocalSyncBody):
    _local_sync_authorize(request)
    async with _state_lock:
        async with _sync_lock:
            result = await anki("sync", {"commit_undo": body.commit_undo}, timeout=300)
        return result if isinstance(result, dict) else {"synced": True, "deferred": False}


def _artifact_path(folder, handle):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.colpkg", handle):
        raise HTTPException(status_code=404, detail="unknown artifact")
    folder = folder.resolve()
    path = folder / handle
    if path.is_symlink() or path.resolve().parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="unknown artifact")
    return path


@app.post("/api/engine/backup")
async def engine_backup(request: Request):
    _engine_authorize(request)
    await anki("createBackup", {"folder":str(BACKUP_DIR)})
    files = sorted(BACKUP_DIR.glob("*.colpkg"), key=lambda p:p.stat().st_mtime)
    if not files:
        raise HTTPException(status_code=502, detail="engine backup produced no artifact")
    handle=files[-1].name
    return {"handle":handle,"download_url":"/api/engine/download/backup/"+handle}


@app.post("/api/engine/export")
async def engine_export(request: Request):
    _engine_authorize(request)
    import uuid
    EXPORT_DIR.mkdir(parents=True,exist_ok=True)
    handle=uuid.uuid4().hex+".colpkg"
    await anki("exportCollection", {"path":str(EXPORT_DIR / handle)}, timeout=300)
    return {"handle":handle,"download_url":"/api/engine/download/export/"+handle}


@app.get("/api/engine/download/{kind}/{handle}")
async def engine_download(request: Request, kind: str, handle: str):
    _engine_authorize(request)
    folders={"backup":BACKUP_DIR,"export":EXPORT_DIR}
    if kind not in folders:
        raise HTTPException(status_code=404, detail="unknown artifact")
    if (await anki("engineStatus"))["sync_deferred"]:
        raise HTTPException(status_code=409, detail="download deferred while undo is pending; explicitly commit first")
    path = _artifact_path(folders[kind],handle)
    if kind == 'backup':
        import hashlib
        marker = path.with_name(path.name+'.ir4anki-provenance.json')
        try:
            if marker.is_symlink():
                raise ValueError('invalid provenance')
            provenance = json.loads(marker.read_text())
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            safe = provenance.get('download_safe') is True and provenance.get('sha256') == digest
        except (OSError, ValueError, AttributeError):
            safe = False
        if not safe:
            raise HTTPException(status_code=409, detail='backup is local recovery material or lacks verified download provenance; create a fresh backup after resolving undo')
        from fastapi.responses import Response
        # Serve exactly the digest-verified bytes, not a path that a periodic
        # backup can overwrite between validation and FileResponse streaming.
        return Response(payload, media_type="application/octet-stream",
                        headers={'Content-Disposition':f'attachment; filename="{handle}"'})
    return FileResponse(path, filename=handle, media_type="application/octet-stream")


@app.post("/api/answer")
async def answer(card_id: int, ease: int):
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _answer_impl(card_id, ease)


async def _answer_impl(card_id: int, ease: int):
    if ease not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="ease must be 1-4")

    rd_guard = current_round()
    if rd_guard and rd_guard.get('status') == 'active':
        await restore_round_cards(rd_guard, render=False)
    f = _flow_read()
    if f:
        r = (_reading_read().get('round') or {}) if READING_MODE else {}
        _flow_reconcile(f, r.get('pending', []), (current_round() or {}).get('pending', []),
                        answered_count=_answer_count(current_round()))
        _flow_write(f)
        pending = (current_round() or {}).get('pending', [])
        if f['phase'] != 'review' or not pending or pending[0] != card_id:
            return {"answered":False,"reason":"stale","round":None}

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

    # Connect retains its legacy snapshot contract. Pylib journals the full
    # engine state and uses this snapshot only for the business undo token.
    snap = None
    infos = await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": [card_id]})
    info = next((i for i in infos or [] if i.get("cardId")), None)
    if info is not None:
        snap = {k: info.get(k) for k in SNAPSHOT_FIELDS}

    answer_params = {"answers": [{"cardId": card_id, "ease": ease}]}
    if _pylib is not None:
        import copy
        after = copy.deepcopy(rd_check)
        index = after["pending"].index(card_id)
        after["pending"].remove(card_id)
        after["done_count"] = after.get("done_count",0)+1
        after["answered_count"] = _answer_count(after)+1
        after["new_answered"] = after.get("new_answered",0)+int(info is not None and info.get("type")==0)
        after["last"] = {"cardId":card_id,"index":index,"ease":ease,"snap":copy.deepcopy(snap)}
        if not after["pending"]: after["status"]="complete"
        answer_params.update(round_before=rd_check, round_after=after)
    res = await anki("answerCards", answer_params)
    if not (res and res[0]):
        raise HTTPException(status_code=404, detail="card not found")
    if _pylib is not None and snap is not None:
        snap["native_token"] = await anki("nativeUndoToken", {"card": card_id})

    # update round state synchronously (no awaits between read and write)
    round_info = None
    rd = current_round()
    if rd and rd.get("status") == "active" and card_id in rd.get("pending", []):
        idx_pos = rd["pending"].index(card_id)
        rd["pending"] = [c for c in rd["pending"] if c != card_id]
        rd["done_count"] = rd.get("done_count", 0) + 1
        rd["answered_count"] = _answer_count(rd)+1
        rd["new_answered"] = rd.get("new_answered",0)+int(info is not None and info.get("type")==0)
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
    # NOTE: the double-Again auto-return hook (2026-09-16) is GONE
    # (user spec 2026-09-27): grading a new card Again twice no longer
    # sends it back to the preview pool — the card stays in the learning
    # queue like any other lapse.

    fire_and_forget_sync()
    state = await _flow_state_impl(adopt=False, current_only=True)
    if round_info and round_info['state'] == 'complete':
        round_info.update({k:state[k] for k in ('due_remaining','new_total','new_per_round') if k in state})
    return {"answered": True, "round": round_info, "state": state}


@app.post("/api/undo")
async def undo():
    """Serialized under _state_lock (see its declaration)."""
    async with _state_lock:
        return await _undo_impl()


async def _round_can_undo(rd):
    last = (rd or {}).get("last") or {}
    snap = last.get("snap") or {}
    if not snap:
        return False
    if _pylib is None:
        return True
    token = snap.get("native_token")
    return bool(token and token == await anki("nativeUndoToken", {"card": last["cardId"]}))


async def _undo_impl():
    """Undo the last answer of the current round.

    Pylib uses native undo or its guarded full journal; collection sync is
    deferred until undo ends. Connect retains the legacy snapshot restore.
    The card returns to the round at its saved position.
    """
    rd = current_round()
    last = (rd or {}).get("last")
    if not rd or not last or not last.get("snap"):
        raise HTTPException(status_code=409, detail="nothing to undo")

    cid = last["cardId"]
    was_pending = cid in rd.get("pending", [])
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
    if _pylib is not None:
        import copy
        undo_round = copy.deepcopy(rd)
        pos = min(last.get("index",0),len(undo_round.get("pending",[])))
        if cid not in undo_round["pending"]: undo_round["pending"].insert(pos,cid)
        if last.get("from_prior_round"):
            undo_round["total"] = undo_round.get("total",0) + (0 if was_pending else 1)
        else:
            undo_round["done_count"] = max(0, undo_round.get("done_count",0)-1)
        if undo_round["status"] == "complete": undo_round["status"]="active"
        undo_round["answered_count"] = max(0,_answer_count(undo_round)-int(not last.get("from_prior_round")))
        undo_round["new_answered"] = max(0,undo_round.get("new_answered",0)-int(snap.get("type")==0 and not last.get("from_prior_round")))
        undo_round.pop("last",None)
        res = await anki("nativeUndo", {"card": cid, "token": snap.get("native_token"), "round_undo":undo_round})
    else:
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

    # put the card back into the round where it was (defensive: never
    # duplicate if it somehow never left)
    pos = min(last.get("index", 0), len(rd.get("pending", [])))
    if cid not in rd.get("pending", []):
        rd["pending"].insert(pos, cid)
    if last.get("from_prior_round"):
        rd["total"] = rd.get("total",0) + (0 if was_pending else 1)
    else:
        rd["done_count"] = max(0, rd.get("done_count", 0) - 1)
    if rd.get("status") == "complete":
        rd["status"] = "active"
    rd["answered_count"] = max(0,_answer_count(rd)-int(not last.get("from_prior_round")))
    rd["new_answered"] = max(0,rd.get("new_answered",0)-int(snap.get("type")==0 and not last.get("from_prior_round")))
    rd.pop("last", None)
    _round_write(rd)
    if _pylib is not None:
        await anki("engineUndoAck")

    if REVIEW_DECK and not await anki("findCards", {"query": review_scope_query(f"cid:{cid}")}):
        await restore_round_cards(rd, render=False)
        fire_and_forget_sync()
        return {"restored": True, "card": None, "index": pos}
    cards = await fetch_cards([cid])
    if not cards:
        raise HTTPException(status_code=404, detail="card vanished after restore")

    # propagate the corrected scheduling to the sync server
    fire_and_forget_sync()
    out: dict = {"restored": True, "card": cards[0], "index": pos}
    return out


# ---- preview pool: helpers -------------------------------------------------


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


# NOTE: the manual preview-round machinery (_preview_read/_preview_write/
# _deferred_today_cards + the /api/preview/* endpoints) was DELETED
# 2026-09-27 — releases are automatic, there is nothing to deal, approve
# or defer. A leftover state/preview.json from before the change is
# ignored (no reader left).


async def preview_pool_count() -> int:
    return len(await preview_pool_ids())


# ---- next-day auto-release (user spec 2026-09-27) --------------------------
# The manual preview stage is retired: nobody approves pool cards by hand
# anymore. auto_release_pool() runs at every session entry point and moves
# pool cards whose NOTE was created on an EARLIER Anki day into RELEASE_DECK
# unsuspended — today's cards become tomorrow's new cards. The overnight gap
# is the whole point (2026-09-07 measurement: same-day first grading is
# recognition, not recall — 61% vs 88% pass rate).
#
# "Created on an earlier day" needs NO tags: Anki's noteId IS the creation
# timestamp in ms, so the card's age comes for free
# (datetime.fromtimestamp(nid/1000) → Anki day). A one-time LEGACY sweep
# covers cards approved under the old manual flow (released-YYYYMMDD stamp,
# suspended in RELEASE_DECK) — after deploy it drains them and goes quiet.
#
# Daily cap: at most RELEASE_DAILY_GOAL cards released per Anki day
# (ledger state/auto_release.json = {day, released}), oldest first; the
# overflow waits for the next day. The cap exists because the user's pacing
# is 3 rounds × 15 new per day — releasing more would inflate the review
# burden (user spec 2026-09-27: 放行太多 → 复习负担太大).

# legacy approve stamp (pre-2026-09-27 manual flow); the sweep below is the
# only remaining reader — no new stamps are ever written.
RELEASED_TAG_PREFIX = "released-"


def _auto_release_ledger() -> dict:
    """{day: YYYYMMDD, released: N} — today's ledger, or empty."""
    try:
        d = json.loads(AUTO_RELEASE_FILE.read_text())
        if isinstance(d, dict) and d.get("day") == _anki_day():
            return {"day": d["day"], "released": int(d.get("released", 0))}
    except Exception:
        pass
    return {}


def _auto_release_write(released_today: int) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = AUTO_RELEASE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"day": _anki_day(), "released": released_today}))
    tmp.replace(AUTO_RELEASE_FILE)


def _released_today_count() -> int:
    return _auto_release_ledger().get("released", 0)


def _release_budget_left() -> int | None:
    """Remaining daily release slots (cap − already released today).
    None when the cap is disabled (<=0)."""
    if RELEASE_DAILY_GOAL <= 0:
        return None
    return max(0, RELEASE_DAILY_GOAL - _released_today_count())


def _note_created_anki_day(note_id: int) -> str:
    """Anki day (YYYYMMDD) a note was created on — noteId is the creation
    timestamp in ms. Garbage in → "" (sorts before any real day, harmless:
    an unparseable id would be treated as ancient and released)."""
    try:
        dt = datetime.fromtimestamp(note_id / 1000) - timedelta(hours=ANKI_ROLLOVER_HOUR)
        return dt.strftime("%Y%m%d")
    except Exception:
        return ""


async def auto_release_pool() -> int:
    """Next-day auto-release (2026-09-27): move pool cards created on an
    EARLIER Anki day into RELEASE_DECK unsuspended, capped at
    RELEASE_DAILY_GOAL per day (oldest first). Also drains legacy
    released-* stamped cards from the retired manual-approve flow.

    Runs at the session entry points (session/state, session/start,
    session/more). Fail-soft: any error leaves cards suspended (they
    release on a later run) — never blocks the caller.
    """
    if not PREVIEW_MODE:
        return 0
    try:
        today = _anki_day()
        # ---- candidates -------------------------------------------------
        # legacy: approved under the manual flow, stamped, still suspended
        legacy_ids = await anki(
            "findCards",
            {"query": f'deck:"{RELEASE_DECK}" is:new is:suspended tag:{RELEASED_TAG_PREFIX}*'},
        ) or []
        legacy_ready: list[int] = []
        if legacy_ids:
            # tags come from notesInfo (cardsInfo does NOT return them)
            linfos = [
                i for i in await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": legacy_ids}) or []
                if i.get("cardId") and i.get("type") == 0
            ]
            ntags: dict[int, list[str]] = {}
            note_ids = list({i["note"] for i in linfos if i.get("note")})
            if note_ids:
                for n in await anki("notesInfo", {"notes": note_ids}) or []:
                    if n.get("noteId"):
                        ntags[n["noteId"]] = n.get("tags") or []
            for i in linfos:
                stamps = [
                    t[len(RELEASED_TAG_PREFIX):]
                    for t in ntags.get(i.get("note"), [])
                    if t.startswith(RELEASED_TAG_PREFIX)
                ]
                if stamps and min(stamps) < today:
                    legacy_ready.append(i["cardId"])
        # pool cards whose note was created before today
        pool_ids = await preview_pool_ids()  # sorted oldest-first already
        pool_ready: list[int] = []
        if pool_ids:
            pinfos = [
                i for i in await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": pool_ids}) or []
                if i.get("cardId") and i.get("type") == 0
            ]
            aged = [
                (i.get("note") or 0, i["cardId"])
                for i in pinfos
                if _note_created_anki_day(i.get("note") or 0) < today
            ]
            aged.sort()  # oldest notes first — the cap releases in age order
            pool_ready = [cid for _, cid in aged]

        candidates = legacy_ready + pool_ready
        if not candidates:
            return 0
        # ---- daily cap ----------------------------------------------------
        budget = _release_budget_left()  # None = uncapped
        take = candidates if budget is None else candidates[:budget]
        if not take:
            return 0
        await anki("changeDeck", {"cards": take, "deck": RELEASE_DECK})
        await anki("unsuspend", {"cards": take})
        if budget is not None:
            _auto_release_write(_released_today_count() + len(take))
        log.info(
            "auto-release: %d cards (legacy %d, pool %d; %d held by daily cap)",
            len(take), len(legacy_ready), len(pool_ready),
            len(candidates) - len(take),
        )
        fire_and_forget_sync()
        return len(take)
    except Exception as e:
        log.warning("auto_release_pool failed (cards stay suspended): %s", e)
        return 0


async def _pending_release_today() -> int:
    """Pool cards created TODAY — still suspended, will release at tomorrow's
    first entry point (subject to the daily cap). Reported in the wire
    payload so the UI can explain why today's cards are not in the new pool
    yet."""
    today = _anki_day()
    pool_ids = await preview_pool_ids()
    if not pool_ids:
        return 0
    infos = [
        i for i in await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": pool_ids}) or []
        if i.get("cardId") and i.get("type") == 0
    ]
    return sum(1 for i in infos if _note_created_anki_day(i.get("note") or 0) == today)


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
        "ord": info.get("ord"),
        "kind": info.get("kind") or ("cloze" if n["modelName"] == ADD_CLOZE_MODEL else "qa"),
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


# ---- card lifecycle: add / delete ------------------------------------------
# Add (user spec 2026-09-04): fixed 问答题 note type (same as the add_cards.py
# injection pipeline); the new card is routed into the preview pool SUSPENDED,
# exactly like add_cards.py's default — it joins the real rotation the NEXT
# day via auto_release_pool (2026-09-27; used to need a manual preview approve).
# Delete is a destructive per-card action with a confirmation dialog on the
# frontend. It keeps any active round consistent: the card counts as handled
# (done_count +1) and the single-level undo slot is cleared when it targeted
# the removed card (undo cannot resurrect what's gone).

ADD_MODEL = os.getenv("ANKI_ADD_MODEL", "问答题")
# 挖空卡 (user spec 2026-09-19): cloze notes use Anki's 填空题 model —
# fields are data-driven via modelFieldNames, so a renamed collection model
# only needs the env var. Cloze markers {{cN::…}} are validated before add.
ADD_CLOZE_MODEL = os.getenv("ANKI_ADD_CLOZE_MODEL", "填空题")


async def _round_remove_card(rd: dict | None, card_id: int) -> None:
    """A card left the review round without being answered (deleted): count
    it as handled so progress, the review/new split and the completion
    detection all stay correct."""
    if not rd or rd.get("status") != "active" or card_id not in rd.get("pending", []):
        return
    import copy
    before = copy.deepcopy(rd)
    rd.setdefault("answered_count", _answer_count(before))
    rd["pending"] = [c for c in rd["pending"] if c != card_id]
    rd["done_count"] = rd.get("done_count", 0) + 1
    if (rd.get("last") or {}).get("cardId") == card_id:
        rd.pop("last", None)
    if not rd["pending"]:
        rd["status"] = "complete"
    if _pylib is not None and await _round_can_undo(before):
        await anki("engineRoundTransition", {"before":before,"after":rd})
    _round_write(rd)


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
    scheduler until auto_release_pool() moves it out the next Anki day
    (先看后考, same route as add_cards.py's default). Preview mode off:
    stays in RELEASE_DECK as a regular new card.
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
            async with _state_lock:
                _reading_record_card(body.reading_source, note_id)
        except Exception:
            log.exception("reading provenance bookkeeping failed for note %s", note_id)
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
    await _round_remove_card(current_round(), card_id)
    fire_and_forget_sync()
    return {"deleted": True}


# NOTE: POST /api/card/to-preview (manual 移回预览池) is GONE (user spec
# 2026-09-27) — with the preview stage retired there is nothing to return
# a card to; a struggling card stays in the learning queue.


# ---- reading mode (渐进制卡, user spec 2026-09-19) -------------------------
# The user reads their own markdown notes chunk by chunk and writes cards by
# hand. Chunk states (human-marked): todo → active(正在制卡) → done(制卡完成),
# plus skipped(无需制卡) — done and skipped both unlock the file's next chunk.
# Within one file only the FRONTIER chunk (first not done/skipped) is ever
# dealt; an `active` chunk IS the frontier, so unfinished work automatically
# resurfaces at the head of the next reading round.
#
# Files are opt-in via the 阅读清单 (manual priority order = list order).
# Seeding is WHOLE-FILE (user decision 2026-09-24: 预切片退役) — a file joins
# the list as ONE segment covering all its lines; consumption happens through
# recursive bookmark splits (spec §3.3 「大片段不等于一次读完，递归切割本身
# 就是消费方式」), not through chunker-cut pieces. The vendored chunker.py
# survives only for LEGACY paths: the one-time round-3/4 migrations
# (_segments_from_legacy), old-client chunk_key re-binding (_record_card)
# and HEADING_RE for title refresh — it never seeds a listed file again.
#
# Segment identity (user spec 2026-09-20 round 4): DB-issued seg_id per file
# (PK path+seg_id, per-file counter, never reused). Identity no longer
# derives from line numbers or heading paths, so edits never drift it.
# Structure (ranges, status, lineage) lives in the segments table; .md files
# stay pure text (no in-file markers). The app is the ONLY writer: in-app
# edits update file + segments in one transaction. External drift (file_sha
# mismatch) trips the re-anchoring fuse: segments relocate by exact
# fingerprint (first-line index + window sha, bounded full-scan fallback);
# failures park in rorphans (card-holding ones demote to container so
# provenance survives) and the file is flagged needs_resync — out of dealing
# until POST /api/reading/reseed confirms. Reseeding tiles uncovered line
# ranges wholesale (no chunker either).
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
    """LEGACY seeding (chunker output → segments). Since 2026-09-24 listed
    files seed WHOLE (see _seed_whole_file); this survives only for the
    one-time round-3/4 migrations (_segments_from_legacy), which must
    reproduce the exact chunker tiling to re-bind legacy chunk_key states."""
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


def _file_title(lines: list[str], path: str) -> str:
    """Display title for a whole-file segment: the first markdown heading in
    the file, else the file stem."""
    c = _chunker()
    for line in lines:
        m = c.HEADING_RE.match(line)
        if m:
            return m.group(2).strip()
    return Path(path).stem


def _seed_whole_file(path: str, text: str, start_id: int = 1) -> list[dict]:
    """Whole-file seeding (user decision 2026-09-24: 预切片退役).

    One segment covering lines 1..N. The file is consumed by recursive
    bookmark splits (spec §3.3): read a bit, cut out what's worth carding,
    the unread tail stays queued. Returns [] for an empty/whitespace-only
    file (nothing to read — list/add rejects those before calling)."""
    lines = text.split("\n")
    if not _reading_worthwhile({"text": text}):
        return []
    return [{
        "seg_id": start_id,
        "start_line": 1,
        "end_line": len(lines),
        "fingerprint": _seg_fingerprint(lines, 1, len(lines)),
        "first_line": lines[0] if lines else "",
        "title": _file_title(lines, path),
        "heading_path": [],
        "status": "todo",
        "parent_seg_id": None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }]


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
    left the preview pool yet (user spec 2026-09-19 round 3, B·二段重推;
    segment-level since the 2026-09-29 option-B ruling): a gated segment is
    skipped by the frontier search and excluded from dealing until its cards
    are truly released (out of 预览池 AND unsuspended) — but it does NOT
    block its file; later segments deal normally.
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
            "gated_active": 0,
            "gated_frontier": None,
            "cards_created": 0,
        }
    counts = {"todo": 0, "active": 0, "done": 0, "skipped": 0, "background": 0}
    cards = 0
    frontier = None
    gated_count = 0
    gated_active = 0
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
            if s == "active":
                gated_active += 1
            # option B (2026-09-29): a gated segment is SKIPPED, not a
            # blocker — the frontier search continues past it and later
            # segments deal normally. gated_frontier keeps the FIRST held
            # segment for display (list chip when nothing else is dealable).
            if gated_frontier is None and not blocked:
                gated_frontier = {
                    "chunk_key": key,
                    "status": s,
                    "title": ch.get("title") or Path(path).stem,
                    "line_start": ch["line_start"],
                }
            continue
        if frontier is not None or blocked:
            continue  # frontier already found / file already held
        frontier = {
            "chunk_key": key,
            "status": s,
            "title": ch.get("title") or Path(path).stem,
            "line_start": ch["line_start"],
        }
    return {
        **base,
        "missing": False,
        "edit_revision": entry.get("file_sha", ""),
        "total_chunks": len(ordered),
        **counts,
        "frontier": frontier,
        "gated": gated_count,
        "gated_active": gated_active,
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
        # stale-coordinate guard (2026-09-30): the frontend echoes these back
        # on /api/reading/split so the backend can detect a moved/changed
        # segment instead of slicing the wrong lines.
        "fingerprint": st.get("fingerprint", ""),
        "status": st.get("status", "todo"),
        "cards_created": list(st.get("cards_created") or []),
        "ancestor_cards": ancestor_cards,
        "edit_revision": summary.get("edit_revision", ""),
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
    file_row = (path, in_list, pos, added_at, file_sha, int(needs_resync))
    old_file = conn.execute('SELECT path,in_list,pos,added_at,file_sha,needs_resync FROM rfiles WHERE path=?', (path,)).fetchone()
    if old_file != file_row:
        conn.execute('INSERT INTO rfiles(path,in_list,pos,added_at,file_sha,needs_resync) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(path) DO UPDATE SET in_list=excluded.in_list,pos=excluded.pos,added_at=excluded.added_at,file_sha=excluded.file_sha,needs_resync=excluded.needs_resync', file_row)
    old_segments = {row[1]: row for row in conn.execute('SELECT path,seg_id,start_line,end_line,fingerprint,first_line,title,heading_path,status,parent_seg_id,updated_at FROM segments WHERE path=?', (path,))}
    old_cards = list(conn.execute('SELECT path,seg_id,note_id FROM rcards WHERE path=? ORDER BY rowid', (path,)))
    old_orphans = list(conn.execute('SELECT path,chunk_key,data FROM rorphans WHERE path=? ORDER BY rowid', (path,)))
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
    new_ids = {row[1] for row in seg_rows}
    for sid in old_segments.keys() - new_ids:
        conn.execute('DELETE FROM segments WHERE path=? AND seg_id=?', (path, sid))
    changed = [row for row in seg_rows if old_segments.get(row[1]) != row]
    if changed:
        conn.executemany('INSERT OR REPLACE INTO segments(path,seg_id,start_line,end_line,fingerprint,first_line,title,heading_path,status,parent_seg_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)', changed)
    old_by_seg, new_by_seg = {}, {}
    for row in old_cards: old_by_seg.setdefault(row[1], []).append(row)
    for row in card_rows: new_by_seg.setdefault(row[1], []).append(row)
    for sid in old_by_seg.keys() | new_by_seg.keys():
        if old_by_seg.get(sid, []) != new_by_seg.get(sid, []):
            conn.execute('DELETE FROM rcards WHERE path=? AND seg_id=?', (path, sid))
            conn.executemany('INSERT INTO rcards(path,seg_id,note_id) VALUES(?,?,?)', new_by_seg.get(sid, []))
    orphan_rows = []
    for o in orphans or []:
        o = dict(o or {})
        key = o.pop("chunk_key", "")
        orphan_rows.append((path, key, json.dumps(o, ensure_ascii=False)))
    if old_orphans != orphan_rows:
        conn.execute("DELETE FROM rorphans WHERE path=?", (path,))
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
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data WHERE rround.data IS NOT excluded.data",
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


def _reading_read(*, preserve_round=False) -> dict:
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
    if isinstance(rd, dict) and _round_expired(rd) and not preserve_round and not _flow_read():
        rd = None  # half-finished reading round older than 24h
    d["round"] = rd
    return d


def _reading_write(d: dict | None):
    """Persist the working state dict back to SQLite (one transaction).

    Compare rows inside the transaction and write only deltas. No cross-request
    snapshot cache: callers serialize read-modify-write with _state_lock.
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
            removed = {row[0] for row in conn.execute('SELECT path FROM rfiles')} - set(keep)
            for path in removed:
                for table in ('rfiles', 'segments', 'rcards', 'rorphans'):
                    conn.execute(f'DELETE FROM {table} WHERE path=?', (path,))
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
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data WHERE rround.data IS NOT excluded.data",
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

    B·二段重推 (user spec 2026-09-19 round 3; SEGMENT-LEVEL since the
    2026-09-29 option-B ruling): a chunk whose created cards have not ALL
    left the preview pipeline is not re-dealt until they have — but it no
    longer blocks its file; the frontier search skips over it and later
    segments deal normally. 「过了预览池」= truly thawed:
    NOT in 预览池 AND NOT suspended — a card made today sits suspended in
    the pool all day (auto_release_pool only moves cards created on an
    EARLIER Anki day), so the segment unlocks exactly when the card
    becomes a gradeable new card the next day. Segment-level hold (option
    B, 2026-09-29): 今天制卡 → 押住「这个片段」过夜（当天不重发，避免
    recognition-not-recall）→ 明天放行 → 片段重新可发；文件其余部分照常
    推进，不被押住的片段连坐。

    Every LIVE segment with cards_created is checked (not just each file's
    frontier — depth dealing can hand out a deeper segment while an earlier
    todo one still stands, and the gate is segment-level since the
    2026-09-29 option-B ruling: a held segment never blocks its file).
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
            i for i in await anki("cardsMetadata" if _pylib is not None else "cardsInfo", {"cards": card_ids}) or []
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


def _deal_reading(data: dict, gated: set | None = None) -> list[dict]:
    """Deal UP TO TWO segments per listed file — user decision 2026-10-09.

    List-driven dealing (2026-10-06) with the per-file slot raised 1 → 2
    (每篇文章推两次，一天只做一轮). Files are visited in 阅读清单 priority
    order; each contributes AT MOST two segments:
      slot 1: the file's FIRST non-gated `active` segment (正在制卡 work
              always resurfaces) — else its frontier (first non-gated
              dealable todo segment);
      slot 2: the SECOND non-gated `active` if the file has two in-progress
              segments (actives beat line order — the old phase-0 resurface
              guarantee) — else the next non-gated dealable segment in line
              order (frontier when slot 1 was an active; the segment AFTER
              the frontier otherwise).
    A file with only ONE dealable segment contributes just one (the natural
    state for whole-file seeding before the first bookmark split). Every
    candidate gated / nothing dealable → the file contributes NOTHING this
    round — never padded from other files. So an N-file list deals up to 2N
    segments; no floor, no cap beyond that.
    done/skipped/background/container segments are never dealt; GATED
    segments (cards still in the preview pipeline) are skipped but never
    lock their file (segment-level hold, option B 2026-09-29).
    """
    gated = gated or set()
    payloads: list[dict] = []
    for entry in data.get("list", []):
        path = entry.get("path", "")
        ordered, summary = _file_view(entry, gated)  # may migrate (caller saves)
        if not ordered:
            continue
        segs_by_id = {s.get("seg_id"): s for s in entry.get("segments") or []}
        dealable = [
            (ch, key, st) for ch, key, st in ordered
            if st.get("status", "todo") in ("todo", "active")
            and (path, key) not in gated
        ]
        if not dealable:
            continue  # every candidate gated / nothing dealable — no padding
        actives = [x for x in dealable if x[2].get("status") == "active"]
        # slot 1: first active, else the frontier (= dealable[0], line order)
        first = actives[0] if actives else dealable[0]
        picked = [first]
        # slot 2: second active, else next dealable in line order
        second = None
        if len(actives) > 1:
            second = actives[1]
        else:
            second = next(
                (x for x in dealable if x[1] != first[1]),
                None,
            )
        if second is not None:
            picked.append(second)
        for ch, key, st in picked:
            payloads.append(_chunk_payload(ch, key, st, summary, segs_by_id))
    return payloads


def _reading_current_round():
    """Rehydrate only pending reading slots and durably reconcile their drift."""
    if not READING_MODE:
        return {}, None
    import copy
    data = _reading_read()
    before = copy.deepcopy(data)
    payload = _reading_round_payload(data)
    if data != before:
        _reading_write(data)
    return data, payload


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

    Caller must hold _state_lock for this synchronous read-modify-write seam.
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

    reading_round rides along ONLY while active (same policy as the old
    preview_round)
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
        dealable_segs = 0
        for entry in data.get("list", []):
            ordered, summary = _file_view(entry, gated)
            summaries.append(summary)
            # exact round-size preview (2026-10-09, two-slot dealing):
            # count each file's non-gated dealable (todo|active) segments,
            # capped at the per-file slot count — same predicate as
            # _deal_reading, so the start-screen plan matches the deal.
            n = sum(
                1
                for _ch, key, st in (ordered or [])
                if st.get("status", "todo") in ("todo", "active")
                and (entry.get("path", ""), key) not in gated
            )
            dealable_segs += min(2, n)
        out["reading_list_size"] = len(data.get("list", []))
        out["reading_available"] = sum(1 for s in summaries if s.get("frontier"))
        out["reading_dealable"] = dealable_segs
        # funnel signal: only NOT-held 正在制卡 chunks resurface next round
        # (a gated active chunk waits for its cards to clear the preview
        # pipeline — counting it here would pull the user into a reading
        # start screen that deals nothing). gated_active counts exactly the
        # held ACTIVE segments (option B, 2026-09-29), so a gated TODO (e.g.
        # a promoted card-bearing background segment) no longer distorts this.
        out["reading_active"] = sum(
            max(0, s.get("active", 0) - s.get("gated_active", 0)) for s in summaries
        )
        out["reading_gated"] = sum(s.get("gated", 0) for s in summaries)
        rp = _reading_round_payload(data)
        if rp is not None and rp.get("status") == "active":
            out["reading_round"] = rp
        _reading_write(data)  # persist any drift migrations
    except Exception:
        log.exception("reading state bookkeeping failed")  # preserve fail-soft session status
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
    """Every indexable .md in the corpus, flagged by reading-list membership.

    `archived` = a previously removed file with parked progress — adding it
    normally restores that progress; the UI offers 重新播种 (add with
    fresh=true) for such files (whole-file seeding escape hatch, 2026-09-24).
    """
    _reading_guard()
    data = _reading_read()
    listed = {e["path"] for e in data.get("list", [])}
    archived = {
        p for p, a in (data.get("archive") or {}).items()
        if (a or {}).get("segments")
    }
    return {
        "files": [
            {
                "path": p,
                "title": Path(p).stem,
                "in_list": p in listed,
                "archived": p in archived,
            }
            for p in _corpus_files()
        ]
    }


# ---- file browser (文件 section, user spec 2026-09-19 round 3) --------------
# Read-only corpus browsing for the left-rail 文件 page. Deliberately NOT
# gated on READING_MODE: browsing notes works even with the reading feature
# flag off. Same corpus rules + traversal-safe reader as reading mode.

@app.get("/api/files/list")
async def files_list():
    # dirs = every directory under the corpus (excluding protected ones).
    # The tree view builds from files alone, but the MOVE dialog needs the
    # directory list so empty dirs (a fresh 2027/ with no notes yet) are
    # still valid move destinations.
    dirs: list[str] = []
    if NOTES_DIR.is_dir():
        root = NOTES_DIR.resolve()
        for p in sorted(NOTES_DIR.rglob("*")):
            try:
                if not p.is_dir():
                    continue
            except OSError:
                continue
            rel = p.relative_to(root)
            if any(part in NOTES_SKIP_DIRS for part in rel.parts):
                continue
            dirs.append(str(rel))
    return {
        "files": [{
            "path": p, "title": Path(p).stem, "revision": _files_revision(NOTES_DIR / p)} for p in _corpus_files()],
        "dirs": dirs,
    }


@app.get("/api/files/raw")
async def files_raw(path: str):
    async with _state_lock:
        source = _files_resolve(path)
        for _ in range(2):
            if not source.is_file():
                raise HTTPException(status_code=404, detail="note file not found")
            revision = _files_revision(source)
            text = _read_note_text(path)
            if text is None:
                raise HTTPException(status_code=404, detail="note file not found")
            if _files_revision(source) == revision:
                return {"path": path, "text": text, "revision": revision}
        raise HTTPException(status_code=409, detail="文件正在变化，请重试")



# ---- file management (user spec 2026-10-06: 前端 = 语料唯一可信来源) --------
# Upload / rename / move / delete over the ~/anki-notes corpus. The reading
# store keys EVERYTHING by relative path (rfiles/segments/rcards/rorphans/
# round pending/archive), so rename & move migrate those rows in the same
# critical section — content is untouched (file_sha stays valid), so segment
# line numbers/fingerprints keep working and reading progress survives a
# rename verbatim (the user's core requirement: 改文件名不能丢阅读进度).
# Delete is HARD (user ruling): disk + all reading progress go; Anki cards
# already made from the file are NOT touched (only the app-side provenance).
# External (file-manager/Obsidian) changes are NOT reconciled automatically —
# the existing missing/orphan/file_sha drift fuses remain the safety net.

@app.middleware('http')
async def recover_file_relocations(request, call_next):
    if request.url.path.startswith('/api/'):
        directory = STATE_DIR / 'file-relocations'
        try:
            pending = _files_relocator().needs_recovery()
            if pending:
                async with _state_lock:
                    _files_relocator().recover()
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={'detail': '文件迁移恢复暂停：存在状态冲突，请保留迁移日志并检查'})
    return await call_next(request)


def _files_revision(path: Path) -> str:
    st = path.stat()
    return f"{st.st_ino}:{st.st_size}:{st.st_mtime_ns}"


@app.get("/api/files/membership")
async def files_membership():
    # No schema initialization, gate checks, corpus reads or state writes.
    if not READING_DB_FILE.exists():
        return {"paths": []}
    conn = sqlite3.connect(READING_DB_FILE.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {"paths": [r[0] for r in conn.execute("SELECT path FROM rfiles WHERE in_list=1")]}
    except sqlite3.OperationalError:
        return {"paths": []}
    finally:
        conn.close()


def _files_relocation_preflight(old, new, is_dir):
    from file_relocation import RelocationConflict
    src, dst = _files_resolve(old), _files_resolve(new)
    if not src.exists() or dst.exists():
        raise RelocationConflict('源不存在或目标已存在')
    if is_dir and (src == dst or src in dst.parents):
        raise RelocationConflict('不能移动到自己或子目录')
    _flow_read()  # fail before disk mutation on corrupt coordinator
    conn = _reading_conn()
    try:
        for table in ('rfiles', 'segments', 'rcards', 'rorphans'):
            if conn.execute(f"SELECT 1 FROM {table} WHERE path=? OR path LIKE ? ESCAPE '\\' LIMIT 1", (new, _like_escape(new + '/') + '%')).fetchone():
                raise RelocationConflict('目标存在阅读状态，不能覆盖')
    finally:
        conn.close()


def _files_relocation_signature(path, is_dir):
    def match(p):
        return p == path or (is_dir and p.startswith(path + '/'))
    conn = _reading_conn()
    try:
        rows = {t: sorted([list(r) for r in conn.execute(f'SELECT * FROM {t}') if match(r[0])], key=str)
                for t in ('rfiles', 'segments', 'rcards', 'rorphans')}
        round_row = conn.execute('SELECT data FROM rround WHERE id=1').fetchone()
        rd = json.loads(round_row[0]) if round_row and round_row[0] else {}
        rows['pending'] = [r for r in rd.get('pending', []) if match(r.get('path', ''))]
        flow = _flow_read()
        rows['flow'] = [[a for a in slot if match(a[0])] for slot in (flow or {}).get('slots', []) if any(match(a[0]) for a in slot)]
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    finally:
        conn.close()


def _files_relocator():
    from file_relocation import Relocator
    return Relocator(_files_root(), STATE_DIR, _files_relocation_preflight,
                     _reading_relocate, None, _files_relocation_signature)


def _files_relocate_api(old, new, is_dir, request_id=None, request=None):
    from file_relocation import RelocationConflict
    try:
        return _files_relocator().move(old, new, is_dir, request_id=request_id, request=request)
    except RelocationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='迁移待恢复，请刷新后重试') from exc


class FilesUndoBody(BaseModel):
    operationId: str
    requestId: str | None = None


def _files_request_result(request_id, request=None):
    from file_relocation import RelocationConflict
    try:
        return _files_relocator().request_result(request_id, request)
    except RelocationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='迁移待恢复，请刷新后重试') from exc


@app.get('/api/files/operation')
async def files_operation(request_id: str):
    async with _state_lock:
        return _files_request_result(request_id)


def _files_request_retry(request_id, request):
    if request_id:
        result = _files_request_result(request_id, request)
        if result['status'] == 'complete':
            return result
        if result['status'] == 'cancelled':
            raise HTTPException(status_code=409, detail='该请求已取消，请发起新的操作')


@app.post('/api/files/undo')
async def files_undo(body: FilesUndoBody):
    from file_relocation import RelocationConflict
    async with _state_lock:
        try:
            return _files_relocator().undo(body.operationId, body.requestId)
        except RelocationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail='撤销待恢复，请刷新确认') from exc


def _files_root() -> Path:
    return NOTES_DIR.resolve()


def _files_resolve(rel: str) -> Path:
    """Absolute path for a corpus-relative file/dir path; 400 on traversal,
    the corpus root itself, or a protected dir (.obsidian/_assets/…)."""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="path 不能为空")
    root = _files_root()
    lexical = NOTES_DIR / rel
    if any(p.is_symlink() for p in [lexical, *lexical.parents] if p == NOTES_DIR or p.is_relative_to(NOTES_DIR)):
        raise HTTPException(status_code=400, detail="不能操作符号链接路径")
    p = lexical.resolve()
    if not p.is_relative_to(root) or p == root:
        raise HTTPException(status_code=400, detail="非法路径")
    if any(part in NOTES_SKIP_DIRS for part in p.relative_to(root).parts):
        raise HTTPException(status_code=400, detail="该目录受保护，不能在 app 内操作")
    return p


def _files_rel(p: Path) -> str:
    return str(p.relative_to(_files_root()))


def _files_check_name(name: str) -> str:
    """Validate a bare file/dir name (rename target, new dir component)."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="非法名称")
    if name.startswith(".") or name in NOTES_SKIP_DIRS:
        raise HTTPException(
            status_code=400, detail="名称不能以 . 开头，也不能是保留目录名")
    return name


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _reading_relocate(old_rel: str, new_rel: str, is_dir: bool, operation_id: str | None = None, fault=lambda phase: None) -> None:
    """Migrate every reading-store reference from old_rel to new_rel.

    Single file: exact-path UPDATEs. Directory: every stored path under
    `old_rel/` is re-prefixed (rows are matched via LIKE with escaping, but
    UPDATED by their exact old value, so no escape subtleties on the write
    side). Also rewrites the active round's pending entries and drops stale
    _chunk_cache keys. Runs in ONE SQLite transaction.
    """
    old_p = old_rel.rstrip("/") + "/"
    new_p = new_rel.rstrip("/") + "/"
    mapping: dict[str, str] = {}
    conn = _reading_conn()
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE IF NOT EXISTS file_relocations (id TEXT PRIMARY KEY)")
        already = operation_id and conn.execute("SELECT 1 FROM file_relocations WHERE id=?", (operation_id,)).fetchone()
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            if operation_id and not already:
                from file_relocation import RelocationConflict
                for table in ('rfiles', 'segments', 'rcards', 'rorphans'):
                    if conn.execute(f"SELECT 1 FROM {table} WHERE path=? OR path LIKE ? ESCAPE '\\' LIMIT 1", (new_rel, _like_escape(new_rel + '/') + '%')).fetchone():
                        raise RelocationConflict('目标阅读状态已变化，迁移恢复暂停')
            for t in (() if already else ("rfiles", "segments", "rcards", "rorphans")):
                if is_dir:
                    rows = conn.execute(
                        f"SELECT DISTINCT path FROM {t} "
                        "WHERE path LIKE ? ESCAPE '\\'",
                        (_like_escape(old_p) + "%",),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT DISTINCT path FROM {t} WHERE path = ?",
                        (old_rel,),
                    ).fetchall()
                for (p,) in rows:
                    np = (new_p + p[len(old_p):]) if is_dir else new_rel
                    mapping[p] = np
                    conn.execute(
                        f"UPDATE {t} SET path = ? WHERE path = ?", (np, p))
            row = conn.execute("SELECT data FROM rround WHERE id = 1").fetchone()
            if row and row[0] and mapping:
                try:
                    rd = json.loads(row[0])
                except Exception:
                    rd = None
                if isinstance(rd, dict):
                    touched = False
                    for item in rd.get("pending") or []:
                        p = item.get("path")
                        if p in mapping:
                            item["path"] = mapping[p]
                            touched = True
                    if touched:
                        conn.execute(
                            "UPDATE rround SET data = ? WHERE id = 1",
                            (json.dumps(rd, ensure_ascii=False),),
                        )
            if operation_id and not already:
                conn.execute("INSERT INTO file_relocations(id) VALUES (?)", (operation_id,))
            fault('db_before')
        fault('db')
        # FULL commits the WAL; also persist directory entries for a newly
        # created database before the relocation journal can become complete.
        for directory in (STATE_DIR, STATE_DIR.parent):
            fd = os.open(directory, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        fault('db_durable')
        flow = _flow_read()
        if flow and flow.get('slots'):
            touched = False
            for slot in flow['slots']:
                for path, key in list(slot):
                    np = new_rel + path[len(old_rel):] if path == old_rel or (is_dir and path.startswith(old_p)) else path
                    alias = [np, key]
                    if alias not in slot:
                        slot.append(alias)
                        touched = True
            fault('flow_before')
            if touched:
                _flow_write(flow)
            fault('flow')
    finally:
        conn.close()
    for old in mapping:
        _chunk_cache.pop(old, None)


def _reading_purge(rel: str, is_dir: bool) -> None:
    """Delete ALL reading-store rows for a path (or a directory prefix) and
    drop it from the active round (counted done, same accounting as
    list/remove). Anki cards made from the file are NOT touched (user
    ruling 2026-10-06: 删除=硬删+进度清掉, 已制卡保留)."""
    prefix = rel.rstrip("/") + "/"
    conn = _reading_conn()
    try:
        with conn:
            for t in ("rfiles", "segments", "rcards", "rorphans"):
                if is_dir:
                    conn.execute(
                        f"DELETE FROM {t} WHERE path LIKE ? ESCAPE '\\'",
                        (_like_escape(prefix) + "%",),
                    )
                else:
                    conn.execute(f"DELETE FROM {t} WHERE path = ?", (rel,))
            row = conn.execute("SELECT data FROM rround WHERE id = 1").fetchone()
            if row and row[0]:
                try:
                    rd = json.loads(row[0])
                except Exception:
                    rd = None
                if isinstance(rd, dict):
                    pend = rd.get("pending") or []
                    keep = [
                        p for p in pend
                        if not (
                            p.get("path") == rel
                            or (is_dir and str(p.get("path", "")).startswith(prefix))
                        )
                    ]
                    dropped = len(pend) - len(keep)
                    if dropped:
                        rd["pending"] = keep
                        rd["done"] = rd.get("done", 0) + dropped
                        if not keep:
                            rd["status"] = "complete"
                        conn.execute(
                            "UPDATE rround SET data = ? WHERE id = 1",
                            (json.dumps(rd, ensure_ascii=False),),
                        )
    finally:
        conn.close()
    _chunk_cache.pop(rel, None)


class FilesRenameBody(BaseModel):
    path: str
    new_name: str
    requestId: str | None = None


class FilesMoveBody(BaseModel):
    path: str
    new_dir: str  # corpus-relative; "" = corpus root
    requestId: str | None = None


class FilesDeleteBody(BaseModel):
    path: str
    confirm: str  # must equal the basename (防呆, user ruling)


@app.post("/api/files/rename")
async def files_rename(body: FilesRenameBody):
    async with _state_lock:
        request = dict(action='rename', path=body.path, value=body.new_name)
        replay = _files_request_retry(body.requestId, request)
        if replay:
            return replay
        src = _files_resolve(body.path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        name = _files_check_name(body.new_name)
        is_dir = src.is_dir()
        is_md = src.is_file() and src.suffix.lower() == ".md"
        if src.is_file() and not is_md:
            # v1 limitation (user-approved): note images are referenced by
            # BARE BASENAME from .md files — renaming one silently breaks
            # every reference. Images travel with folder moves instead.
            raise HTTPException(
                status_code=400,
                detail="暂不支持重命名图片（会断开 .md 里的引用）；图片可随文件夹整体移动",
            )
        if is_md and Path(name).suffix.lower() != ".md":
            name += ".md"  # friendly: inline rename without the extension
        dst = src.parent / name
        old_rel = _files_rel(src)
        if dst == src:
            return {"ok": True, "path": old_rel, "unchanged": True}
        if dst.exists():
            raise HTTPException(status_code=409, detail="目标名称已存在")
        return _files_relocate_api(old_rel, _files_rel(dst), is_dir, body.requestId, request)


@app.post("/api/files/move")
async def files_move(body: FilesMoveBody):
    async with _state_lock:
        request = dict(action='move', path=body.path, value=body.new_dir)
        replay = _files_request_retry(body.requestId, request)
        if replay:
            return replay
        src = _files_resolve(body.path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        new_dir_rel = (body.new_dir or "").strip().replace("\\", "/").strip("/")
        dst_dir = _files_root() if not new_dir_rel else _files_resolve(new_dir_rel)
        if dst_dir.exists() and not dst_dir.is_dir():
            raise HTTPException(status_code=400, detail="目标不是文件夹")
        is_dir = src.is_dir()
        if is_dir and (dst_dir == src or src in dst_dir.parents):
            raise HTTPException(status_code=400, detail="不能把文件夹移动到它自己里面")
        dst = dst_dir / src.name
        old_rel = _files_rel(src)
        if dst == src:
            return {"ok": True, "path": old_rel, "unchanged": True}
        if dst.exists():
            raise HTTPException(status_code=409, detail="目标位置已有同名文件/文件夹")
        return _files_relocate_api(old_rel, _files_rel(dst), is_dir, body.requestId, request)


@app.post("/api/files/delete")
async def files_delete(body: FilesDeleteBody):
    async with _state_lock:
        src = _files_resolve(body.path)
        if not src.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        # 防呆 (user ruling): the request must carry the exact basename
        if (body.confirm or "") != src.name:
            raise HTTPException(
                status_code=400,
                detail=f"确认文本不匹配（需要输入 {src.name}）",
            )
        rel = _files_rel(src)
        is_dir = src.is_dir()
        if is_dir:
            shutil.rmtree(src)
        else:
            src.unlink()
        _reading_purge(rel, is_dir)
        return {"ok": True, "deleted": rel, "was_dir": is_dir}


def _rewrite_image_refs(text: str, mapping: dict[str, str], source: str, destination: str, *, max_bytes: int) -> str:
    """Resolve references in the original upload tree, then map to final paths."""
    import posixpath
    from urllib.parse import urlsplit, unquote, quote
    def rewrite(ref):
        try:
            parsed = urlsplit(re.sub(r'\\([\\() ])', r'\1', ref))
        except ValueError:
            return ref
        if parsed.scheme or parsed.netloc or ref.startswith(('/', '#')):
            return ref
        identity = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(parsed.path)))
        if identity == '..' or identity.startswith('../') or identity not in mapping:
            return ref
        result = quote(posixpath.relpath(mapping[identity], posixpath.dirname(destination) or '.'), safe='/.-_')
        return result + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
    def visible_markdown(source_text):
        # Parse blocks only: token maps include quote/list containers. Keep original
        # offsets and bytes; the parser's normalized text is never written back.
        from markdown_it import MarkdownIt
        parser = MarkdownIt('commonmark', {'maxNesting': 100})
        normalized = source_text.replace('\r\n', '\n').replace('\r', '\n')
        tokens = []
        parser.block.parse(normalized, parser, {}, tokens)
        starts = [0] + [match.end() for match in re.finditer(r'\r\n|\r|\n', source_text)]
        starts.append(len(source_text))
        characters = list(source_text)
        for token in tokens:
            if token.type in ('code_block', 'fence') and token.map is not None:
                start, stop = token.map
                for position in range(starts[start], starts[stop]):
                    if characters[position] not in '\r\n':
                        characters[position] = ' '
        visible = ''.join(characters)
        runs = list(re.finditer(r'`+', visible))
        next_same, last = {}, {}
        for index in range(len(runs) - 1, -1, -1):
            length = len(runs[index][0])
            if length in last:
                next_same[index] = last[length]
            last[length] = index
        characters, end = list(visible), 0
        for index, run in enumerate(runs):
            if run.start() < end or index not in next_same:
                continue
            # Backslash escapes apply to openers outside code, not closers inside.
            backslashes, position = 0, run.start() - 1
            while position >= 0 and visible[position] == '\\':
                backslashes += 1; position -= 1
            if backslashes % 2:
                continue
            end = runs[next_same[index]].end()
            for position in range(run.start(), end):
                if characters[position] != '\n':
                    characters[position] = ' '
        return ''.join(characters)

    def bounded_sub(pattern, replace, source_text, flags=0):
        parts, offset, size = [], 0, 0
        def append(value):
            nonlocal size
            size += len(value.encode('utf-8'))
            if size > max_bytes:
                raise HTTPException(status_code=413, detail=f'图片引用改写后超出文件预算（{max_bytes} 字节）')
            parts.append(value)
        compiled = re.compile(pattern, flags)
        for match in compiled.finditer(visible_markdown(source_text)):
            original = compiled.match(source_text, match.start())
            replacement = replace(original) if original and original.end() == match.end() else source_text[match.start():match.end()]
            append(source_text[offset:match.start()]); append(replacement); offset = match.end()
        append(source_text[offset:])
        return ''.join(parts)
    # Inline destinations can have balanced parentheses or escaped punctuation.
    mask, parts, offset, size = visible_markdown(text), [], 0, 0
    def append_inline(value):
        nonlocal size
        size += len(value.encode('utf-8'))
        if size > max_bytes:
            raise HTTPException(status_code=413, detail=f'图片引用改写后超出文件预算（{max_bytes} 字节）')
        parts.append(value)
    for match in re.finditer(r'!\[[^\]\n]*\]\(\s*', mask):
        start = match.end()
        if start < offset or start >= len(mask):
            continue
        if mask[start] == '<':
            end = mask.find('>', start + 1)
            if end < 0 or '\n' in mask[start:end]:
                continue
            start += 1
        else:
            end, depth = start, 0
            while end < len(mask):
                char = mask[end]
                if char == '\\' and end + 1 < len(mask):
                    end += 2; continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0: break
                    depth -= 1
                elif char.isspace():
                    break
                end += 1
            if depth or end == len(mask):
                continue
        append_inline(text[offset:start]); append_inline(rewrite(text[start:end])); offset = end
    append_inline(text[offset:])
    text = ''.join(parts)
    labels = {re.sub(r'\s+', ' ', (m[2] or m[1])).strip().lower()
              for m in re.finditer(r'!\[([^\]\n]+)\](?:\[([^\]\n]*)\])?(?!\()', visible_markdown(text))}
    text = bounded_sub(r'(?m)^( {0,3}\[([^\]\n]+)\]:[ \t]*)(<[^>]+>|[^\s]+)([^\n]*)$',
        lambda m: m[1] + (('<' + rewrite(m[3][1:-1]) + '>') if m[3].startswith('<') else rewrite(m[3])) + m[4]
            if re.sub(r'\s+', ' ', m[2]).strip().lower() in labels else m[0], text)
    return bounded_sub(r"((?:src|href)\s*=\s*[\"'])([^\"']+)([\"'])",
                  lambda m: m[1] + rewrite(m[2]) + m[3], text, flags=re.I)


@app.post("/api/files/upload")
async def files_upload(
    files: list[UploadFile] = File(...),
    rel_paths: list[str] = Form(...),
    target_dir: str = Form("", alias="dir"),
    on_conflict: str = Form("error"),  # error | overwrite | rename | skip
):
    """Upload .md notes + images into the corpus (user spec 2026-10-06).

    Folder uploads ride webkitRelativePath: the frontend sends every file
    with its relative path and they land under `dir` keeping the subtree.
    .md name conflict → policy (error=409 so the UI can ask; overwrite /
    rename=auto `name (1).md` / skip). IMAGES keep their relative position,
    but a basename collision with ANY existing corpus image auto-prefixes
    the folder name (media is served by basename — the textbook-import
    lesson) and the same batch's .md references are rewritten to match.
    Uploaded files do NOT auto-join the reading list (stays opt-in).
    """
    if len(files) != len(rel_paths):
        raise HTTPException(status_code=400, detail="files 与 rel_paths 数量不一致")
    if not files:
        raise HTTPException(status_code=400, detail="没有上传任何文件")
    if on_conflict not in ("error", "overwrite", "rename", "skip"):
        raise HTTPException(status_code=400, detail="on_conflict 非法")
    tdir_rel = (target_dir or "").strip().replace("\\", "/").strip("/")
    tdir = _files_root() if not tdir_rel else _files_resolve(tdir_rel)
    if tdir.exists() and not tdir.is_dir():
        raise HTTPException(status_code=400, detail="目标不是文件夹")

    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"上传文件过多（最多 {UPLOAD_MAX_FILES} 个）")
    total_bytes = 0
    # read + validate everything up front (all-or-nothing before writes)
    items: list[dict] = []
    original_paths = set()
    for up, rel in zip(files, rel_paths):
        rel = (rel or "").strip().replace("\\", "/")
        if rel.startswith("/"):
            raise HTTPException(status_code=400, detail="上传路径必须是相对路径")
        import posixpath
        rel = posixpath.normpath(rel) if rel else ""
        if not rel:
            raise HTTPException(status_code=400, detail="缺少相对路径")
        dest = _files_resolve(
            (tdir_rel + "/" if tdir_rel else "") + rel)
        identity = _files_rel(dest)
        if identity in original_paths:
            raise HTTPException(status_code=409, detail=f"批内重复目标：{rel}")
        original_paths.add(identity)
        suffix = dest.suffix.lower()
        if suffix == ".md":
            kind = "md"
        elif suffix in NOTE_IMAGE_EXTS:
            kind = "img"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"仅支持 .md 笔记和图片：{rel}",
            )
        data = await _read_upload_bounded(up, min(UPLOAD_MAX_BYTES, UPLOAD_BATCH_MAX_BYTES - total_bytes), rel)
        total_bytes += len(data)
        if not data:
            raise HTTPException(status_code=400, detail=f"空文件：{rel}")
        if len(data) > UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413, detail=f"文件过大（最多 25 MB）：{rel}")
        if kind == "md":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail=f"不是有效的 UTF-8 文本：{rel}")
        items.append({"rel": rel, "dest": dest, "kind": kind, "data": data})

    async with _state_lock:
        # Revalidate after off-lock reads; mutations and name allocation remain serialized.
        for it in items:
            _files_resolve((tdir_rel + "/" if tdir_rel else "") + it["rel"])
        if tdir.exists() and not tdir.is_dir():
            raise HTTPException(status_code=409, detail="目标不是文件夹")
        # existing image basenames (media is served corpus-wide by basename)
        img_names: set[str] = set()
        for p in NOTES_DIR.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in NOTE_IMAGE_EXTS:
                    img_names.add(p.name)
            except OSError:
                continue

        reserved: set[Path] = {it["dest"] for it in items}
        allocated: set[Path] = set()

        def _free_name(dirpath: Path, name: str) -> str:
            """First free `name`, else `stem (1).ext`, `stem (2).ext`, …"""
            if not (dirpath / name).exists() and (dirpath / name) not in reserved and (dirpath / name) not in allocated:
                return name
            stem, ext = Path(name).stem, Path(name).suffix
            i = 1
            while ((dirpath / f"{stem} ({i}){ext}").exists() or (dirpath / f"{stem} ({i}){ext}") in reserved or (dirpath / f"{stem} ({i}){ext}") in allocated):
                i += 1
            return f"{stem} ({i}){ext}"

        conflicts: list[str] = []
        rename_map: dict[str, str] = {}   # original relative image identity → final corpus path
        for it in items:
            dest: Path = it["dest"]
            if it["kind"] == "md":
                if dest.exists():
                    if on_conflict == "error":
                        conflicts.append(it["rel"])
                    elif on_conflict == "overwrite":
                        it["action"] = "overwritten"
                    elif on_conflict == "rename":
                        dest = dest.parent / _free_name(dest.parent, dest.name)
                        it["dest"] = dest
                        it["action"] = "renamed"
                    else:  # skip
                        it["action"] = "skipped"
                else:
                    it["action"] = "written"
            else:
                if dest.name in img_names:
                    # basename collision → prefix with the upload's first
                    # folder component (textbook-import lesson), else (N)
                    parts = Path(it["rel"]).parts
                    prefix = parts[0] if len(parts) > 1 else (
                        tdir_rel.split("/")[-1] if tdir_rel else "img")
                    prefix = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", prefix).strip("-")
                    cand = f"{prefix}-{dest.name}"
                    i = 1
                    stem, ext = Path(cand).stem, Path(cand).suffix
                    while cand in img_names:
                        cand = f"{stem} ({i}){ext}"
                        i += 1
                    it["dest"] = dest.parent / cand
                    it["action"] = "renamed"
                else:
                    it["action"] = "written"
                img_names.add(it["dest"].name)  # claim within the batch
                rename_map[it["rel"]] = _files_rel(it["dest"])

        if conflicts:
            raise HTTPException(
                status_code=409,
                detail=json.dumps(
                    {"conflicts": conflicts}, ensure_ascii=False),
            )

        existing_parent = _files_root()
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        name_limit = os.pathconf(existing_parent, 'PC_NAME_MAX')
        path_limit = os.pathconf(existing_parent, 'PC_PATH_MAX')
        for it in items:
            dest = it['dest']
            if (any(len(part.encode('utf-8')) > name_limit for part in dest.parts)
                or len(os.fsencode(dest)) >= path_limit):
                raise HTTPException(status_code=400, detail=f"分配后的路径过长：{it['rel']}")

        for it in items:
            if it['action'] == 'skipped':
                continue
            dest = it['dest']
            if dest in allocated or (dest.exists() and not dest.is_file()):
                raise HTTPException(status_code=409, detail=f"目标冲突：{it['rel']}")
            allocated.add(dest)
            if any((parent.exists() and not parent.is_dir()) or parent in reserved for parent in dest.parents if parent != _files_root() and parent.is_relative_to(_files_root())):
                raise HTTPException(status_code=409, detail=f"父路径冲突：{it['rel']}")
            if it['kind'] == 'md':
                it['data'] = _rewrite_image_refs(it['data'].decode('utf-8'), rename_map, it['rel'], _files_rel(dest), max_bytes=UPLOAD_MAX_BYTES).encode('utf-8')

        if sum(len(it['data']) for it in items) > UPLOAD_BATCH_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f'改写后超出上传批次预算（{UPLOAD_BATCH_MAX_BYTES} 字节）')

        # Each file is atomically replaced. A batch is NOT a filesystem transaction:
        # true I/O failures report exactly what committed, allowing a conscious retry.
        saved: list[dict] = []
        for index, it in enumerate(items):
            if it['action'] == 'skipped':
                saved.append({'path': it['rel'], 'action': 'skipped'})
                continue
            dest = it['dest']
            temp = dest.parent / ('.upload-' + uuid.uuid4().hex)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with temp.open('xb') as stream:
                    stream.write(it['data']); stream.flush(); os.fsync(stream.fileno())
                temp.replace(dest)
                saved.append({'path': _files_rel(dest), 'action': it['action'], 'original': it['rel']})
                fd = os.open(dest.parent, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            except OSError as exc:
                raise HTTPException(status_code=503, detail={'partial': bool(saved), 'saved': saved,
                    'failed': it['rel'], 'remaining': [x['rel'] for x in items[index:]],
                    'message': '上传 I/O 失败；请核对已保存文件后重试'}) from exc
            finally:
                with suppress(OSError):
                    temp.unlink(missing_ok=True)
        return {'ok': True, 'saved': saved, 'images_renamed': {rel: final for rel, final in rename_map.items()
            if final != ((tdir_rel + '/') if tdir_rel else '') + rel}}



class ReadingPathBody(BaseModel):
    path: str
    # list/add only: discard archived progress and re-seed from scratch
    # (user decision 2026-09-24 — no data migration for the whole-file
    # seeding change; a file pre-cut under the old regime re-joins as ONE
    # segment via remove → add(fresh=true))
    fresh: bool = False


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
        text = _read_note_text(body.path)
        if text is None:
            raise HTTPException(status_code=404, detail="note file not found in the corpus")
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件是空的，没有可读内容")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # re-adding a previously removed file restores its old progress
        # unless `fresh` asks for a clean re-seed (whole-file seeding,
        # 2026-09-24 — see _seed_whole_file)
        archived = (data.get("archive") or {}).pop(body.path, None) or {}
        if archived.get("segments") and not body.fresh:
            segments = archived.get("segments", [])
            entry_sha = archived.get("file_sha", sha)
        else:
            segments = _seed_whole_file(body.path, text)
            if not segments:
                raise HTTPException(status_code=400, detail="文件里没有可读的片段")
            entry_sha = sha
        entry_new = {
            "path": body.path,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "file_sha": entry_sha,
            "segments": segments,
            "orphans": [] if body.fresh else archived.get("orphans", []),
        }
        if archived.get("needs_resync") and not body.fresh:
            entry_new["needs_resync"] = True
        data["list"].append(entry_new)
        _reading_write(data)
        return {
            "ok": True,
            "order": [e["path"] for e in data["list"]],
            "segments": len(segments),
            "fresh": bool(body.fresh) or not bool(archived.get("segments")),
        }


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
    """Deal one reading round: up to two segments per 阅读清单 file."""
    async with _state_lock:
        _reading_guard()
        if _flow_read():
            raise HTTPException(status_code=409, detail="use the whole-round flow endpoint")
        return await _reading_start_impl(study_mode(mode))


async def _reading_start_impl(mode: str = DEFAULT_MODE):
    if PREVIEW_MODE:
        # entry point for the daily chain (2026-09-27): run the auto-release
        # BEFORE computing the gates, so a page left open across the 4 AM
        # rollover unlocks gated frontiers on 开始 without a reload
        await auto_release_pool()
    # resolved pacing table (dynamic review size, 2026-09-24) — the frontend
    # pipes this into its studyModes signal, so it must not carry review=0
    modes = await _wire_study_modes()
    # List-driven dealing (user spec 2026-10-06): every listed file
    # contributes exactly one segment; there is NO card-making quota anymore
    # (the 2026-10-05 daily goal and its start-time stand-down are retired),
    # so a round is only empty when nothing is dealable.
    data = _reading_read()
    gated = await _reading_gates(data)
    payloads = _deal_reading(data, gated)
    if not payloads:
        _reading_write(data)  # persist migrations even on an empty deal
        return {
            "chunks": [],
            "mode": mode,
            "empty": True,
            "study_modes": modes,
            # why nothing was dealt: all_gated = every remaining chunk is
            # waiting on its cards to clear the preview pipeline (the UI can
            # then say 「等卡片过预览池」 instead of 「清单读完了」)
            "gated": len(gated),
            "all_gated": bool(gated),
        }
    data["round"] = {
        "id": uuid.uuid4().hex,
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
        "study_modes": modes,
    }


@app.post("/api/reading/act")
async def reading_act(path: str, chunk_key: str, action: str):
    async with _state_lock:
        _reading_guard()
        if _flow_read() and action in ('complete', 'skip', 'next'):
            wire = await _flow_state_impl(adopt=False)
            chunks = (wire.get('reading_round') or {}).get('chunks',[])
            if wire['flow']['phase'] != 'reading' or not chunks or (chunks[0]['path'],chunks[0]['chunk_key']) != (path,chunk_key):
                return {'ok':False,'reason':'stale'}
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
    promote/demote: background ⇄ todo/active salvage moves — NOT round
        members by nature (option-B fix 2026-09-29: they bypass the
        in-round stale check, so the salvage API works mid-round too).
        demote of a segment that IS pending drops it from the round
        (a background segment can never be dealt, so it must not strand).
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
    # round-advancing actions must target a pending chunk; mark_active and
    # promote/demote are exempt (they operate on segments by status, not by
    # round membership — blocking them mid-round silently broke the only
    # salvage path for background segments).
    if (
        in_round
        and target not in (rd or {}).get("pending", [])
        and action not in ("mark_active", "promote", "demote")
    ):
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
    demote_completed_round = False
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
        # a demoted segment can never be dealt again — if it sits in the
        # active round, drop it so the round doesn't strand on it (option-B
        # fix 2026-09-29; counted as done like a drift drop, NOT as skip:
        # the user didn't decide 无需制卡, they moved it out of schedule).
        if isinstance(rd, dict) and target in rd.get("pending", []):
            rd["pending"] = [p for p in rd.get("pending", []) if p != target]
            rd["done"] = rd.get("done", 0) + 1
            if not rd["pending"]:
                rd["status"] = "complete"
                demote_completed_round = True
    seg["updated_at"] = datetime.now().isoformat(timespec="seconds")

    round_complete = False
    if in_round and action in ("complete", "skip", "next"):
        stats_key = {"complete": "done", "skip": "skipped", "next": "next"}[action]
        stats = rd.setdefault("stats", {})
        stats[stats_key] = stats.get(stats_key, 0) + 1
        round_complete = _drop_and_maybe_complete()
    if demote_completed_round:
        round_complete = True
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
        if _flow_read():
            raise HTTPException(status_code=409, detail="use the whole-round end-reading endpoint")
        data = _reading_read()
        stats = (data.get("round") or {}).get("stats", {})
        data["round"] = None
        _reading_write(data)
        return {"ok": True, "stats": stats}


# ---- persisted whole-round coordinator (independent of both schedulers) ----
from flow import create as _flow_create, reconcile as _flow_reconcile, replace_slot as _flow_replace_slot, answer_count as _answer_count
FLOW_FILE = STATE_DIR / 'flow.json'


def _flow_read():
    if not FLOW_FILE.exists():
        return None
    # Fail closed on corruption: never redeal over an unreadable coordinator.
    try:
        flow = json.loads(FLOW_FILE.read_text())
        if not isinstance(flow, dict) or flow.get('status') not in ('initializing','active','complete','exiting'):
            raise ValueError('invalid flow status')
        if flow['status'] in ('active','complete') and (flow.get('policy') != 'even-floor-v1' or not isinstance(flow.get('slots'), list) or not isinstance(flow.get('card_ids'), list)):
            raise ValueError('invalid or unsupported flow policy')
        return flow
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail='flow state unreadable; restore its backup') from exc


def _flow_write(flow):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if flow is None:
        FLOW_FILE.unlink(missing_ok=True)
        return
    tmp = FLOW_FILE.with_suffix('.tmp')
    with tmp.open('w') as stream:
        json.dump(flow, stream)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(FLOW_FILE)
    fd = os.open(STATE_DIR, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


async def _flow_initialize(mode, legacy=False):
    """Write intent before dealing; recover an interrupted deal from its queue."""
    f = _flow_read()
    if not f or f.get('status') == 'complete':
        f = {'status':'initializing', 'mode':mode, 'legacy':legacy}
        _flow_write(f)
    if f.get('status') != 'initializing':
        return
    if READING_MODE and not f.get('reading_ready'):
        data = _reading_read()
        if f.get('fresh') and data.get('round') == f.get('previous_reading'):
            data['round'] = None
            _reading_write(data)
        if not isinstance(data.get('round'), dict):
            await _reading_start_impl(mode)
        f['reading_ready'] = True
        _flow_write(f)
    if not f.get('cards_ready'):
        existing = _round_read()
        if existing is None or (f.get('fresh') and existing == f.get('previous_cards')):
            batch = await _start_session_impl(False, mode, render=False)
            if not batch['cards']:
                await _write_new_round({'status':'complete','created':datetime.now().isoformat(timespec='seconds'),
                                        'total':0,'done_count':0,'pending':[],'new_count':0,'mode':mode})
        f['cards_ready'] = True
        _flow_write(f)
    data = _reading_read() if READING_MODE else {}
    r = data.get('round') or {}
    c = _round_read() or {}
    flow = _flow_create(r.get('pending',[]), c.get('pending',[]), r.get('id',r.get('created')), c.get('id',c.get('created')), f.get('legacy',False))
    flow['reading_stats_base'] = dict(r.get('stats',{}))
    _flow_write(flow)


async def _flow_state_impl(adopt=True, *, current_only=False):
    f = _flow_read()
    if f and f.get('status') == 'exiting':
        await _flow_exit_impl()
        f = None
    if f and f.get('status') == 'initializing':
        await _flow_initialize(study_mode(f.get('mode')), f.get('legacy',False))
        f = _flow_read()
    if not f and adopt:
        r = (_reading_read(preserve_round=True).get('round') or {}) if READING_MODE else {}
        c = _round_read() or {}
        if r.get('status') == 'active' or c.get('status') == 'active':
            await _flow_initialize(study_mode(c.get('mode') or r.get('mode')), True)
            f = _flow_read()
    # Validate both queues before choosing whether to render a recall card.
    # A withdrawal can change reading -> review in this very response; using
    # the persisted phase here otherwise returns REVIEW with no presented card.
    reading_state = _reading_current_round() if current_only or f else None
    if f:
        r = (reading_state[0].get('round') or {}) if READING_MODE else {}
        c = current_round() or {}
        if c.get('pending'):
            await restore_round_cards(c, current_only=True, render=False)
        _flow_reconcile(f, r.get('pending', []), c.get('pending', []),
                        answered_count=_answer_count(c))
    # Refresh global counts once at the final landing screen, not per grade.
    lightweight = (current_only or bool(f)) and (current_round() or {}).get('status') != 'complete'
    d = await _session_state_impl(current_only=lightweight,
                                  render_card=not f or f.get('phase') == 'review',
                                  reading_state=reading_state)
    if not f:
        return {**d, 'flow':None}
    r = (_reading_read().get('round') or {}) if READING_MODE else {}
    c = _round_read() or {}
    _flow_reconcile(f,r.get('pending',[]),c.get('pending',[]), answered_count=_answer_count(c))
    stats = r.get('stats') or f.get('reading_stats_base',{})
    f['stats'] = {'readingDone':stats.get('done',0),'readingSkipped':stats.get('skipped',0),
                  'readingNext':stats.get('next',0),'reviewed':_answer_count(c),
                  'newReviewed':c.get('new_answered',0)}
    _flow_write(f)
    return {**d, 'flow':f}


@app.get('/api/flow/state')
async def flow_state():
    async with _state_lock:
        return await _flow_state_impl()


@app.post('/api/flow/start')
async def flow_start(mode: str | None = None):
    async with _state_lock:
        f = _flow_read()
        if f and f.get('status') in ('initializing', 'exiting'):
            return await _flow_state_impl()
        if f:
            # Queues may have changed since the last state poll (especially
            # undo from another device). Reconcile before declaring a fresh start.
            wire = await _flow_state_impl(adopt=False)
            f = wire['flow']
            if f['status'] != 'complete':
                return wire
        r = (_reading_read(preserve_round=True).get('round') if READING_MODE else None)
        c = _round_read()
        legacy = not f and ((r or {}).get('status') == 'active' or (c or {}).get('status') == 'active')
        if f or (not legacy and (r or c)):
            # Persist prior queue identities before replacing either one.
            # A retry distinguishes "not dealt yet" from "deal committed".
            # Completed legacy tombstones also need a fresh deal.
            _flow_write({'status':'initializing','mode':study_mode(mode),'legacy':False,
                         'fresh':True,'previous_reading':r,'previous_cards':c})
        await _flow_initialize(study_mode(mode), legacy)
        return await _flow_state_impl()


@app.post('/api/flow/end-reading')
async def flow_end_reading():
    async with _state_lock:
        await _flow_state_impl()
        if READING_MODE:
            data = _reading_read(); r = data.get('round') or {}
            n = len(r.get('pending',[]))
            stats = r.setdefault('stats',{})
            stats['skipped'] = stats.get('skipped',0)+n
            r.update(pending=[],status='complete',done=r.get('done',0)+n)
            data['round']=r; _reading_write(data)
        return await _flow_state_impl()


async def _flow_exit_impl():
    # Commit the existing journal locally before clearing business queues.
    # Unresolved native recovery rejects this without discarding either queue.
    if _pylib is not None:
        await anki('engineCommitUndo')
    _round_write(None)
    if READING_MODE:
        data = _reading_read(); data['round'] = None; _reading_write(data)
    _flow_write(None)


@app.post('/api/flow/exit')
async def flow_exit():
    async with _state_lock:
        _flow_read()  # Fail closed if existing state is corrupt.
        _flow_write({'status':'exiting'})
        await _flow_exit_impl()
        return {'ok':True}


# Stale-coordinate sentinel: the client's view of the parent segment no longer
# matches the file on disk (an in-app edit shifted later segments, or the .md
# was edited externally). The frontend catches this prefix, re-fetches the
# round, and asks the user to re-select — it must NOT silently retry, because
# the selection's line numbers were computed against the OLD rendering.
SPLIT_STALE = "stale: 片段坐标已过期，请刷新后重新选择"


class ReadingSplitBody(BaseModel):
    path: str
    seg_id: int
    # 1-based inclusive file ranges. Selected children are todo; prefix
    # and middle gaps are background; a worthwhile unread tail stays todo.
    # The source file is untouched.
    selections: list[dict]
    # Compatibility input for old clients only; never drives split behavior.
    # Reject obsolete extract/unknown requests during validation, before writes.
    gap_policy: Literal["bookmark"] = "bookmark"
    # Stale-coordinate guard (2026-09-30): the client echoes the parent's
    # fingerprint + line_start as it saw them when the chunk was dealt. If
    # either differs from the segment's CURRENT (post-reanchor) state, the
    # selection's absolute line numbers can't be trusted → 409 SPLIT_STALE.
    # Both OPTIONAL: an old client / a trace chunk without them skips that
    # half of the guard (backwards compatible).
    fingerprint: str | None = None
    line_start: int | None = None


@app.post("/api/reading/split")
async def reading_split(body: ReadingSplitBody):
    """Recursive split: parent → container + 2k+1 children (round 4).

    Rules (bookmark splitting):
      - parent keeps its seg_id and EXACT range forever → cards already
        anchored to it stay valid (provenance never drifts on split);
      - parent becomes `container` (never dealt, read-only history);
      - children get fresh seg_ids, parent_seg_id = parent;
      - selected ranges → todo (scheduled);
      - prefix and middle gaps → background; worthwhile unread tail →
        todo, deferred until a future round (segment-level gating);
      - a child can be split again — recursion is just interval refinement.
    """
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="file not in the reading list")
        text = _read_note_text(body.path)
        if text is None:
            raise HTTPException(status_code=404, detail="note file not found")
        lines = text.split("\n")
        # Re-anchor on external drift BEFORE touching coordinates (2026-09-30):
        # split used to trust the stored start_line against a freshly-read
        # file, so an external .md edit silently misaligned every cut. Now we
        # reconcile segments against the current text first (pure shifts
        # re-anchor cleanly via fingerprint); a content edit that can't
        # relocate flags needs_resync.
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if entry.get("file_sha") != sha:
            _reanchor_entry(entry, lines, sha)
        if entry.get("needs_resync"):
            _reading_write(data)  # persist the re-anchor/needs_resync flag
            raise HTTPException(status_code=409, detail="file needs reseed first")
        segs = entry.get("segments") or []
        parent = next(
            (s for s in segs if s.get("seg_id") == body.seg_id), None
        )
        if parent is None:
            _reading_write(data)
            raise HTTPException(status_code=404, detail="segment not found")
        if parent.get("status") == "container":
            _reading_write(data)
            raise HTTPException(status_code=409, detail="segment already split")
        # Stale-coordinate guard (2026-09-30): the client echoes the parent
        # fingerprint + line_start it rendered against. After the re-anchor
        # above, a mismatch means the segment moved or changed since the card
        # was dealt (in-app edit shifted it, or external drift relocated it) —
        # the selection's absolute lines are computed against the OLD layout,
        # so cutting now would slice the wrong text. Refuse; the frontend
        # re-fetches and asks the user to re-select.
        if (
            body.fingerprint is not None
            and parent.get("fingerprint", "") != body.fingerprint
        ):
            _reading_write(data)
            raise HTTPException(status_code=409, detail=SPLIT_STALE)
        if (
            body.line_start is not None
            and int(parent.get("start_line", 0)) != int(body.line_start)
        ):
            _reading_write(data)
            raise HTTPException(status_code=409, detail=SPLIT_STALE)
        p_start, p_end = parent["start_line"], parent["end_line"]
        # normalize + validate selections inside the parent range
        sels = []
        sels_raw = False  # any selection intersected the parent before trim
        for sel in body.selections:
            try:
                a = int(sel.get("start_line"))  # type: ignore[arg-type]
                b = int(sel.get("end_line"))    # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="selections need int start_line/end_line")
            if a < 1 or b < a or b > len(lines):
                raise HTTPException(status_code=400, detail=f"selection out of file range: {a}-{b}")
            a = max(a, p_start)
            b = min(b, p_end)
            if a > b:
                continue
            sels_raw = True
            # trim leading/trailing blank lines off the selection (markdown-it
            # list/paragraph maps carry trailing blank lines; cutting them in
            # yields a child that starts/ends on whitespace).
            while a <= b and not lines[a - 1].strip():
                a += 1
            while b >= a and not lines[b - 1].strip():
                b -= 1
            if a <= b:
                sels.append((a, b))
        sels.sort()
        merged: list[tuple[int, int]] = []
        for a, b in sels:
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        if not merged:
            # distinguish "the selection was only blank lines" (the
            # 「切出来完全空」 report — trimmed away above) from "the range
            # didn't intersect the parent at all"
            if sels_raw:
                raise HTTPException(
                    status_code=400,
                    detail="选区没有实质内容（只有空行）",
                )
            raise HTTPException(status_code=400, detail="no valid selection inside the segment")
        # Reject an empty / heading-only selection: without this a cut landing
        # between paragraphs (the "切出来完全空" report) builds a child that
        # renders blank and queues as a pointless card. The selection's own
        # body must clear MIN_READING_BODY, same bar a whole-file seed and the
        # bookmark tail already meet.
        sel_text = "\n".join(
            "\n".join(lines[a - 1 : b]) for a, b in merged
        )
        if not _reading_worthwhile({"text": sel_text}):
            raise HTTPException(
                status_code=400,
                detail="选区没有实质内容（太短或只有空行/标题）",
            )
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
            # Unread tail is deferred; empty/heading-only/short tails sink.
            tail_text = "\n".join(lines[cursor - 1 : p_end])
            tail_status = "todo" if _reading_worthwhile({"text": tail_text}) else "background"
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
        # 「之后再推」— it stays in the FILE queue and the frontier/depth
        # passes deal it from the NEXT round on (segment-level gate, option
        # B 2026-09-29: the selection's cards no longer hold it back),
        # NOT in this round.
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
                flow = _flow_read()
                if flow:
                    _flow_replace_slot(flow, {"path":body.path,"chunk_key":key}, todo_children)
                    _flow_write(flow)
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
    expected_revision: str
    expected_fingerprint: str


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
        revision = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if (body.expected_revision != revision or entry.get('file_sha') != revision
            or body.expected_fingerprint != seg.get('fingerprint')):
            raise HTTPException(status_code=409, detail='内容已被其他编辑器或外部文件修改；草稿已保留，请重新打开后合并')
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
    their status; every uncovered line range is rebuilt as ONE fresh `todo`
    segment (whole-file seeding, 2026-09-24 — the chunker no longer tiles).
    Cards on lost segments stay anchored to stale containers — provenance
    never deleted, per round-4 rule.
    """
    async with _state_lock:
        _reading_guard()
        data = _reading_read()
        entry = _find_entry(data, body.path)
        if entry is None:
            raise HTTPException(status_code=404, detail="file not in the reading list")
        text = _read_note_text(body.path)
        if text is None:
            raise HTTPException(status_code=404, detail="note file not found")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
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
        now = datetime.now().isoformat(timespec="seconds")
        n = len(lines)
        # whole-file re-seeding (2026-09-24): every maximal contiguous run of
        # UNCOVERED lines becomes ONE fresh todo segment (no chunker tiling).
        # Runs that are pure whitespace/heading-only are dropped (same body
        # filter the chunker applied) so we never deal an empty card.
        line_no = 1
        while line_no <= n:
            if line_no in covered:
                line_no += 1
                continue
            run_start = line_no
            while line_no <= n and line_no not in covered:
                line_no += 1
            run_end = line_no - 1  # inclusive
            run_text = "\n".join(lines[run_start - 1 : run_end])
            if not _reading_worthwhile({"text": run_text}):
                continue
            title = _file_title(lines[run_start - 1 : run_end], body.path)
            fresh.append({
                "seg_id": nid,
                "start_line": run_start,
                "end_line": run_end,
                "fingerprint": _seg_fingerprint(lines, run_start, run_end),
                "first_line": lines[run_start - 1] if run_start - 1 < n else "",
                "title": title,
                "heading_path": [],
                "status": "todo",
                "parent_seg_id": None,
                "updated_at": now,
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
                    "fingerprint": s.get("fingerprint", ""),
                    "edit_revision": entry.get("file_sha", ""),
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
    data = await _read_upload_bounded(file, UPLOAD_MAX_BYTES, file.filename or "image")
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
def _positive_upload_limit(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f'{name} must be a positive integer') from exc
    if value <= 0:
        raise RuntimeError(f'{name} must be a positive integer')
    return value

UPLOAD_MAX_BYTES = _positive_upload_limit('ANKI_UPLOAD_MAX_BYTES', 25 * 1024 * 1024)
UPLOAD_BATCH_MAX_BYTES = _positive_upload_limit('ANKI_UPLOAD_BATCH_MAX_BYTES', 100 * 1024 * 1024)
UPLOAD_MAX_FILES = _positive_upload_limit('ANKI_UPLOAD_MAX_FILES', 100)

async def _read_upload_bounded(upload, limit, label):
    data = bytearray()
    while True:
        chunk = await upload.read(min(65536, limit - len(data) + 1))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f'上传预算超限（最多 {limit} 字节）：{label}')



@app.post("/api/media/upload")
async def media_upload(file: UploadFile = File(...)):
    """Store an uploaded file in the Anki media collection via AnkiConnect.

    Returns the stored filename; the editor inserts it as <img src="name">
    (Anki media references are bare filenames, served back at /media/).
    """
    data = await _read_upload_bounded(file, UPLOAD_MAX_BYTES, file.filename or "image")
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



