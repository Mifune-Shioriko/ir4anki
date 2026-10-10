#!/usr/bin/env bash
# Explicit local update/build preparation. Never rewrites operator credentials.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL=0
RESTART=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --local) LOCAL=1 ;;
        --no-restart) RESTART=0 ;;
        --help|-h)
            printf '%s\n' 'Usage: bash deploy/update.sh [--local] [--no-restart]' '--local builds current code without pulling (including uncommitted changes).' '--no-restart prepares an isolated frontend build and DOES NOT deploy it or touch services.' 'Default: refuse dirty repo, pull ff-only, build, retain prior dist, restart service.'
            exit 0 ;;
        *) printf 'Unknown flag: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done
cd "$REPO"
say() { printf '\n== %s\n' "$*"; }
if [ "$LOCAL" -eq 0 ]; then
    if [ -n "$(git status --porcelain)" ]; then
        printf 'ERROR: local changes detected; refusing pull. Commit/stash yourself or use --local.\n' >&2
        exit 1
    fi
    BEFORE="$(git rev-parse HEAD)"
    say 'git pull'
    git pull --ff-only
    AFTER="$(git rev-parse HEAD)"
    if [ "$BEFORE" = "$AFTER" ]; then printf 'Already up to date.\n'; exit 0; fi
else
    say 'Build current local snapshot — no git pull'
fi
# For preparation, deps must already exist; no-restart must not mutate the live
# venv while an old process is running. Run dependency installation offline.
VENV="$REPO/backend/.venv"
if [ "$RESTART" -eq 1 ]; then
    say 'Pinned backend dependencies'
    if command -v uv >/dev/null; then
        uv pip install --python "$VENV/bin/python" --index-url https://pypi.org/simple -r backend/requirements.txt
    else
        "$VENV/bin/python" -m pip install --index-url https://pypi.org/simple -r backend/requirements.txt
    fi
fi
"$VENV/bin/python" -c 'import fastapi,httpx,uvicorn; from importlib.metadata import version; assert tuple(map(int,version("anki").split(".")))==(25,2,7)' || { printf 'Install pinned requirements with the service stopped before update.\n' >&2; exit 1; }
BUILD_DIR="$(mktemp -d -t ir4anki-build-XXXXXXXX)"
say 'Isolated frontend build'
# Reuse the lockfile-installed dependencies when preparing a running instance.
# A deliberate deploying update may refresh node_modules with npm ci.
if [ "$RESTART" -eq 1 ]; then (cd frontend && npm ci); fi
(cd frontend && npm run build -- --outDir "$BUILD_DIR")
[ -f "$BUILD_DIR/index.html" ] || { printf 'No build output.\n' >&2; exit 1; }
if [ "$RESTART" -eq 0 ]; then
    printf 'Prepared frontend: %s\nNo live frontend, venv or service was changed.\n' "$BUILD_DIR"
    exit 0
fi
# The operator explicitly invoked deployment. Preserve the old bundle rather
# than deleting it; a backend flag is not a collection/state rollback.
say 'Deploy bundle, retain previous output'
STAMP="$(date -u +%Y%m%dT%H%M%S)"
NEXT="$REPO/frontend/dist.next-$STAMP-$$"
mv "$BUILD_DIR" "$NEXT"
if [ -d "$REPO/frontend/dist" ]; then mv "$REPO/frontend/dist" "$REPO/frontend/dist.previous-$STAMP-$$"; fi
mv "$NEXT" "$REPO/frontend/dist"
say 'Restart user service'
if command -v systemctl >/dev/null && systemctl --user cat ir4anki.service >/dev/null 2>&1; then
    systemctl --user restart ir4anki.service
    if ! systemctl --user is-active --quiet ir4anki.service; then
        printf 'Service did not start. Check journalctl --user -u ir4anki -n 40. Previous bundle remains preserved.\n' >&2
        exit 1
    fi
else
    printf 'No user service found; restart your manually run one-worker backend yourself.\n'
fi
printf 'Done. Hard-refresh the browser (Ctrl+Shift+R). Environment config remains untouched.\n'
