#!/usr/bin/env bash
# ir4anki updater — pull the latest release and rebuild only what changed.
# Safe to run while the service is up (restart is ~1s; state lives outside
# the repo in ~/.local/state/ir4anki and ~/.config/ir4anki.env).
#
# Usage: bash deploy/update.sh   (from anywhere)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

say() { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }

say "git pull"
if [ -n "$(git status --porcelain)" ]; then
    echo "WARN: local changes detected — pulling anyway (ff-only will fail if they conflict)"
fi
BEFORE="$(git rev-parse HEAD)"
git pull --ff-only
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "already up to date ($(git rev-parse --short HEAD))"
    exit 0
fi

echo "updated $(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER"):"
git log --oneline "$BEFORE..$AFTER" | sed 's/^/  /'

CHANGED="$(git diff --name-only "$BEFORE" "$AFTER")"

if grep -q '^backend/requirements.txt$' <<<"$CHANGED"; then
    say "backend deps changed — updating venv"
    VENV="$REPO/backend/.venv"
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$VENV/bin/python" -r backend/requirements.txt
    else
        "$VENV/bin/pip" install -r backend/requirements.txt
    fi
fi

if grep -q '^frontend/' <<<"$CHANGED"; then
    say "frontend changed — rebuilding dist"
    (cd frontend && npm ci && npm run build)
fi

say "restart service"
if systemctl --user list-unit-files ir4anki.service >/dev/null 2>&1 \
   && systemctl --user cat ir4anki.service >/dev/null 2>&1; then
    systemctl --user restart ir4anki.service
    sleep 1
    if systemctl --user is-active --quiet ir4anki.service; then
        echo "service active"
    else
        echo "WARN: service failed to restart — journalctl --user -u ir4anki -n 30" >&2
        exit 1
    fi
else
    echo "no ir4anki systemd service found — if you run uvicorn manually, restart it yourself"
fi

say "done — hard-refresh the browser tab (Ctrl+Shift+R)"
