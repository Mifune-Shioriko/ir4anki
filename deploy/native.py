#!/usr/bin/env python3
"""Read-only native preflight and operator-run offline migration snapshots.

Never opens the source with pylib, never starts/stops services, never selects
full sync, and never prints credential values. Run with backend/.venv/bin/python.
"""
import argparse
import datetime
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import urllib.request

PIN = '25.2.7'


def read_env(path):
    result = {}
    for line in Path(path).expanduser().read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not key.replace('_', '').isalnum():
            raise RuntimeError('Invalid environment assignment; edit the config locally')
        value = value.strip()
        if value[:1] in ('"', "'"):
            if value[-1:] != value[:1]:
                raise RuntimeError('Unclosed quote in environment config')
            value = value[1:-1]
        result[key] = value
    return result


def absolute_path(value, name):
    if not value or not Path(value).is_absolute():
        raise RuntimeError(f'{name} requires an absolute path')
    return Path(value).resolve()


def collection_info(path):
    if not path.is_file():
        raise RuntimeError('Anki collection path does not exist; no blank database will be created')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
        return {'notes': conn.execute('SELECT count(*) FROM notes').fetchone()[0],
                'cards': conn.execute('SELECT count(*) FROM cards').fetchone()[0],
                'schema': conn.execute('SELECT ver FROM col').fetchone()[0],
                'note_types': [r[0] for r in conn.execute('SELECT name FROM notetypes')]}


def desktop_processes():
    result = subprocess.run(['ps', '-eo', 'pid=,args='], capture_output=True, text=True, check=True)
    pids = []
    for line in result.stdout.splitlines():
        pieces = line.strip().split(None, 1)
        if len(pieces) != 2:
            continue
        pid, command = pieces
        words = command.split()
        executable = Path(words[0]).name
        if executable in ('anki', 'anki.exe', 'anki-launcher') or (executable.startswith('python') and '-m aqt' in command):
            pids.append(int(pid))
    return pids


def service_active(service):
    if shutil.which('systemctl') is None:
        raise RuntimeError('Cannot verify service state without systemctl; use manual offline backups')
    result = subprocess.run(['systemctl', '--user', 'is-active', service], capture_output=True, text=True)
    if result.stdout.strip() in ('active', 'activating', 'reloading', 'deactivating'):
        return True
    if result.returncode not in (0, 3, 4) or ('Failed to connect' in result.stderr):
        raise RuntimeError('Cannot verify user-service state; refusing an offline snapshot')
    return False


def check_environment(env_file):
    env = read_env(env_file)
    backend = env.get('ANKI_BACKEND', 'connect')
    if backend not in ('connect', 'pylib'):
        raise RuntimeError('ANKI_BACKEND must be connect or pylib')
    version = importlib.metadata.version('anki')
    try:
        pinned = tuple(int(part) for part in version.split('.')) == (25, 2, 7)
    except ValueError:
        pinned = False
    if not pinned:
        raise RuntimeError(f'Anki engine must be pinned to {PIN}; installed {version}')
    path = absolute_path(env.get('ANKI_COLLECTION_PATH'), 'ANKI_COLLECTION_PATH')
    info = collection_info(path)
    for name in ('ANKI_ADD_MODEL', 'ANKI_ADD_CLOZE_MODEL'):
        if env.get(name) and env[name] not in info['note_types']:
            raise RuntimeError(f'{name} does not match an existing note type')
    media = absolute_path(env.get('ANKI_MEDIA_DIR', str(path.with_suffix('.media'))), 'ANKI_MEDIA_DIR')
    if media != path.with_suffix('.media'):
        raise RuntimeError('ANKI_MEDIA_DIR must match the selected native collection')
    endpoint = env.get('ANKI_SYNC_ENDPOINT', '')
    credentials = bool(env.get('ANKI_SYNC_HKEY') or (env.get('ANKI_SYNC_USERNAME') and env.get('ANKI_SYNC_PASSWORD')))
    warnings = []
    if not endpoint or not credentials:
        warnings.append('Native sync is not configured; local use only until configured locally')
    if desktop_processes():
        warnings.append('Desktop Anki is running: close it before native ownership')
    if not media.is_dir():
        warnings.append('collection.media does not exist yet')
    if backend != 'pylib':
        warnings.append('Config still selects connect; this check does not switch it')
    return {'backend': backend, 'engine_version': PIN, 'engine_build_version': version, 'collection': info,
            'collection_path': str(path), 'media_path': str(media),
            'sync_configured': bool(endpoint and credentials), 'warnings': warnings}


def create_snapshot(env_file, output, *, offline_confirmed=False, service='ir4anki.service'):
    if not offline_confirmed:
        raise RuntimeError('Offline backup requires --offline-confirmed after stopping ir4anki and closing desktop Anki')
    if service_active(service):
        raise RuntimeError('Please stop ir4anki before taking the offline snapshot')
    if desktop_processes():
        raise RuntimeError('Close desktop Anki before taking the offline snapshot')
    env_file = Path(env_file).expanduser().resolve()
    env = read_env(env_file)
    verdict = check_environment(env_file)
    collection = Path(verdict['collection_path'])
    media = Path(verdict['media_path'])
    journal = Path(str(collection)+'.ir4anki-answer.json')
    if journal.exists() and json.loads(journal.read_text()) is not None:
        raise RuntimeError('Pending answer journal: resolve undo or explicitly commit in ir4anki before migration snapshot; never discard it')
    state = absolute_path(env.get('ANKI_STATE_DIR', str(Path.home()/'.local/state/ir4anki')), 'ANKI_STATE_DIR')
    corpus = absolute_path(env.get('ANKI_NOTES_DIR', str(Path.home()/'anki-notes')), 'ANKI_NOTES_DIR')
    output = Path(output).expanduser().resolve()
    for source in (collection.parent, state, corpus):
        if output == source or output.is_relative_to(source):
            raise RuntimeError('Snapshot output must be outside collection, state and corpus directories')
    if not state.is_dir() or not corpus.is_dir():
        raise RuntimeError('State and corpus directories must exist for a complete snapshot')
    lock = None
    try:
        lockpath = Path(str(collection) + '.ir4anki.lock')
        if lockpath.exists():
            lock = lockpath.open('r')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another native owner holds collection; stop it before snapshot') from None
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        target = output / stamp
        target.mkdir(mode=0o700)
        with sqlite3.connect(collection.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(target/'collection.anki2') as destination:
                source.backup(destination)
        if media.is_dir():
            shutil.copytree(media, target/'media', symlinks=True)
        shutil.copytree(state, target/'state', symlinks=True)
        shutil.copytree(corpus, target/'corpus', symlinks=True)
        shutil.copyfile(env_file, target/'environment.private')
        os.chmod(target/'environment.private', 0o600)
        manifest = {'created_utc': stamp, 'complete': True, 'engine_version': PIN,
                    'collection': collection_info(target/'collection.anki2'),
                    'sources': {'collection': str(collection), 'media': str(media), 'state': str(state), 'corpus': str(corpus)},
                    'media_present': media.is_dir(), 'symlinks_preserved': True}
        (target/'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
        return target
    finally:
        if lock is not None:
            lock.close()


def health(url, require_pylib=True):
    with urllib.request.urlopen(url.rstrip('/')+'/api/status', timeout=10) as response:
        data = json.load(response)
    if data.get('anki') != 'ok' or (require_pylib and data.get('anki_backend') != 'pylib'):
        raise RuntimeError('Backend health does not confirm an active pylib collection')
    return {'anki': data['anki'], 'anki_backend': data.get('anki_backend')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'backup'):
        item = sub.add_parser(name)
        item.add_argument('--env', default=str(Path.home()/'.config/ir4anki.env'))
        item.add_argument('--json', action='store_true')
        if name == 'check':
            item.add_argument('--require-offline', action='store_true')
        if name == 'backup':
            item.add_argument('--output', required=True)
            item.add_argument('--offline-confirmed', action='store_true')
    item = sub.add_parser('health')
    item.add_argument('--url', default='http://127.0.0.1:8901')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            if args.require_offline and desktop_processes():
                raise RuntimeError('Close desktop Anki before selecting the native backend')
            result = check_environment(args.env)
        elif args.command == 'backup':
            result = {'snapshot': str(create_snapshot(args.env, args.output, offline_confirmed=args.offline_confirmed))}
        else:
            result = health(args.url)
    except (RuntimeError, OSError, sqlite3.Error, importlib.metadata.PackageNotFoundError, ValueError) as exc:
        # Controlled diagnostics never echo config contents or credential-bearing URLs.
        print(f'ERROR: {exc}' if isinstance(exc, RuntimeError) else 'ERROR: preflight/snapshot failed; inspect local paths, permissions and pinned dependencies', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
