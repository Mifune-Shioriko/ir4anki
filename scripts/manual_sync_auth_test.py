"""ASGI-only authorization tests: isolated paths, no engine/server/lifespan."""
import asyncio, importlib, sys
from pathlib import Path
import httpx
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith('ANKI_'): monkeypatch.delenv(key)
    for key, value in {'ANKI_BACKEND':'connect', 'ANKI_STATE_DIR':str(tmp_path/'state'), 'ANKI_NOTES_DIR':str(tmp_path/'corpus'), 'ANKI_MEDIA_DIR':str(tmp_path/'media'), 'REVIEW_DIST_DIR':str(tmp_path/'no-dist')}.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop('app', None)
    module=importlib.import_module('app')
    monkeypatch.setattr(module, '_pylib', object())
    calls=[]
    async def engine(action, params=None, **kwargs):
        calls.append((action,params)); return {'synced':True}
    monkeypatch.setattr(module, 'anki', engine)
    yield module, calls
    sys.modules.pop('app', None)

def request(module, *, peer='127.0.0.1', url='http://127.0.0.1:8901', headers=None, body=None, method='POST', path='/api/local/sync'):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app,client=(peer,12345)),base_url=url) as client:
            return await client.request(method,path,headers=headers or {},json=body)
    return asyncio.run(run())

def valid(origin='http://127.0.0.1:8901'):
    return {'Origin':origin,'X-IR4Anki-Local-Sync':'confirm','Content-Type':'application/json'}

@pytest.mark.parametrize('url,peer', [('http://127.0.0.1:8901','127.0.0.1'),('http://localhost:8901','::1'),('http://[::1]:8901','::1')])
def test_local_confirm_no_admin_token(isolated_app,url,peer):
    module,calls=isolated_app
    r=request(module,url=url,peer=peer,headers=valid(url),body={'commit_undo':True})
    assert r.status_code==200, r.text
    assert calls==[('sync',{'commit_undo':True})]

@pytest.mark.parametrize('peer,url,headers', [
    ('192.0.2.1','http://127.0.0.1:8901',valid()),
    ('127.0.0.1','http://evil.example',valid('http://evil.example')),
    ('127.0.0.1','http://127.0.0.1:8901',valid('http://evil.example')),
    ('127.0.0.1','http://127.0.0.1:8901',valid('http://127.0.0.1:8902')),
    ('127.0.0.1','http://127.0.0.1:8901',valid('null')),
    ('127.0.0.1','http://127.0.0.1:8901',{}),
    ('127.0.0.1','http://127.0.0.1:8901',{'Origin':'http://127.0.0.1:8901'}),
    ('127.0.0.1','http://127.0.0.1:8901',{**valid(),'X-Forwarded-For':'127.0.0.1'}),
    ('127.0.0.1','http://127.0.0.1:8901',{**valid(),'Forwarded':'for=127.0.0.1'}),
    ('127.0.0.1','http://127.0.0.1:8901',{**valid(),'Sec-Fetch-Site':'cross-site'}),
    ('127.0.0.1','http://127.0.0.1:8901',{**valid(),'Content-Type':'text/plain'}),
])
def test_local_boundary_rejects_before_dispatch(isolated_app,peer,url,headers):
    module,calls=isolated_app
    assert request(module,peer=peer,url=url,headers=headers,body={'commit_undo':True}).status_code in (403,422)
    assert not calls

@pytest.mark.parametrize('body',[{}, {'commit_undo':False}, {'commit_undo':True,'direction':'upload'}])
def test_confirmation_required_and_direction_not_accepted(isolated_app,body):
    module,calls=isolated_app
    assert request(module,headers=valid(),body=body).status_code==422
    assert not calls

@pytest.mark.parametrize('method,path', [('POST','/api/engine/sync'),('POST','/api/engine/backup'),('POST','/api/engine/export'),('GET','/api/engine/stats'),('GET','/api/engine/download/export/test.colpkg')])
def test_admin_operations_stay_protected(isolated_app,method,path):
    module,calls=isolated_app
    assert request(module,method=method,path=path,headers=valid(),body={}).status_code==403
    assert not calls

def test_connect_fails_closed(isolated_app,monkeypatch):
    module,calls=isolated_app; monkeypatch.setattr(module,'_pylib',None)
    assert request(module,headers=valid(),body={'commit_undo':True}).status_code==409
    assert not calls

def test_original_admin_sync_still_accepts_configured_token(isolated_app,monkeypatch):
    module,calls=isolated_app
    monkeypatch.setenv('ANKI_ENGINE_API_TOKEN','isolated-fixture-token')
    r=request(module,path='/api/engine/sync',headers={'Authorization':'Bearer isolated-fixture-token'},body={'commit_undo':True})
    assert r.status_code==200
    assert calls==[('sync',{'commit_undo':True})]
    calls.clear()
    assert request(module,path='/api/engine/sync',headers={'Authorization':'Bearer wrong'},body={'commit_undo':True}).status_code==403
    assert not calls

def test_admin_token_cannot_bypass_local_origin_boundary(isolated_app,monkeypatch):
    module,calls=isolated_app
    monkeypatch.setenv('ANKI_ENGINE_API_TOKEN','isolated-fixture-token')
    headers={**valid('http://evil.example'),'Authorization':'Bearer isolated-fixture-token'}
    assert request(module,headers=headers,body={'commit_undo':True}).status_code==403
    assert not calls
