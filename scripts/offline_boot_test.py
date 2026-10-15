"""Static cold-load asset/unit closure; actual browser/service tests are separate."""
import os,re,json
from pathlib import Path
from html.parser import HTMLParser
ROOT=Path(__file__).resolve().parents[1]
unit=Path(os.environ.get('OFFLINE_UNIT',str(ROOT/'deploy/ir4anki.service'))).read_text()
assert 'network-online.target' not in unit, 'offline service depends on online target'
dist=Path(os.environ['REGRESSION_DIST']).resolve()
assert dist != ROOT/'frontend/dist'
assets=[]
def local(url,base):
    if url.startswith('data:'):return
    assert not re.match(r'(https?:)?//',url),f'remote runtime asset: {url}'
    p=(dist/url.lstrip('/')) if url.startswith('/') else (base/url)
    assert p.is_file(),f'missing built asset: {p}'
    assets.append(str(p))
class HTML(HTMLParser):
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=='script' and d.get('src'):local(d['src'],dist)
        if tag=='link' and d.get('rel') in ('stylesheet','modulepreload','preload'):local(d['href'],dist)
HTML().feed((dist/'index.html').read_text())
for css in dist.rglob('*.css'):
    text=css.read_text()
    for match in re.finditer(r'url\(\s*[\'\"]?([^\)\'\"\s]+)',text):local(match[1],css.parent)
    assert not re.search(r'@import\s*[\'\"]https?://',text)
assert any('KaTeX' in p for p in assets),'bundled formula fonts missing'
print(json.dumps({'unit':'no network-online target','built_local_assets_checked':len(assets),'dist':str(dist),'scope':'static HTML/CSS assets; not browser execution or real systemd startup'}))
