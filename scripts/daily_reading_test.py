"""Daily reading plan, exit-resume idempotency, and extra reading test."""
import asyncio, os, sys
from pathlib import Path
from native_fixture import configure, NativeData, run_closed

os.environ.update(ANKI_READING_MODE='1', ANKI_PREVIEW_MODE='1')
configure()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as b
import httpx

b.fire_and_forget_sync = lambda: None


async def run():
    seed = NativeData(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url='http://fixture') as c:
        async def req(path, **kw):
            r = await c.post(path, **kw)
            assert r.status_code == 200, r.text
            return r.json()

        async def state():
            r = await c.get('/api/flow/state')
            assert r.status_code == 200, r.text
            return r.json()

        await req('/api/flow/exit')
        seed.clear()
        data = b._reading_read()
        data['list'] = []
        data['round'] = None
        b._reading_write(data)
        b._daily_reading_write(None)

        # 1. Create two files with multiple sections
        for name in ['fileA.md', 'fileB.md']:
            content = """# Section 1
Paragraph 1 with enough content for a reading chunk.

# Section 2
Paragraph 2 with enough content for a reading chunk.

# Section 3
Paragraph 3 with enough content for a reading chunk.
"""
            (Path(os.environ['ANKI_NOTES_DIR']) / name).write_text(content)
            await req('/api/reading/list/add', json={'path': name})
            # Split into sections
            entry = b._find_entry(b._reading_read(), name)
            # Create selections for section 1 and 2
            await req('/api/reading/split', json={
                'path': name,
                'seg_id': entry['segments'][0]['seg_id'],
                'selections': [
                    {'start_line': 1, 'end_line': 3},
                    {'start_line': 4, 'end_line': 6},
                ]
            })

        # Check dealable preview before starting
        s0 = await state()
        assert s0.get('reading_daily_done') is False
        assert s0.get('reading_dealable') == 4  # 2 per file * 2 files

        # 2. Start round 1 -> deals 4 segments
        d1 = await req('/api/flow/start')
        assert d1['flow']['reading_slots'] == 4, f"expected 4 reading slots, got {d1['flow']['reading_slots']}"
        chunks1 = d1['reading_round']['chunks']
        assert len(chunks1) == 4

        # Complete the first chunk, skip the second chunk
        ch0 = chunks1[0]
        await req('/api/reading/act', params=dict(path=ch0['path'], chunk_key=ch0['chunk_key'], action='complete'))
        s_mid = await state()
        ch1 = s_mid['reading_round']['chunks'][0]
        await req('/api/reading/act', params=dict(path=ch1['path'], chunk_key=ch1['chunk_key'], action='skip'))

        # Now 2 chunks were completed/skipped.
        # 3. Simulate exiting halfway
        await req('/api/flow/exit')

        # Check state after exit
        s_after_exit = await state()
        assert s_after_exit.get('reading_daily_done') is False
        # Only 2 remaining segments from today's plan!
        assert s_after_exit.get('reading_dealable') == 2, f"expected 2 dealable remaining, got {s_after_exit.get('reading_dealable')}"
        assert s_after_exit.get('reading_daily_remaining') == 2
        assert s_after_exit.get('reading_daily_planned') == 4

        # 4. Start a new round today (without extra_reading)
        d2 = await req('/api/flow/start')
        assert d2['flow']['reading_slots'] == 2, f"expected 2 reading slots, got {d2['flow']['reading_slots']}"
        chunks2 = d2['reading_round']['chunks']
        assert len(chunks2) == 2
        # Verify chunks2 are indeed the 2 uncompleted ones
        keys2 = {(c['path'], str(c['chunk_key'])) for c in chunks2}
        assert (ch0['path'], str(ch0['chunk_key'])) not in keys2
        assert (ch1['path'], str(ch1['chunk_key'])) not in keys2

        # Complete the remaining 2 chunks
        await req('/api/reading/act', params=dict(path=chunks2[0]['path'], chunk_key=chunks2[0]['chunk_key'], action='complete'))
        s_mid2 = await state()
        ch_last = s_mid2['reading_round']['chunks'][0]
        await req('/api/reading/act', params=dict(path=ch_last['path'], chunk_key=ch_last['chunk_key'], action='complete'))

        # Now all 4 planned chunks are finished!
        s_done = await state()
        assert s_done.get('reading_daily_done') is True
        assert s_done.get('reading_daily_remaining') == 0
        assert s_done.get('reading_dealable') == 0
        assert s_done.get('reading_dealable_extra', 0) > 0

        # Complete round 2
        await req('/api/flow/exit')

        # 5. Starting again without extra_reading deals 0 reading segments
        d3 = await req('/api/flow/start')
        assert d3['flow']['reading_slots'] == 0, f"expected 0 reading slots, got {d3['flow']['reading_slots']}"
        await req('/api/flow/exit')

        # 6. Starting with extra_reading deals a fresh batch from the frontier!
        d4 = await req('/api/flow/start', params={'extra_reading': 'true'})
        assert d4['flow']['reading_slots'] > 0, f"expected extra reading slots, got {d4['flow']['reading_slots']}"
        await req('/api/flow/exit')

        print("All daily reading plan and extra reading tests passed!")


if __name__ == '__main__':
    asyncio.run(run_closed(b, run()))
