#!/usr/bin/env python3
"""API-level test for the review-app note-panel endpoints (in-process, no restart).

Verifies /api/note/sections + /api/notes/raw against the REAL AnkiConnect
and the REAL notes-rag service (both live). Read-only: no cards answered,
no rounds opened, no writes anywhere.
"""
import asyncio
import sys

sys.path.insert(0, "/home/shioriko/anki-review-app/backend")
import httpx

import app as backend

FAILS = []

def check(cond, msg):
    if cond:
        print(f"  ok  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL {msg}")

async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # find a real 问答题 note that came from the anatomy batch
        r = await c.post("/api/card/add/info")  # harmless GET-ish info call
        # grab a note id from Anki directly via the backend's client
        ids = await backend.anki("findNotes", {"query": 'note:问答题 "腋鞘"'}) or []
        check(len(ids) > 0, f"found 腋鞘 test note ({ids[0] if ids else 'none'})")
        nid = ids[0]

        # sections endpoint
        r = await c.get(f"/api/note/sections?note_id={nid}&top_k=3")
        check(r.status_code == 200, f"sections HTTP {r.status_code}")
        d = r.json()
        secs = d.get("sections", [])
        check(len(secs) > 0, f"{len(secs)} sections returned")
        top = secs[0]
        check("上肢" in top["file"], f"top-1 file: {top['file']}")
        check(top["line_start"] >= 1, f"line_start={top['line_start']}")
        check(0 < top["score"] <= 1, f"score={top['score']}")
        check(all(k in top for k in ("title", "heading_path", "snippet", "year")),
              "all fields present")

        # raw file endpoint
        r = await c.get("/api/notes/raw", params={"path": top["file"]})
        check(r.status_code == 200, f"raw HTTP {r.status_code}")
        raw = r.json()
        check(raw["path"] == top["file"], "raw path echoed")
        check(len(raw["text"]) > 1000, f"raw text {len(raw['text'])} chars")
        lines = raw["text"].split("\n")
        check(len(lines) >= top["line_end"],
              f"file has {len(lines)} lines >= line_end {top['line_end']}")

        # traversal guard
        r = await c.get("/api/notes/raw", params={"path": "../../.hermes/config.yaml"})
        check(r.status_code in (400, 404), f"traversal blocked → HTTP {r.status_code}")
        r = await c.get("/api/notes/raw", params={"path": "/etc/passwd"})
        check(r.status_code in (400, 404), f"abs path blocked → HTTP {r.status_code}")

        # missing file
        r = await c.get("/api/notes/raw", params={"path": "2026/nope.md"})
        check(r.status_code == 404, f"missing file → 404")

        # nonexistent note → 404 from notes-rag, sections must fail-soft []
        r = await c.get("/api/note/sections?note_id=123456789012345&top_k=3")
        check(r.status_code == 200, f"unknown note → HTTP {r.status_code} (fail-soft)")
        check(r.json().get("sections") == [], "unknown note → empty sections")

        # fail-soft when notes-rag is DOWN: point at a dead port
        old = backend.NOTES_RAG
        backend.NOTES_RAG = "http://127.0.0.1:9"
        try:
            r = await c.get(f"/api/note/sections?note_id={nid}&top_k=3")
            check(r.status_code == 200 and r.json()["sections"] == [],
                  "notes-rag down → fail-soft []")
            r = await c.get("/api/notes/raw", params={"path": top["file"]})
            check(r.status_code == 502, f"raw with notes-rag down → HTTP {r.status_code}")
        finally:
            backend.NOTES_RAG = old

        # fetch_note_sections direct unit
        secs2 = await backend.fetch_note_sections(nid, 3)
        check(len(secs2) > 0 and secs2[0]["file"] == top["file"],
              "fetch_note_sections matches endpoint")
        check(await backend.fetch_note_sections(None) == [], "None note → []")

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        sys.exit(1)
    print("ALL PASS")

asyncio.run(main())
