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
for the two workflows below. Cards always live in your collection and move
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

### Preview mode (先看后考 — read before you're tested)
New cards don't hit the scheduler immediately. They land **suspended** in a
preview deck; you read them in a low-stakes round (question shown, answer
hidden, no grading) and either approve (放行) or defer (明天再看). Approved
cards are unsuspended into your main deck **the next day** — the first graded
review happens after a night's sleep, so FSRS seeds from real recall instead of
a recognition illusion. A daily release budget caps how many cards enter the
queue per day.

### Pacing modes
Two per-round sizes: **quick** (碎片时间, 2 read + 5 preview + 5 new + 20
review) and **focus** (整块时间, 5 + 10 + 10 + 30). Chosen per round, survives
refresh; every number is env-overridable.

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
backend/.venv/bin/python scripts/new_again_test.py
backend/.venv/bin/python scripts/release_budget_test.py
backend/.venv/bin/python scripts/modes_e2e.py
backend/.venv/bin/python scripts/sync_throttle_test.py
# UI tests additionally need: pip install playwright && playwright install chromium
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
