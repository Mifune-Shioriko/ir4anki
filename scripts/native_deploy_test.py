"""Operator tooling tests: disposable data only; never run installer/start services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from anki.collection import Collection

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'deploy' / 'native.py'


def fixture(tmp_path):
    profile = tmp_path / 'Profile with spaces'
    profile.mkdir()
    path = profile / 'collection.anki2'
    col = Collection(str(path))
    note = col.new_note(col.models.by_name('Basic'))
    note['Front'], note['Back'] = 'isolated deployment question', 'answer'
    col.add_note(note, col.decks.id('Default'))
    col.close()
    media = profile / 'collection.media'
    media.mkdir(exist_ok=True)
    (media / 'picture.png').write_bytes(b'isolated-fixture')
    state, corpus = tmp_path / 'state', tmp_path / 'corpus'
    state.mkdir(); corpus.mkdir()
    (state / 'round.json').write_text('{"pending":[]}', encoding='utf-8')
    (corpus / 'note.md').write_text('# content\n\nPure Markdown.', encoding='utf-8')
    env = tmp_path / 'settings.txt'
    env.write_text(f'ANKI_BACKEND=pylib\nANKI_COLLECTION_PATH={path}\nANKI_STATE_DIR={state}\nANKI_NOTES_DIR={corpus}\nANKI_ADD_MODEL=Basic\nANKI_ADD_CLOZE_MODEL=Cloze\nANKI_SYNC_ENDPOINT=http://127.0.0.1:1/\nANKI_SYNC_HKEY=DO_NOT_PRINT_TEST_SECRET\n', encoding='utf-8')
    return env, path, state, corpus


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str,args)], capture_output=True, text=True, timeout=15)


def test_check_is_readonly_and_never_prints_secrets(tmp_path):
    env, path, _, _ = fixture(tmp_path)
    before = path.read_bytes()
    result = run('check', '--env', env, '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data['collection']['cards'] == 1
    assert data['engine_version'] == '25.2.7'
    assert data['sync_configured'] is True
    assert 'DO_NOT_PRINT' not in result.stdout + result.stderr
    assert path.read_bytes() == before


def test_missing_collection_does_not_create_database(tmp_path):
    env = tmp_path / 'settings.txt'
    missing = tmp_path / 'absent.anki2'
    env.write_text(f'ANKI_BACKEND=pylib\nANKI_COLLECTION_PATH={missing}\n')
    result = run('check', '--env', env, '--json')
    assert result.returncode != 0
    assert not missing.exists()
    assert 'collection' in result.stderr.lower()


def test_backup_requires_explicit_offline_confirmation(tmp_path):
    env, path, _, _ = fixture(tmp_path)
    result = run('backup', '--env', env, '--output', tmp_path / 'backups')
    assert result.returncode != 0
    assert not (tmp_path / 'backups').exists()


def test_complete_offline_snapshot_is_restorable_and_manifest_redacted(tmp_path, monkeypatch):
    env, path, state, corpus = fixture(tmp_path)
    spec = importlib.util.spec_from_file_location('native_deploy', TOOL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'service_active', lambda service: False)
    monkeypatch.setattr(module, 'desktop_processes', lambda: [])
    target = module.create_snapshot(env, tmp_path / 'backups', offline_confirmed=True)
    manifest = json.loads((target / 'manifest.json').read_text())
    assert manifest['collection']['cards'] == 1
    assert 'DO_NOT_PRINT' not in json.dumps(manifest)
    assert (target / 'media' / 'picture.png').read_bytes() == b'isolated-fixture'
    assert (target / 'state' / 'round.json').read_text() == (state / 'round.json').read_text()
    assert (target / 'corpus' / 'note.md').read_text() == (corpus / 'note.md').read_text()
    assert (target / 'environment.private').stat().st_mode & 0o777 == 0o600
    copied = Collection(str(target / 'collection.anki2'))
    assert len(copied.find_cards('')) == 1
    copied.close()


def test_snapshot_refuses_pending_answer_journal(tmp_path, monkeypatch):
    import pytest
    env, path, _, _ = fixture(tmp_path)
    journal = Path(str(path)+'.ir4anki-answer.json')
    journal.write_text(json.dumps({'phase':'committed','token':'isolated-token'}))
    spec = importlib.util.spec_from_file_location('native_pending_snapshot', TOOL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'service_active', lambda service: False)
    monkeypatch.setattr(module, 'desktop_processes', lambda: [])
    with pytest.raises(RuntimeError, match='journal'):
        module.create_snapshot(env, tmp_path/'backups', offline_confirmed=True)
    assert not (tmp_path/'backups').exists()


def test_backup_refuses_active_service(tmp_path, monkeypatch):
    env, _, _, _ = fixture(tmp_path)
    spec = importlib.util.spec_from_file_location('native_deploy_active', TOOL)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'service_active', lambda service: True)
    import pytest
    with pytest.raises(RuntimeError, match='stop'):
        module.create_snapshot(env, tmp_path / 'backups', offline_confirmed=True)
    assert not (tmp_path / 'backups').exists()


def test_updater_help_does_not_pull_or_restart():
    result = subprocess.run(['bash', str(ROOT/'deploy'/'update.sh'), '--help'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert '--local' in result.stdout and '--no-restart' in result.stdout


def test_updater_refuses_dirty_pull_before_touching_remote(tmp_path):
    repo = tmp_path / 'repo'
    (repo/'deploy').mkdir(parents=True)
    import shutil
    shutil.copyfile(ROOT/'deploy'/'update.sh', repo/'deploy'/'update.sh')
    subprocess.run(['git','init','-q'], cwd=repo, check=True)
    result = subprocess.run(['bash',str(repo/'deploy'/'update.sh')], cwd=repo, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'local changes' in (result.stdout + result.stderr).lower()
    assert 'git pull' not in result.stdout


def test_native_service_has_single_owner_and_graceful_stop_budget():
    text = (ROOT/'deploy'/'ir4anki.service').read_text()
    assert '--workers 1' in text
    assert 'TimeoutStopSec=300' in text


def test_health_confirms_real_isolated_native_backend(tmp_path):
    import os, socket, time
    envfile, _, _, _ = fixture(tmp_path)
    env = os.environ.copy()
    for line in envfile.read_text().splitlines():
        key, _, value = line.partition('='); env[key] = value
    env.update(ANKI_SYNC_ENDPOINT='', ANKI_SYNC_HKEY='', ANKI_SYNC_USERNAME='', ANKI_SYNC_PASSWORD='', ANKI_READING_MODE='0', ANKI_PREVIEW_MODE='0', REVIEW_DIST_DIR='/nonexistent')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    log = (tmp_path/'backend.log').open('w')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','--app-dir',str(ROOT/'backend'),'app:app','--host','127.0.0.1','--port',str(port),'--workers','1'], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        result = None
        for _ in range(50):
            result = run('health', '--url', f'http://127.0.0.1:{port}')
            if result.returncode == 0: break
            if process.poll() is not None: break
            time.sleep(.1)
        assert result.returncode == 0, (tmp_path/'backend.log').read_text() + result.stderr
        assert json.loads(result.stdout)['anki_backend'] == 'pylib'
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
        log.close()


def test_installer_documents_native_mode_without_running_install():
    result = subprocess.run(['bash', str(ROOT/'deploy'/'install.sh'), '--help'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert '--backend' in result.stdout and 'pylib' in result.stdout
