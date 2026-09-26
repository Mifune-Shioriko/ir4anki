# ir4anki — Incremental Reading for Anki

A self-hosted review web app that adds **incremental reading** (渐进制卡) and a
**preview gate** (先看后考) on top of your existing Anki collection. FastAPI
backend + SolidJS/Material Design 3 frontend, driven entirely through
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) — no Anki fork, no
database migration, no add-on code inside Anki.

```
┌──────────┐  AnkiConnect :8765  ┌─────────────┐   built-in sync   ┌──────────┐
│ desktop  │ ◄────────────────── │   ir4anki    │                   │ AnkiWeb / │
│ Anki     │   (cards, decks,    │  backend :8901│                   │ self-host │
│ (yours)  │    sync, media)     │  + SPA UI     │                   │ sync srv  │
└──────────┘                     └─────────────┘                   └──────────┘
```

You keep reviewing in desktop Anki as usual; ir4anki is an *extra* front door
for the daily flow below. Cards always live in your collection and move
between your devices via Anki's own sync.

## What it adds

### Reading mode (渐进制卡 — incremental reading)
Point it at a folder of markdown notes (`ANKI_NOTES_DIR`). Each listed file is
dealt as ONE whole-file segment; you consume it by recursive **bookmark
splits** — read a bit, cut out what's worth carding, the unread tail stays
queued. For each segment you write cards by hand (QA or cloze, one-click into
Anki). Segment state (todo → active → done/skipped), the segment tree, and
**exact card provenance** (which segment produced which cards) live in a local
sqlite DB — the .md files stay pure text with zero markers. While reviewing any
card later, the app can show the original source segment with full-file context.

### Preview pool (先看后考 — read before you're tested)
New cards don't hit the scheduler immediately. They land **suspended** in a
preview deck the moment you make them. The next day the backend
**auto-releases** every pool card whose note was created on an earlier Anki day
into your main deck unsuspended — so the first graded review happens after a
night's sleep, letting FSRS seed from real recall instead of a recognition
illusion. There is no manual approve/defer step: the overnight gap does the
work. A daily release cap (`ANKI_RELEASE_DAILY_GOAL`, default 45) limits how
many cards enter the queue per day — oldest first, overflow waits for the next
day — so a heavy card-making day can't blow up your review burden.

### One daily flow (早/中/晚, one button)
A single **开始** button runs the whole chain with no intermediate pick/stats
screens — each stage flows straight into the next, then returns to the start
screen:

```
阅读 (4 段)  →  复习 (15 新 + ⌈当日到期 / 3⌉)  →  开始页
```

The review count is **dynamic**: ⌈D/3⌉ where D is the day's due-card count,
snapshotted at the first review deal of the Anki day (4 AM rollover) into
`state/daily.json`. Three rounds (morning/noon/evening) therefore clear the
day's due pile evenly. Stages with nothing to do are skipped automatically
(list exhausted, no cards). Every number is env-overridable — see
[`deploy/ir4anki.env.example`](deploy/ir4anki.env.example).

## Requirements

- Linux (systemd user services) — the app itself is plain FastAPI/uvicorn and
  runs anywhere Python 3.10+ does, but `deploy/install.sh` assumes systemd
- Python ≥ 3.10, Node ≥ 18 (+ npm)
- Desktop **Anki running** with the **AnkiConnect** add-on (code `2055492159`),
  listening on its default `127.0.0.1:8765`
- Anki configured with its own sync target (AnkiWeb or a self-hosted sync
  server) — ir4anki never syncs anything itself; it triggers Anki's sync after
  each answer

## Install

```bash
git clone https://github.com/Mifune-Shioriko/ir4anki.git
cd ir4anki
bash deploy/install.sh
```

The installer will:

1. create `backend/.venv` and install Python deps (prefers `uv` if present)
2. `npm ci && npm run build` the frontend into `frontend/dist`
3. generate `~/.config/ir4anki.env` (prompts for your Anki profile's
   `collection.media` path, state dir, notes dir — all with defaults; never
   overwrites an existing file)
4. install + start the `ir4anki` systemd **user** service on `127.0.0.1:8901`
5. health-check the backend and probe AnkiConnect

Then open <http://127.0.0.1:8901>.

Useful flags: `--non-interactive` (accept defaults), `--no-service` (build +
config only, run uvicorn yourself).

### Manual run (no systemd)

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn --app-dir . app:app --host 127.0.0.1 --port 8901
# env vars from ~/.config/ir4anki.env are read by the service; export them
# yourself when running bare, or rely on the built-in defaults
```

### Keep it running after logout

systemd user services stop on logout unless lingering is enabled:

```bash
sudo loginctl enable-linger $USER
```

## Update

```bash
bash deploy/update.sh
```

Pulls the latest `main`, reinstalls backend deps only if `requirements.txt`
changed, rebuilds the frontend only if `frontend/` changed, then restarts the
service (~1s downtime). Your state (`~/.local/state/ir4anki`) and config
(`~/.config/ir4anki.env`) are never touched. Hard-refresh the browser tab
afterwards (Ctrl+Shift+R) to pick up a new frontend bundle.

## Configuration

Everything lives in `~/.config/ir4anki.env` (one KEY=VALUE per line). Full documentation
of every knob — ports, paths, deck names, note-type names, pacing sizes,
feature flags — is in [`deploy/ir4anki.env.example`](deploy/ir4anki.env.example).
After editing: `systemctl --user restart ir4anki`.

Defaults that most people will want to change:

| Key | Default | Meaning |
|---|---|---|
| `ANKI_MEDIA_DIR` | *(prompted)* | profile's `collection.media` — needed for card images |
| `ANKI_NOTES_DIR` | `~/anki-notes` | markdown corpus for reading mode |
| `ANKI_PREVIEW_DECK` | `预览池` | where suspended preview cards live |
| `ANKI_PREVIEW_RELEASE_DECK` | `2026` | where approved cards move |
| `ANKI_ADD_MODEL` / `ANKI_ADD_CLOZE_MODEL` | `问答题` / `填空题` | note types used by the add-card dialogs — must exist in your collection |
| `ANKI_ROLLOVER_HOUR` | `4` | must match Anki's own "next day starts at" |

## Multiple machines

Cards sync through Anki itself, so any number of machines can run ir4anki
against the same collection — **but the app state (round, quota, reading
progress) is machine-local by design** (`ANKI_STATE_DIR`). Running two
instances at once means two independent daily budgets and two reading
frontiers, which double-releases cards and splits progress. Pick ONE machine
as your ir4anki instance; on the others, just use desktop Anki.

## Layout

```
backend/    FastAPI app (app.py) + vendored chunker (legacy migrations only)
frontend/   SolidJS 1.9 + @material/web (MD3) SPA, built to frontend/dist
deploy/     install.sh + systemd unit template + env example
scripts/    API/E2E/UI tests (some need playwright + a live AnkiConnect)
docs/       design documents (reading mode, round-4 segment identity)
```

## Tests

```bash
# pure-API tests (need backend deps; some need live AnkiConnect)
backend/.venv/bin/python scripts/reading_test.py
backend/.venv/bin/python scripts/auto_release_test.py
backend/.venv/bin/python scripts/reading_edit_test.py
backend/.venv/bin/python scripts/modes_e2e.py
backend/.venv/bin/python scripts/sync_throttle_test.py
# UI tests additionally need: pip install playwright && playwright install chromium
backend/.venv/bin/python scripts/modes_ui_test.py
backend/.venv/bin/python scripts/reading_ui_test.py
```

Tests spawn throwaway backends on ports 8902/8903 with isolated state dirs and
fake or dead AnkiConnect endpoints — they never answer cards against the live
collection.

## Acknowledgements

Chunking logic adapted from a private notes-RAG experiment; the rest grew out
of a self-hosted Anki stack (sync server + headless Anki). The design docs in
`docs/` record the reasoning behind segment identity, the preview gate, and
the overnight-release policy.

## License

MIT — see [LICENSE](LICENSE).
