"""Real browser + disposable native backend folder-import smoke test.

FOLDER_IMPORT_BACKEND and REGRESSION_DIST select tested source/build.
Requires playwright + Chromium; never visits the operational service.
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

from native_fixture import configure, tempdir, cleanup, reserve_loopback_port, verify_server
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
backend = Path(os.environ.get('FOLDER_IMPORT_BACKEND', root / 'backend'))
dist = Path(os.environ['REGRESSION_DIST'])
os.environ['ANKI_STATE_DIR'] = tempdir('folder-browser-state-')
os.environ['ANKI_NOTES_DIR'] = tempdir('folder-browser-corpus-')
os.environ['ANKI_PREVIEW_MODE'] = '0'
os.environ['ANKI_READING_MODE'] = '1'
os.environ['REVIEW_DIST_DIR'] = str(dist)
configure()
corpus = Path(os.environ['ANKI_NOTES_DIR'])
(corpus / '2026').mkdir()
source = Path(tempdir('folder-browser-input-')) / '金钱心理学'
(source / '_assets').mkdir(parents=True)
(source / '001-正文.md').write_text('# 正文\n\n![图](_assets/a.png)', encoding='utf-8')
(source / '_assets/a.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))
(source / '_assets/manifest.json').write_text('{}')
(source / '.DS_Store').write_bytes(b'junk')
port = reserve_loopback_port()
base = f'http://127.0.0.1:{port}'
log = Path(tempdir('folder-browser-log-')) / 'server.log'
proc = None
try:
    with log.open('w') as output:
        proc = subprocess.Popen([os.environ.get('FOLDER_IMPORT_PYTHON', sys.executable), '-m', 'uvicorn',
            '--app-dir', str(backend), 'app:app', '--host', '127.0.0.1', '--port', str(port)],
            env=os.environ.copy(), stdout=output, stderr=output)
    verify_server(proc, base)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("sessionStorage.setItem('files-directory', JSON.stringify('2026'))")
        page.goto(base)
        page.locator('.nav-rail__item', has_text='文件').click()
        folder_button = page.locator('.fb-import-actions md-outlined-button', has_text='导入文件夹')
        expect(folder_button).to_be_visible()
        assert page.locator('.fb-import-actions md-menu-item', has_text='导入文件夹').count() == 0
        with page.expect_file_chooser() as chooser:
            folder_button.click()
        with page.expect_response(lambda r: '/api/files/upload' in r.url) as response:
            chooser.value.set_files(str(source))
        assert response.value.status == 200, response.value.text()
        expect(page.locator('.fb-import-skipped summary')).to_have_text('已跳过 2 个文件（未上传）')
        page.locator('.fb-import-skipped summary').click()
        expect(page.locator('.fb-import-skipped')).to_contain_text('_assets/manifest.json')
        assert (corpus / '2026/金钱心理学/001-正文.md').exists()
        assert (corpus / '2026/金钱心理学/_assets/a.png').exists()
        assert not (corpus / '2026/金钱心理学/_assets/manifest.json').exists()
        assert not (corpus / '2026/金钱心理学/.DS_Store').exists()
        media = page.request.get(base + '/api/reading/media/a.png')
        assert media.status == 200
        assert media.body() == (source / '_assets/a.png').read_bytes()
        # Run the same picker again: explicit conflict decision is still required.
        with page.expect_file_chooser() as chooser:
            folder_button.click()
        chooser.value.set_files(str(source))
        conflict = page.get_by_role('dialog')
        expect(conflict).to_be_visible()
        expect(conflict).to_contain_text('导入文件名冲突')
        conflict.get_by_role('button', name='跳过重名', exact=True).click()
        expect(conflict).not_to_be_visible()
        page.wait_for_function("!document.querySelector('.fb-import-actions md-outlined-button').disabled")
        # Wide, intermediate, and phone layouts must expose all main actions.
        for width in [1440, 800, 601, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 900})
            expect(folder_button).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
            box = folder_button.bounding_box()
            assert box and box['x'] >= 0 and box['x'] + box['width'] <= width, (width, box)
        # All-unsupported selection is reported without dispatching a request.
        unsupported = Path(tempdir('folder-browser-junk-')) / '只有清单'
        unsupported.mkdir()
        (unsupported / 'manifest.json').write_text('{}')
        uploads = []
        page.on('request', lambda request: uploads.append(request.url) if '/api/files/upload' in request.url else None)
        with page.expect_file_chooser() as chooser:
            folder_button.click()
        chooser.value.set_files(str(unsupported))
        expect(page.locator('.fb-import-skipped summary')).to_have_text('已跳过 1 个文件（未上传）')
        assert not uploads
        assert not errors, errors
        browser.close()
        print('browser folder import passed: actual picker + native upload + junk report + conflicts + 5 viewport sizes')
except Exception:
    if log.exists():
        print(log.read_text()[-6000:], file=sys.stderr)
    raise
finally:
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    cleanup()
