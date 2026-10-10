"""Harness ownership checks without browser sockets or any live collection."""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_harness(monkeypatch, tmp_path):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'assets').mkdir()
    (dist / 'index.html').write_text('isolated')
    monkeypatch.setenv('REGRESSION_DIST', str(dist))
    monkeypatch.setenv('INTERLEAVE_RUN_DIR', str(tmp_path / 'evidence'))
    spec = importlib.util.spec_from_file_location('interleave_ui_harness', ROOT / 'scripts/interleave_ui_test.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_outside_playwright_and_case_cleanup(monkeypatch, tmp_path):
    h = load_harness(monkeypatch, tmp_path)
    inside = False
    prepared = []
    cleaned = []
    monkeypatch.setattr(h, 'reserve_loopback_port', lambda: 1234)

    def prepare(s, c, kind):
        assert not inside, 'native asyncio setup must precede sync Playwright'
        env = {'case': str(len(prepared))}
        prepared.append(env)
        return env

    class Browser:
        def close(self): pass

    class Playwright:
        chromium = None
        def __init__(self): self.chromium = self
        def launch(self): return Browser()
        def __enter__(self):
            nonlocal inside
            inside = True
            return self
        def __exit__(self, *args):
            nonlocal inside
            inside = False

    def run_case(browser, s, c, kind, env):
        assert inside
        assert env is prepared[-1]

    monkeypatch.setattr(h, 'prepare', prepare)
    monkeypatch.setattr(h, 'run_case', run_case)
    monkeypatch.setattr(h, 'sync_playwright', Playwright)
    monkeypatch.setattr(h, 'cleanup', lambda: cleaned.append(len(prepared)))
    h.main()
    assert len(prepared) == 11
    assert cleaned == list(range(1, 12))


def test_native_case_environment_and_roots_are_unique(monkeypatch, tmp_path):
    h = load_harness(monkeypatch, tmp_path)
    monkeypatch.setenv('NATIVE_FIXTURE_ROOT', str(tmp_path))
    original = dict(os.environ)
    try:
        first = h.prepare(1, 1, 'normal')
        second = h.prepare(0, 0, 'normal')
        assert dict(os.environ) == original
        for key in ('ANKI_COLLECTION_PATH', 'ANKI_STATE_DIR', 'ANKI_NOTES_DIR', 'ANKI_MEDIA_DIR', 'ANKI_BACKUP_DIR'):
            assert first[key] != second[key], key
            assert Path(first[key]).is_relative_to(tmp_path)
            assert Path(second[key]).is_relative_to(tmp_path)
        assert Path(first['ANKI_COLLECTION_PATH']).exists()
        assert Path(second['ANKI_COLLECTION_PATH']).exists()
    finally:
        h.cleanup()
    assert not Path(first['ANKI_COLLECTION_PATH']).exists()
    assert not Path(second['ANKI_COLLECTION_PATH']).exists()
