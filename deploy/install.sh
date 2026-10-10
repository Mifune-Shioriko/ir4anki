#!/usr/bin/env bash
# New installs use official pylib; --backend connect keeps the rollback path.
# Existing env files are retained. Run this script yourself for machine changes.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$HOME/.config/ir4anki.env"
UNIT_FILE="$HOME/.config/systemd/user/ir4anki.service"
INTERACTIVE=1
WANT_SERVICE=1
ANKI_BACKEND=pylib
COLLECTION_ARG=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --non-interactive) INTERACTIVE=0 ;;
        --no-service) WANT_SERVICE=0 ;;
        --backend) [ "$#" -ge 2 ] || exit 2; ANKI_BACKEND="$2"; shift ;;
        --collection) [ "$#" -ge 2 ] || exit 2; COLLECTION_ARG="$2"; shift ;;
        --help|-h)
            printf '%s\n' 'Usage: bash deploy/install.sh [--backend pylib|connect] [--collection /absolute/profile/collection.anki2] [--non-interactive] [--no-service]' 'New installs default to pylib; existing environment files are NEVER overwritten.' 'Close desktop Anki before native ownership; configure sync credentials locally.'
            exit 0 ;;
        *) printf 'Unknown flag: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done
[ "$ANKI_BACKEND" = pylib ] || [ "$ANKI_BACKEND" = connect ] || { printf 'backend must be pylib or connect\n' >&2; exit 2; }
say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
ask() {
    local prompt="$1" variable="$2" default="$3" line=""
    if [ "$INTERACTIVE" -eq 1 ]; then printf '%s [%s]: ' "$prompt" "$default"; read -r line || true; fi
    printf -v "$variable" '%s' "${line:-$default}"
}
say 'Prerequisites'
command -v python3 >/dev/null || die 'Python >=3.10 required'
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || die 'Python >=3.10 required'
command -v node >/dev/null || die 'Node >=18 required'
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' || die 'Node >=18 required'
command -v npm >/dev/null || die 'npm required'
say 'Backend dependencies (official anki==25.2.7)'
VENV="$REPO/backend/.venv"
if command -v uv >/dev/null; then
    [ -d "$VENV" ] || uv venv "$VENV" --python python3
    uv pip install --python "$VENV/bin/python" --index-url https://pypi.org/simple -r "$REPO/backend/requirements.txt"
else
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --index-url https://pypi.org/simple -r "$REPO/backend/requirements.txt"
fi
"$VENV/bin/python" -c 'import fastapi,httpx,uvicorn; from importlib.metadata import version; assert tuple(map(int,version("anki").split(".")))==(25,2,7)' || die 'Pinned backend dependencies failed'
say 'Machine-local config'
if [ -f "$ENV_FILE" ]; then
    printf 'Keeping existing %s unchanged. Backend selection flags do not rewrite it.\n' "$ENV_FILE"
else
    PROFILES=()
    BASE="$HOME/.local/share/Anki2"
    if [ -d "$BASE" ]; then
        for profile in "$BASE"/*/; do
            [ ! -f "${profile}collection.anki2" ] || PROFILES+=("${profile}collection.anki2")
        done
    fi
    COLLECTION_DEFAULT="$COLLECTION_ARG"
    if [ "${#PROFILES[@]}" -eq 1 ] && [ -z "$COLLECTION_DEFAULT" ]; then COLLECTION_DEFAULT="${PROFILES[0]}"; fi
    if [ "${#PROFILES[@]}" -gt 1 ]; then printf 'Select one of these collections explicitly:\n'; printf '  %s\n' "${PROFILES[@]}"; fi
    if [ "$ANKI_BACKEND" = pylib ]; then
        ask 'Existing collection.anki2 (absolute path)' ANKI_COLLECTION_PATH "$COLLECTION_DEFAULT"
        [ -n "$ANKI_COLLECTION_PATH" ] && [ -f "$ANKI_COLLECTION_PATH" ] || die 'Select an existing collection using --collection; no blank profile is created'
        [[ "$ANKI_COLLECTION_PATH" = /* ]] || die 'Collection path must be absolute'
        ANKI_MEDIA_DIR="${ANKI_COLLECTION_PATH%.anki2}.media"
    else
        ask 'AnkiConnect URL' ANKICONNECT_URL 'http://127.0.0.1:8765'
        MEDIA_DEFAULT="${COLLECTION_DEFAULT%.anki2}.media"
        [ -n "$COLLECTION_DEFAULT" ] || MEDIA_DEFAULT="$BASE/User 1/collection.media"
        ask 'collection.media dir (absolute)' ANKI_MEDIA_DIR "$MEDIA_DEFAULT"
    fi
    ask 'Application state dir' ANKI_STATE_DIR "$HOME/.local/state/ir4anki"
    ask 'Markdown corpus dir' ANKI_NOTES_DIR "$HOME/anki-notes"
    ask 'Existing QA note type' ANKI_ADD_MODEL '问答题'
    ask 'Existing cloze note type' ANKI_ADD_CLOZE_MODEL '填空题'
    mkdir -p "$(dirname "$ENV_FILE")" "$ANKI_STATE_DIR" "$ANKI_NOTES_DIR"
    # Owner-run config generation, no sync passwords are requested or copied.
    umask 077
    {
        printf '%s\n' '# Machine-local config. See deploy/ir4anki.env.example.' 'IR4ANKI_HOST=127.0.0.1' 'IR4ANKI_PORT=8901'
        printf 'ANKI_BACKEND=%s\nANKI_MEDIA_DIR=%s\nANKI_STATE_DIR=%s\nANKI_NOTES_DIR=%s\n' "$ANKI_BACKEND" "$ANKI_MEDIA_DIR" "$ANKI_STATE_DIR" "$ANKI_NOTES_DIR"
        if [ "$ANKI_BACKEND" = pylib ]; then
            printf 'ANKI_COLLECTION_PATH=%s\nANKI_BACKUP_DIR=%s/anki-backups\nANKI_BACKUP_INTERVAL=1800\n' "$ANKI_COLLECTION_PATH" "$ANKI_STATE_DIR"
            printf '%s\n' '# Configure ANKI_SYNC_ENDPOINT and ANKI_SYNC_HKEY (or user/password) locally.'
        else printf 'ANKICONNECT_URL=%s\n' "$ANKICONNECT_URL"; fi
        printf 'ANKI_ADD_MODEL=%s\nANKI_ADD_CLOZE_MODEL=%s\n' "$ANKI_ADD_MODEL" "$ANKI_ADD_CLOZE_MODEL"
        printf '%s\n' 'ANKI_ROLLOVER_HOUR=4' 'ANKI_PREVIEW_MODE=1' 'ANKI_PREVIEW_DECK=预览池' 'ANKI_PREVIEW_RELEASE_DECK=2026' 'ANKI_RELEASE_DAILY_GOAL=36' 'ANKI_READING_MODE=1'
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi
# Only inspect nonsecret keys. Do NOT source the env as executable shell code.
SELECTED_BACKEND="$(python3 - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path
backend='connect'
for line in Path(sys.argv[1]).read_text().splitlines():
    key, sep, value=line.partition('=')
    if sep and key.strip()=='ANKI_BACKEND': backend=value.strip().strip('"').strip("'")
if backend not in ('pylib','connect'): raise SystemExit('Invalid ANKI_BACKEND')
print(backend)
PY
)"
if [ "$SELECTED_BACKEND" = pylib ]; then
    "$VENV/bin/python" "$REPO/deploy/native.py" check --env "$ENV_FILE" --require-offline || die 'Native preflight failed; fix paths/models and close desktop Anki'
fi
say 'Frontend production build'
(cd "$REPO/frontend" && npm ci && npm run build)
[ -f "$REPO/frontend/dist/index.html" ] || die 'No frontend build output'
if [ "$WANT_SERVICE" -eq 1 ] && command -v systemctl >/dev/null; then
    say 'Install/start one-worker systemd user service'
    mkdir -p "$(dirname "$UNIT_FILE")"
    python3 - "$REPO/deploy/ir4anki.service" "$UNIT_FILE" "$REPO" <<'PY'
import sys
from pathlib import Path
Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text().replace('@REPO@',sys.argv[3]))
PY
    systemctl --user daemon-reload
    systemctl --user enable ir4anki.service
    systemctl --user restart ir4anki.service
    if ! systemctl --user is-active --quiet ir4anki.service; then warn 'Check journalctl --user -u ir4anki -n 40'; exit 1; fi
else
    printf 'Service not changed. Run: %s/bin/uvicorn --app-dir %s/backend app:app --host 127.0.0.1 --port 8901\n' "$VENV" "$REPO"
fi
say 'Done'
printf 'Config: %s\nDocs: %s/docs/pylib-migration.md\n' "$ENV_FILE" "$REPO"
if [ "$SELECTED_BACKEND" = pylib ]; then
    printf '%s\n' 'No desktop/AnkiConnect prerequisite. Keep desktop CLOSED; configure native sync locally.' 'Verify: backend/.venv/bin/python deploy/native.py health'
else printf '%s\n' 'Connect rollback selected: desktop Anki must be RUNNING with AnkiConnect; configure its sync separately.'; fi
