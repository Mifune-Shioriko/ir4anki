"""Anki review web app — backend (v2).

Session model:
  - one round = a 5-card read-only PREVIEW lead-in (preview mode), then
    up to 10 review cards + up to 5 new cards (user spec 2026-09-03:
    先预览 5 张新卡，然后复习 10 + 5)
  - new cards capped at 5 per clock hour (quota tracked in state file)
  - 'continue' after a round serves more review cards (like normal Anki)
  - every answer triggers a fire-and-forget sync to the self-hosted server
  - round state persists in state/round.json: refreshing the page resumes
    the in-progress round instead of dealing a fresh one (GET /api/session/state)
"""
import asyncio
import base64
import json
import os
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
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

# Anki collection.media directory (served back at /media/<name>)
MEDIA_DIR = Path(os.getenv("ANKI_MEDIA_DIR", str(_REPO_ROOT / "collection.media")))
# built SolidJS frontend (served as the SPA; skipped if absent)
REVIEW_WEB_V2_DIST = Path(
    os.getenv("REVIEW_DIST_DIR", str(_REPO_ROOT / "frontend" / "dist"))
)
STATE_DIR = Path(os.getenv("ANKI_STATE_DIR", str(Path(__file__).parent / "state")))
QUOTA_FILE = STATE_DIR / "quota.json"
ROUND_FILE = STATE_DIR / "round.json"

REVIEW_PER_ROUND = 10  # + up to NEW_PER_HOUR new cards = 15 total per round
NEW_PER_HOUR = 5
ROUND_EXPIRE_HOURS = 24  # a half-finished round older than this is discarded

# ---- preview mode (先看后考: read new cards before they enter testing) ----
# Newly injected cards land in PREVIEW_DECK SUSPENDED, so they are invisible
# to the scheduler and to normal rounds. A preview round shows them
# question+answer together with NO grading; per card the user either
# approves (放行: move to RELEASE_DECK + unsuspend → joins the regular
# hourly new-card quota pool) or defers (tagged deferred-YYYYMMDD, hidden
# from today's preview rounds, resurfaces tomorrow).
#
# The whole feature is gated on ANKI_PREVIEW_MODE. With it unset, preview
# endpoints return 404 and status reports preview_mode=false — the frontend
# then renders the exact legacy UI (system-level rollback).
# Data-level rollback: preview cards are untouched NEW cards; unsuspend them
# and move them back into 2026 and the collection is exactly as before.
PREVIEW_MODE = os.getenv("ANKI_PREVIEW_MODE", "").lower() in ("1", "true", "yes", "on")
PREVIEW_DECK = os.getenv("ANKI_PREVIEW_DECK", "预览池")
RELEASE_DECK = os.getenv("ANKI_PREVIEW_RELEASE_DECK", "2026")
PREVIEW_PER_ROUND = int(os.getenv("ANKI_PREVIEW_PER_ROUND", "5"))
PREVIEW_FILE = STATE_DIR / "preview.json"

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


# ---- new-card hourly quota ----------------------------------------------

def hour_key() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H")


def quota_state() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    if QUOTA_FILE.exists():
        try:
            st = json.loads(QUOTA_FILE.read_text())
            if st.get("hour") == hour_key():
                return st
        except Exception:
            pass
    return {"hour": hour_key(), "served": 0}


def quota_add(n: int) -> dict:
    st = quota_state()
    st["served"] += n
    QUOTA_FILE.write_text(json.dumps(st))
    return st


def new_quota_left() -> int:
    return max(0, NEW_PER_HOUR - quota_state()["served"])


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
    info_by_id = {i["cardId"]: i for i in infos or []}
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


async def fetch_cards(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    infos = await anki("cardsInfo", {"cards": ids})
    # cardsInfo returns cards sorted by id — re-order to match the REQUEST
    # order (matters for preview-priority new cards and due-order batches)
    by_id = {i["cardId"]: i for i in infos or []}
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
    # enrich with similar cards + AI explanations in parallel (local lookups, fast)
    similars = await asyncio.gather(*(fetch_similar(c["question"]) for c in out))
    explanations = await asyncio.gather(*(fetch_explanation(c["noteId"]) for c in out))
    for card, sim, expl in zip(out, similars, explanations):
        card["similar"] = sim
        card["explanation"] = expl
    return out


async def build_batch(review_count: int, allow_new: bool, priority_new: list[int] | None = None):
    """Assemble one study batch.

    New cards are INTERLEAVED evenly among the review cards (like Anki's
    mixed new/review ordering). They used to be appended at the very end,
    so a user who stopped after the 10 reviews never reached them — that
    was the "never pushes new cards" bug (2026-08-26).

    priority_new: card ids approved in the preview lead-in that just
    finished — they get the new-card slots FIRST (still quota-limited), so
    the user is tested on exactly what they just read (先看后考闭环).
    """
    cards: list[dict] = []

    # review cards: due but not new, most overdue first
    due_ids = await anki("findCards", {"query": "is:due -is:new"}) or []
    if due_ids:
        infos = await anki("cardsInfo", {"cards": due_ids})
        infos.sort(key=lambda i: i["due"])
        cards += await fetch_cards([i["cardId"] for i in infos[:review_count]])

    # new cards within the hourly quota, spread evenly through the batch
    new_left = new_quota_left() if allow_new else 0
    if new_left > 0:
        new_ids = await anki("findCards", {"query": new_card_query()}) or []
        if priority_new:
            pool_set = set(new_ids)
            prio = [c for c in priority_new if c in pool_set]
            new_ids = prio + [c for c in new_ids if c not in set(prio)]
        take = min(new_left, len(new_ids))
        if take > 0:
            new_cards = await fetch_cards(new_ids[:take])
            n_reviews = len(cards)
            if n_reviews:
                for i, c in enumerate(new_cards):
                    # evenly spaced slots; +i accounts for earlier inserts
                    pos = round((i + 1) * n_reviews / (take + 1)) + i
                    cards.insert(min(len(cards), pos), c)
            else:
                cards += new_cards
            quota_add(take)

    due_remaining = len(await anki("findCards", {"query": "is:due -is:new"}) or [])
    new_total = len(await anki("findCards", {"query": new_card_query()}) or [])
    return cards, due_remaining, new_left if allow_new else 0, new_total


# ---- API ------------------------------------------------------------------

@app.get("/api/status")
async def status():
    try:
        due = await anki("findCards", {"query": "is:due -is:new"})
        new = await anki("findCards", {"query": new_card_query()})
        out = {
            "anki": "ok",
            "due_review": len(due or []),
            "new_total": len(new or []),
            "new_quota_left": new_quota_left(),
            "new_quota_total": NEW_PER_HOUR,
            "preview_mode": PREVIEW_MODE,
        }
        if PREVIEW_MODE:
            out["preview_pool"] = len(await preview_pool_ids() or [])
        return out
    except Exception as e:
        return {"anki": "error", "detail": str(e)}


@app.post("/api/session/start")
async def start_session():
    synced = await do_sync()  # do_sync acquires the lock itself
    # preview lead-in just finished? Deal its approved cards first (先看后考)
    priority = _consume_preview_approved() if PREVIEW_MODE else []
    cards, due_remaining, new_left, new_total = await build_batch(
        REVIEW_PER_ROUND, allow_new=True, priority_new=priority
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
            }
        )
    return {
        "synced": synced,
        "cards": cards,
        "due_remaining": due_remaining,
        "new_quota_left": new_quota_left(),
        "new_quota_total": NEW_PER_HOUR,
        "new_total": new_total,
    }


@app.get("/api/session/state")
async def session_state():
    """Page-load entry point: resume an unfinished round instead of auto-starting."""
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
    preview: dict = {"preview_mode": PREVIEW_MODE}
    if PREVIEW_MODE:
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
                    }
                else:
                    # round drained — keep the approved tombstone (if any)
                    # so the next review start still prioritizes it
                    approved = prd.get("approved") or []
                    _preview_write(
                        {"status": "complete", "approved": approved}
                        if approved
                        else None
                    )
        except Exception:
            preview["preview_pool"] = None

    rd = current_round()
    if rd is None:
        return {
            "state": "none",
            "due_remaining": due_left,
            "new_quota_left": new_quota_left(),
            "new_quota_total": NEW_PER_HOUR,
            "new_total": new_total,
            "can_undo": False,
            **preview,
        }

    if rd.get("status") == "complete" or not rd.get("pending"):
        return {
            "state": "complete",
            "done": rd.get("done_count", rd.get("total", 0)),
            "total": rd.get("total", 0),
            "new_in_batch": rd.get("new_count"),
            "due_remaining": due_left,
            "new_quota_left": new_quota_left(),
            "new_quota_total": NEW_PER_HOUR,
            "new_total": new_total,
            "can_undo": bool((rd.get("last") or {}).get("snap")),
            **preview,
        }

    cards = await restore_round_cards(rd)
    if rd.get("status") == "complete" or not cards:
        return {
            "state": "complete",
            "done": rd.get("done_count", 0),
            "total": rd.get("total", 0),
            "new_in_batch": rd.get("new_count"),
            "due_remaining": due_left,
            "new_total": new_total,
            "can_undo": bool((rd.get("last") or {}).get("snap")),
            **preview,
        }
    return {
        "state": "active",
        "cards": cards,
        "done": rd.get("done_count", 0),
        "total": rd.get("total", len(cards)),
        "new_in_batch": rd.get("new_count"),
        "due_remaining": due_left,
        "new_quota_left": new_quota_left(),
        "new_quota_total": NEW_PER_HOUR,
        "new_total": new_total,
        "can_undo": bool((rd.get("last") or {}).get("snap")),
        **preview,
    }


@app.post("/api/session/more")
async def session_more():
    """'Keep going' — next batch, reviews only (new-card quota already spent)."""
    cards, due_remaining, _, new_total = await build_batch(REVIEW_PER_ROUND, allow_new=False)
    if cards:
        _round_write(
            {
                "status": "active",
                "created": datetime.now().isoformat(timespec="seconds"),
                "total": len(cards),
                "done_count": 0,
                "pending": [c["cardId"] for c in cards],
                "new_count": sum(1 for c in cards if c["isNew"]),
            }
        )
    return {
        "cards": cards,
        "due_remaining": due_remaining,
        "new_quota_left": new_quota_left(),
        "new_quota_total": NEW_PER_HOUR,
        "new_total": new_total,
    }


@app.post("/api/session/finish")
async def session_finish():
    _round_write(None)
    synced = await do_sync()
    return {"synced": synced}


@app.post("/api/answer")
async def answer(card_id: int, ease: int):
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
    if infos:
        snap = {k: infos[0].get(k) for k in SNAPSHOT_FIELDS}

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
            round_info["new_quota_left"] = new_quota_left()
            round_info["new_quota_total"] = NEW_PER_HOUR

    fire_and_forget_sync()
    return {"answered": True, "round": round_info}


@app.post("/api/undo")
async def undo():
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

    cards = await fetch_cards([cid])
    if not cards:
        raise HTTPException(status_code=404, detail="card vanished after restore")

    # propagate the corrected scheduling to the sync server
    fire_and_forget_sync()
    return {"restored": True, "card": cards[0], "index": pos}


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


def _consume_preview_approved() -> list[int]:
    """Pop the tombstone of cards approved in the last preview lead-in.

    Called by /api/session/start so the fresh review round deals those
    cards as its new-card material (tested right after being read).
    Consuming removes the tombstone — a second start gets nothing.
    """
    rd = _preview_read()
    approved = (rd or {}).get("approved") or []
    if rd is not None and rd.get("status") == "complete":
        _preview_write(None)
    return approved


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


@app.get("/api/preview/state")
async def preview_state():
    """Entry point for the preview UI: pool size + in-progress round resume."""
    _preview_guard()
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
            }
        # round drained — keep the approved tombstone (if any)
        approved = rd.get("approved") or []
        _preview_write(
            {"status": "complete", "approved": approved} if approved else None
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
async def preview_start():
    """Deal one preview batch: oldest pool cards not deferred today."""
    _preview_guard()
    await do_sync()  # pull anything injected elsewhere
    pool = await preview_pool_ids()
    deferred = await _deferred_today_cards(pool)
    pending = [c for c in pool if c not in deferred][:PREVIEW_PER_ROUND]
    if not pending:
        return {"cards": [], "pool": len(pool), "available": 0}
    _preview_write(
        {
            "status": "active",
            "created": datetime.now().isoformat(timespec="seconds"),
            "total": len(pending),
            "done": 0,
            "pending": pending,
        }
    )
    return {
        "cards": await fetch_cards(pending),
        "pool": len(pool),
        "available": len(pool) - len(deferred) - len(pending),
    }


@app.post("/api/preview/act")
async def preview_act(card_id: int, action: str):
    """Per-card decision inside a preview round.

    approve (放行): move card to RELEASE_DECK and unsuspend — it becomes a
        regular new card subject to the hourly quota, AND is remembered as
        priority new material for the next study round (先看后考闭环:
        tested right after reading).
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
    if not infos:
        raise HTTPException(status_code=404, detail="card vanished")
    # single-level undo slot (mirrors the review round's "last"): enough to
    # reverse this exact act — deck+suspend for approve, note+tag for defer
    rd["last"] = {
        "cardId": card_id,
        "index": rd["pending"].index(card_id),
        "action": action,
        "note": infos[0]["note"],
    }
    if action == "approve":
        await anki("changeDeck", {"cards": [card_id], "deck": RELEASE_DECK})
        await anki("unsuspend", {"cards": [card_id]})
        rd.setdefault("approved", []).append(card_id)
    else:
        tag = "deferred-" + datetime.now().strftime("%Y%m%d")
        await anki("addTags", {"notes": [infos[0]["note"]], "tags": tag})

    # deck move / tag change must reach the sync server (and thus the
    # user's phone + desktop) — same coalesced sync the answer path uses
    fire_and_forget_sync()

    # pending BEFORE this act — needed for the tombstone so undo can
    # rehydrate the round exactly as it stood (返回上一张)
    pending_before = rd.get("pending", [])
    rd["pending"] = [c for c in rd["pending"] if c != card_id]
    rd["done"] = rd.get("done", 0) + 1
    if not rd["pending"]:
        # tombstone: keep the approved list so the next /api/session/start
        # deals those cards first; _consume_preview_approved() pops it.
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
            }
        )
        remaining = await preview_pool_ids()
        deferred = await _deferred_today_cards(remaining)
        return {
            "ok": True,
            "round_complete": True,
            "pool": len(remaining),
            "available": len(remaining) - len(deferred),
        }
    _preview_write(rd)
    return {"ok": True, "round_complete": False}


@app.post("/api/preview/finish")
async def preview_finish():
    """End the preview round; untouched cards stay suspended in the pool.

    Cards already approved this session are kept as a tombstone so a later
    review start still prioritizes them (preview-then-review funnel).
    """
    _preview_guard()
    rd = _preview_read()
    approved = (rd or {}).get("approved") or []
    if approved:
        _preview_write({"status": "complete", "approved": approved})
    else:
        _preview_write(None)
    return {"ok": True, "pool": await preview_pool_count()}


@app.post("/api/preview/undo")
async def preview_undo():
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
        rd["approved"] = [c for c in rd.get("approved", []) if c != cid]
    else:
        if info["type"] != 0 or PREVIEW_DECK not in info.get("deckName", ""):
            raise HTTPException(status_code=409, detail="card moved on")
        tag = "deferred-" + datetime.now().strftime("%Y%m%d")
        await anki(
            "removeTags", {"notes": [last.get("note") or info["note"]], "tags": tag}
        )

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
    if not infos:
        raise HTTPException(status_code=404, detail="card not found")
    note_id = infos[0]["note"]
    notes = await anki("notesInfo", {"notes": [note_id]})
    if not notes:
        raise HTTPException(status_code=404, detail="note not found")
    n = notes[0]
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
    if not infos:
        raise HTTPException(status_code=404, detail="card not found")
    note_id = infos[0]["note"]
    await anki(
        "updateNoteFields", {"note": {"id": note_id, "fields": body.fields}}
    )
    if body.tags is not None:
        await anki("updateNoteTags", {"note": note_id, "tags": body.tags})
    # hand back the re-rendered card so the UI refreshes without a reload
    fresh = await anki("cardsInfo", {"cards": [body.card_id]})
    f = fresh[0]
    return {
        "updated": True,
        "question": f["question"],
        "answer": f["answer"],
    }


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
    import uuid
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



