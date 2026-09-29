# ir4anki — Incremental Reading for Anki

A self-hosted review web app that adds **incremental reading** (渐进制卡) and a
**preview gate** (先看后考) on top of your existing Anki collection. FastAPI
backend + SolidJS/Material Design 3 frontend, driven entirely through
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) — no Anki fork, no
database migration, no add-on code inside Anki.

You keep reviewing in desktop Anki as usual; ir4anki is an *extra* front door
for the daily flow below. Cards always live in your collection and move
between your devices via Anki's own sync.

## What it adds

### One daily flow (早/中/晚, one button)
A single **开始** button runs the whole day: it deals reading segments first,
then a review batch, then returns to the start screen — no intermediate
pick/stats pages. The review count is **dynamic**: ⌈D/3⌉ where D is the day's
due-card count, snapshotted at the first review deal of the Anki day (4 AM
rollover), so three rounds (morning/noon/evening) clear the day's due pile
evenly. New cards reach the review batch through the overnight release below.
Stages with nothing to do are skipped automatically. Every number is
env-overridable — see [`deploy/ir4anki.env.example`](deploy/ir4anki.env.example).

### Reading mode (渐进制卡 — incremental reading)
Point it at a folder of markdown notes (`ANKI_NOTES_DIR`). Each listed file is
dealt as ONE whole-file segment; you consume it by recursive **bookmark
splits** — read a bit, cut out what's worth carding, the unread tail stays
queued. For each segment you write cards by hand (QA or cloze, one-click into
Anki). Segment state (todo → active → done/skipped), the segment tree, and
**exact card provenance** (which segment produced which cards) live in a local
sqlite DB — the .md files stay pure text with zero markers. While reviewing any
card later, the app can show the original source segment with full-file context.

- **Segment editor** — edit a segment's text in-app (CodeMirror 6 source +
  live markdown preview). Saves atomically rewrite the .md and re-anchor every
  other segment in the same transaction, so line shifts never corrupt the
  segment tree or trip the drift fuse.
- **Images in notes** — upload images from the editor; they're stored under
  `NOTES_DIR/_assets` and referenced by bare filename, so the .md stays clean
  and your notes folder stays relocatable (git-friendly).
- **文件 browser** — a read-only folder-tree view of the whole corpus with
  rendered markdown/KaTeX, separate from the 阅读清单 (which controls what
  gets dealt and in what priority).

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

### Segment-level gate (per-segment cooldown, 2026-09-29)
A segment whose cards are still sitting in the preview pool is held overnight:
it won't be re-dealt the same day (grading cards you just made is recognition,
not recall). Crucially it holds **only itself** — the rest of its file keeps
dealing normally, so one half-finished chunk never blocks your reading
frontier. Segments without cards are never gated.

### Friendly to desktop Anki
Every AnkiConnect call is serialized through one queue, and the background
sync that follows each answer is throttled (at most one sync per
`ANKI_SYNC_MIN_INTERVAL` seconds, default 120) — so ir4anki answering never
stutters your open Anki window. Slow calls are logged
(`journalctl --user -u ir4anki`).

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

### First-run checklist

Before the daily flow works end to end, confirm:

1. **Desktop Anki is RUNNING** with the AnkiConnect add-on installed
   (`2055492159`) — ir4anki drives your collection through it. If Anki is
   closed the app shows an Anki-connection error on every action.
2. **`ANKI_PREVIEW_RELEASE_DECK` (default `2026`) exists in YOUR collection**
   — that's where released new cards land. Rename it in
   `~/.config/ir4anki.env` if your main deck is called something else.
3. **`ANKI_ADD_MODEL` / `ANKI_ADD_CLOZE_MODEL` note types exist** (defaults
   `问答题` / `填空题`) — the add-card dialogs create notes with them.
   Either create matching note types or point the env keys at yours.
4. **`ANKI_MEDIA_DIR` points at your profile's `collection.media`** — only
   needed for card images; the installer prompts for it.
5. **`ANKI_ROLLOVER_HOUR` matches Anki's own "next day starts at"** setting
   (Preferences → Scheduling), or daily budgets reset at the wrong moment.
6. Reading mode only: put markdown notes in `ANKI_NOTES_DIR` and add files
   via the 阅读清单 section; without them the chain skips straight to review.

Useful flags: `--non-interactive` (accept defaults), `--no-service` (build +
config only, run uvicorn yourself).

### Desktop-first UI

The layout is designed for desktop widths (works from 1180px up): card in the
center column, related cards / source note in a side column. On small panels
(e.g. 1280×800) run the browser full-screen (F11) or kiosk-style
(`chromium --kiosk http://127.0.0.1:8901`) to give the layout the whole
screen; the action bar stays pinned at the bottom. Keyboard first: `Space`
reveal, `1`–`4` rate (Again/Hard/Good/Easy), `Ctrl+Z` undo; reading stage adds
`A` add card, `C` cloze, `S` skip, `N` next.

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
| `ANKI_PREVIEW_RELEASE_DECK` | `2026` | where released cards land |
| `ANKI_ADD_MODEL` / `ANKI_ADD_CLOZE_MODEL` | `问答题` / `填空题` | note types used by the add-card dialogs — must exist in your collection |
| `ANKI_ROLLOVER_HOUR` | `4` | must match Anki's own "next day starts at" |
| `ANKI_RELEASE_DAILY_GOAL` | `45` | max auto-released cards per day |

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
deploy/     install.sh + update.sh + systemd unit template + env example
scripts/    API/E2E/UI tests (some need playwright + a live AnkiConnect)
docs/       design documents (reading mode, round-4 segment identity)
```

## Tests

```bash
# pure-API tests (need backend deps; some need live AnkiConnect)
backend/.venv/bin/python scripts/reading_test.py
backend/.venv/bin/python scripts/auto_release_test.py
backend/.venv/bin/python scripts/reading_edit_test.py
backend/.venv/bin/python scripts/gate_segment_level_test.py
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
