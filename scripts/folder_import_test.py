"""Folder import regressions against a real ASGI app and disposable Anki.

FOLDER_IMPORT_BACKEND can select an isolated release backend checkout.
Never loads operational environment/configuration or uses a real collection.
"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote

from native_fixture import configure, tempdir, run_closed

root = Path(__file__).resolve().parents[1]
backend_path = Path(os.environ.get('FOLDER_IMPORT_BACKEND', root / 'backend'))
os.environ['ANKI_STATE_DIR'] = tempdir('folder-import-state-')
os.environ['ANKI_NOTES_DIR'] = tempdir('folder-import-notes-')
os.environ['ANKI_PREVIEW_MODE'] = '0'
os.environ['ANKI_READING_MODE'] = '1'
configure()
sys.path.insert(0, str(backend_path))
import app as b
import httpx


async def main():
    corpus = b.NOTES_DIR
    checks = 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://t') as c:
        async def upload(entries, policy='error'):
            return await c.post('/api/files/upload', data={
                'rel_paths': [p for p, _ in entries], 'dir': '2026', 'on_conflict': policy,
            }, files=[('files', (p, data, 'application/octet-stream')) for p, data in entries])

        # Reproduce the user's actual book layout, not just an ordinary images/.
        (corpus / 'old').mkdir()
        (corpus / 'old' / 'a.png').write_bytes(b'existing-image')
        entries = [('金钱心理学/001-正文.md', '# 正文\n![图](_assets/a.png)'.encode()),
                   ('金钱心理学/_assets/a.png', b'new-image'),
                   ('金钱心理学/章节/_assets/b.PNG', b'another-image')]
        r = await upload(entries)
        assert r.status_code == 200, r.text
        saved = {s['original']: s['path'] for s in r.json()['saved']}
        assert len(saved) == 3
        image = corpus / saved['金钱心理学/_assets/a.png']
        assert image.parent == corpus / '2026/金钱心理学/_assets'
        assert image.read_bytes() == b'new-image'
        text = (corpus / saved['金钱心理学/001-正文.md']).read_text()
        assert '_assets/' + image.name in unquote(text), text
        assert (corpus / 'old/a.png').read_bytes() == b'existing-image'
        assert (corpus / saved['金钱心理学/章节/_assets/b.PNG']).read_bytes() == b'another-image'
        assert (await c.get('/api/reading/media/' + image.name)).content == b'new-image'
        checks += 1

        # Internal editor assets remain protected, even for image uploads.
        for path in ['_assets/root.png', '2026/金钱心理学/_assets/a.png']:
            try:
                b._files_resolve(path)
            except b.HTTPException as e:
                assert e.status_code == 400
            else:
                raise AssertionError('management path protection lost: ' + path)
        try:
            b._files_resolve('_assets/root.png', upload_image=True)
        except b.HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError('root editor assets unprotected')
        checks += 1

        # Markdown conflicts still preflight the whole group without image writes.
        before = {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob('*') if p.is_file()}
        r = await upload(entries)
        assert r.status_code == 409, r.text
        after = {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob('*') if p.is_file()}
        assert before == after
        for policy in ['skip', 'rename', 'overwrite']:
            r = await upload(entries, policy)
            assert r.status_code == 200, (policy, r.text)
        checks += 1

        # The upload-only exemption must not weaken metadata/path/symlink checks.
        rejected = ['_assets/.git/a.png', '.git/a.png', '.obsidian/a.png',
                    '书/_assets/node_modules/a.png', '书/_assets/private.md',
                    '书/_assets/manifest.json', '../../outside.png']
        for path in rejected:
            before = {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob('*') if p.is_file()}
            r = await upload([('安全/未写入.md', b'# safe'), (path, b'content')])
            assert r.status_code == 400, (path, r.text)
            assert before == {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob('*') if p.is_file()}
            checks += 1
        outside = Path(tempdir('folder-import-outside-'))
        (corpus / '2026/linked').symlink_to(outside, target_is_directory=True)
        r = await upload([('linked/_assets/a.png', b'bad')])
        assert r.status_code == 400, r.text
        assert not list(outside.iterdir())
        checks += 1

        # Import does not silently opt the book into reading.
        assert not b._reading_read()['list']
        checks += 1
    print(f'{checks} folder import regression groups passed; backend={backend_path}')


asyncio.run(run_closed(b, main()))
