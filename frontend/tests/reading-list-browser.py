"""Synthetic reading-list presentation/interaction checks; never contacts the live app.

Build: cd frontend && npx vite build --config tests/reading-list-fixture.config.mjs --outDir /tmp/reading-list-fixture
Run: uv run --with playwright python frontend/tests/reading-list-browser.py --dist /tmp/reading-list-fixture
"""
import argparse
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--dist', type=Path, required=True)
parser.add_argument('--screenshots', type=Path)
parser.add_argument('--browser', type=Path, help='Optional installed Chromium executable')
args = parser.parse_args()


def summary(path, **extra):
    return dict(path=path, title=Path(path).stem, total_chunks=8, todo=3,
                active=1, done=2, skipped=1, background=1, total_chars=24000,
                read_chars=12000, active_chars=3000, cards_created=12, orphans=0,
                frontier=dict(chunk_key='next', status='active', title='下一节',
                              line_start=42, preview='结构与功能：从这里继续阅读。'), **extra)


initial = [
    summary('2026/解剖/颈部.md'),
    summary('2026/教材/组织学.pdf', kind='pdf', total_pages=120, read_pages=30, active_pages=10),
    summary('2026/解剖/待确认.md'),
    summary('2026/旧目录/已移走.md', missing=True),
    summary('2026/专题/' + '很长的文件名与路径' * 12 + '.md'),
]
initial[2].update(orphans=2, frontier=None,
                  gated_frontier=dict(chunk_key='held', status='active', title='等待放行', line_start=72))
initial[4].update(frontier=None, read_chars=24000, active_chars=0)
archived_path = '2026/解剖/有存档的笔记.md'
fresh_path = '2026/解剖/待加入.md'
corpus = [dict(path=s['path'], title=s['title'], chars=24000,
               **({'kind': 'pdf', 'page_count': 120} if s.get('kind') == 'pdf' else {}))
          for s in initial if not s.get('missing')]
corpus.extend([dict(path=archived_path, title='有存档的笔记', chars=3000, archived=True),
               dict(path=fresh_path, title='待加入', chars=6000)])
items = copy.deepcopy(initial)
mutations = []
fail_next = False
empty_corpus = False


def api_route(route):
    global fail_next
    path = urlparse(route.request.url).path
    payload = route.request.post_data_json if route.request.method == 'POST' else None
    if payload is not None:
        mutations.append((path, payload))
        if fail_next:
            fail_next = False
            route.fulfill(status=500, json={'detail': '测试错误'})
            return
    if path == '/api/reading/status':
        data = {'list': items}
    elif path == '/api/reading/corpus':
        data = {'files': [] if empty_corpus else [dict(f, in_list=any(s['path'] == f['path'] for s in items)) for f in corpus]}
    elif path == '/api/reading/list/reorder':
        if 'order' in payload:
            by_path = {s['path']: s for s in items}
            items[:] = [by_path[p] for p in payload['order']]
        else:
            index = next(i for i, s in enumerate(items) if s['path'] == payload['path'])
            item = items.pop(index)
            items.insert(0 if payload.get('top') else max(0, index - 1), item)
        data = {'ok': True}
    elif path == '/api/reading/list/remove':
        items[:] = [s for s in items if s['path'] != payload['path']]
        data = {'ok': True}
    elif path == '/api/reading/list/add':
        items.append(summary(payload['path']))
        data = {'ok': True}
    elif path == '/api/files/list':
        data = {'files': corpus, 'dirs': ['2026', '2026/解剖', '2026/教材', '2026/专题']}
    elif path == '/api/files/membership':
        data = {'paths': [s['path'] for s in items]}
    else:
        raise AssertionError('Unexpected API request: ' + path)
    route.fulfill(json=data)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(args.dist.resolve())))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{server.server_port}/reading-list-fixture.html'
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(**({'executable_path': str(args.browser)} if args.browser else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', api_route)
        page.goto(url)
        expect(page.locator('.reading-list-item')).to_have_count(5)
        expect(page.locator('h1')).to_have_text('阅读清单')
        expect(page.locator('.reading-picker-hint')).to_have_text('待加入 2 篇 · 共 6 篇')
        first = page.locator('.reading-list-item').first
        for text in ['颈部', '解剖', '50%', '已读 12,000 / 24,000 字', '正在制卡 3,000 字 (1 段)',
                     '已制卡 12 张', '片段 4 / 8', 'L42', '结构与功能']:
            expect(first).to_contain_text(text)
        pdf = page.locator('.reading-list-item').nth(1)
        expect(pdf).to_contain_text('已读 30 / 120 页')
        expect(pdf).to_contain_text('正在制卡 10 页')
        expect(pdf.locator('[role=progressbar]')).to_have_attribute('aria-valuenow', '30')
        expect(page.locator('.reading-list-item').nth(2)).to_contain_text('文件已变更 · 2 段状态待确认')
        expect(page.locator('.reading-list-item').nth(2)).to_contain_text('等卡片过预览池')
        expect(page.locator('.reading-list-item').nth(3)).to_contain_text('文件已不在语料中')
        expect(page.locator('.reading-list-item').nth(4)).to_contain_text('全部读完')

        # Exact presentation parity with the real FilesScreen, not copied CSS in a mock.
        reading_style = page.locator('.reading-corpus-card').evaluate('e => { const s=getComputedStyle(e), r=e.getBoundingClientRect(); return [s.backgroundColor,s.borderRadius,r.x,r.y,r.width,r.height] }')
        reading_title = page.locator('.reading-corpus-head h2').evaluate('e => getComputedStyle(e).fontSize')
        page.get_by_role('button', name='文件', exact=True).click()
        expect(page.locator('.files-tree-card')).to_be_visible()
        files_style = page.locator('.files-tree-card').evaluate('e => { const s=getComputedStyle(e), r=e.getBoundingClientRect(); return [s.backgroundColor,s.borderRadius,r.x,r.y,r.width,r.height] }')
        assert reading_style == files_style, (reading_style, files_style)
        assert reading_title == page.locator('.files-tree-head > span').first.evaluate('e => getComputedStyle(e).fontSize')
        page.get_by_role('button', name='阅读', exact=True).click()
        expect(page.locator('.reading-list-item')).to_have_count(5)

        # Responsive/light/dark checks including long names and a scrollable long queue.
        items.extend(summary(f'2026/长队列/笔记 {i}.md') for i in range(6))
        page.reload()
        expect(page.locator('.reading-list-item')).to_have_count(11)
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
        for theme in ['light', 'dark']:
            page.emulate_media(color_scheme=theme)
            for width in [1440, 1280, 1180, 1179, 768, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width)
                assert page.locator('.reading-list-item__actions').first.evaluate('e => e.getBoundingClientRect().right <= innerWidth'), (theme, width)
                boxes = [page.locator(s).bounding_box() for s in ['.reading-corpus-card', '.reading-queue-card']]
                if width >= 1180:
                    assert boxes[0]['y'] == boxes[1]['y'] and boxes[1]['width'] > boxes[0]['width']
                    assert page.locator('.reading-queue-body').evaluate('e => e.scrollHeight > e.clientHeight')
                else:
                    assert boxes[1]['y'] >= boxes[0]['y'] + boxes[0]['height']
                if args.screenshots and width in [1440, 390]:
                    page.screenshot(path=str(args.screenshots / f'{theme}-{width}.png'), full_page=True)
        page.emulate_media(color_scheme='light', reduced_motion='reduce')
        page.set_viewport_size({'width': 1440, 'height': 900})
        items[:] = copy.deepcopy(initial)
        page.reload()
        expect(page.locator('.reading-list-item')).to_have_count(5)

        search = page.get_by_role('searchbox')
        search.fill('没有这个文件')
        expect(page.get_by_role('status')).to_contain_text('没有匹配的文件')
        search.fill('有存档')
        expect(page.locator('.reading-tree-file')).to_have_count(1)
        expect(page.locator('.reading-tree-file')).to_contain_text('有存档进度')
        expect(page.get_by_role('button', name='恢复进度')).to_be_visible()
        expect(page.get_by_role('button', name='重新播种')).to_be_visible()
        page.evaluate('window.fixture.setBusy(true)')
        expect(page.get_by_role('button', name='恢复进度')).to_be_disabled()
        expect(first.get_by_role('button', name='下移')).to_be_disabled()
        page.evaluate('window.fixture.setBusy(false)')
        page.get_by_role('button', name='恢复进度').click()
        expect(page.locator('.reading-list-item')).to_have_count(6)
        assert mutations[-1] == ('/api/reading/list/add', {'path': archived_path})
        page.locator('.reading-list-item').last.get_by_role('button', name='移出清单').click()
        expect(page.locator('md-dialog')).to_contain_text('已读进度会保留')
        page.get_by_role('button', name='取消', exact=True).click()
        expect(page.locator('.reading-list-item')).to_have_count(6)
        page.locator('.reading-list-item').last.get_by_role('button', name='移出清单').click()
        page.get_by_role('button', name='移出', exact=True).click()
        expect(page.locator('.reading-list-item')).to_have_count(5)
        page.get_by_role('button', name='重新播种').click()
        expect(page.locator('.reading-list-item')).to_have_count(6)
        assert mutations[-1] == ('/api/reading/list/add', {'path': archived_path, 'fresh': True})
        search.fill('待加入')
        page.get_by_role('button', name='加入清单').click()
        expect(page.locator('.reading-list-item')).to_have_count(7)
        assert mutations[-1] == ('/api/reading/list/add', {'path': fresh_path})
        search.fill('')
        expect(first.get_by_role('button', name='置顶')).to_be_disabled()
        first.get_by_role('button', name='下移').click()
        expect(first).to_contain_text('组织学')
        page.locator('.reading-list-item').nth(1).get_by_role('button', name='上移').click()
        expect(first).to_contain_text('颈部')
        page.locator('.reading-list-item').nth(2).get_by_role('button', name='置顶').click()
        expect(first).to_contain_text('待确认')
        fail_next = True
        first.get_by_role('button', name='下移').click()
        expect(page.get_by_role('alert')).to_contain_text('操作失败')
        assert page.evaluate('window.fixture.changes()') == 7

        items.clear()
        empty_corpus = True
        page.reload()
        expect(page.locator('.reading-list-empty')).to_contain_text('清单还是空的')
        expect(page.locator('.reading-picker-empty')).to_contain_text('还没有可读内容')
        assert not errors, errors
        browser.close()
        print('PASS: information retained; files-page parity; 14 responsive/theme cases; search, busy, add/restore/reseed/remove/reorder/error/empty states')
finally:
    server.shutdown()
    server.server_close()
