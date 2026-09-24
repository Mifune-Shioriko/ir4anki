#!/usr/bin/env python3
"""Tests for reading-mode segment edit + note-image routes (P5, 2026-09-23).

Same harness pattern as reading_test.py: real FastAPI app against a FAKE
AnkiConnect and TEMPORARY corpus/state — the live app, ~/anki-notes and
the real reading.db are untouched.

Covers /api/reading/edit:
  1. gate off → 404
  2. basic edit: text replaced on disk, chunk payload refreshed
  3. delta shift: segments AFTER the edit move; segments before untouched;
     pure-shift siblings keep their fingerprints (no fuse, no needs_resync)
  4. file_sha updates in the same transaction (status view clean)
  5. shorter edit (negative delta)
  6. container is read-only (409); editing a split CHILD stretches the
     ancestor container (end_line += delta, fingerprint recomputed)
  7. round membership survives a mid-round edit (pending untouched)
  8. validation: empty text 400, unknown file 404, unknown seg 404

Covers /api/reading/media (P5 image pipeline):
  9. upload → stored in NOTES/_assets, filename returned
 10. GET serves the bytes; basename lookup finds images anywhere in the
     corpus; traversal / bad-extension → 404; upload .txt → 400
 11. _assets excluded from the corpus scan

Run: backend/.venv/bin/python scripts/reading_edit_test.py
"""
import asyncio
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="edit-test-state-")
NOTES = Path(tempfile.mkdtemp(prefix="edit-test-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "1"
os.environ["ANKI_READING_MODE"] = "1"
os.environ["ANKI_QUICK_READ"] = "2"
os.environ["ANKI_FOCUS_READ"] = "5"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402
import app as backend  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


class FakeAnki:
    """Minimal fake: only the actions the edit/media paths can touch."""
    def __init__(self):
        self.next_note = 900001

    async def __call__(self, action, params=None, timeout=30):
        if action == "addNotes":
            nid = self.next_note
            self.next_note += 1
            return [nid]
        if action in ("findCards", "notesInfo", "cardsInfo"):
            return []
        return None


backend.anki = FakeAnki()
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")  # keep the SPA mount away

NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内有颈阔肌。

## 二、颈筋膜

分为浅、中、深三层，形成筋膜鞘。

## 三、颈动脉三角

境界：胸锁乳突肌前缘、肩胛舌骨肌上腹、二腹肌后腹。
"""

A_PATH = "2026/局部解剖学/颈部.md"


def write_note(rel: str, text: str):
    p = NOTES / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


write_note(A_PATH, NOTE_A)
# a tiny 1×1 red PNG for the upload tests
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
           "nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
PNG_BYTES = base64.b64decode(PNG_B64)


def _entry(path: str) -> dict:
    data = backend._reading_read()
    return backend._find_entry(data, path)


def _segs(path: str) -> list[dict]:
    return sorted(_entry(path).get("segments") or [], key=lambda s: s["start_line"])


def _todo_segs(path: str) -> list[dict]:
    """Leaf segments that are actually dealable (whole-file seeding 2026-09-24:
    a file joins as ONE segment; multi-segment fixtures are built by splitting,
    which leaves background gaps — those are not 'sections')."""
    return [s for s in _segs(path) if s.get("status") not in ("container", "background")]


async def _split_whole_into_sections(c, path: str, whole_id: int, text: str):
    """Cut the single whole-file segment into one todo segment per '## '
    heading, reproducing the retired chunker's section ranges EXACTLY
    (heading line up to the line before the next heading).

    One split per section, each targeting the previous bookmark TAIL:
    a single multi-selection split can't do it because contiguous
    selections merge into one child (by design). Returns the last response."""
    lines = text.split("\n")
    heads = [i + 1 for i, l in enumerate(lines) if l.startswith("## ")]
    cur = whole_id
    r = None
    for k, h in enumerate(heads):
        end = (heads[k + 1] - 1) if k + 1 < len(heads) else len(lines)
        r = await c.post("/api/reading/split", json={
            "path": path, "seg_id": cur,
            "selections": [{"start_line": h, "end_line": end}],
            "gap_policy": "bookmark"})
        if r.status_code != 200:
            return r
        tail = next((x for x in r.json().get("children", []) if x.get("tail")), None)
        if tail is None:
            break  # last section consumed the remainder
        cur = tail["seg_id"]
    return r


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("== 1. add file (whole-file seed) + split into sections + deal ==")
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("add A ok", r.json().get("ok") is True, r.text[:120])
        check("whole-file seed = 1 segment", r.json().get("segments") == 1, r.json())
        whole = _segs(A_PATH)[0]
        check("seeded segment covers whole file",
              (whole["start_line"], whole["end_line"]) == (1, len(NOTE_A.split("\n"))),
              (whole["start_line"], whole["end_line"]))
        # build the 3-section fixture the edit/container tests rely on
        r = await _split_whole_into_sections(c, A_PATH, whole["seg_id"], NOTE_A)
        check("split into sections 200", r is not None and r.status_code == 200,
              getattr(r, "text", "")[:200])
        segs = _todo_segs(A_PATH)
        check("3 sections after split", len(segs) == 3, len(segs))
        s1, s2, s3 = segs
        r = await c.post("/api/reading/start?mode=focus")
        dealt = r.json()["chunks"]
        check("focus dealt 3", len(dealt) == 3, len(dealt))
        by_key = {ch["chunk_key"]: ch for ch in dealt}

        print("== 2. basic edit: replace s1 with +2 lines ==")
        new_text = ("## 一、浅层结构（改）\n\n皮肤薄，移动性大。\n\n浅筋膜内有颈阔肌，由面神经支配。\n\n"
                    "新增一行内容。\n\n再增一行内容。")
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": s1["seg_id"], "new_text": new_text})
        check("edit 200", r.status_code == 200, r.text[:200])
        d = r.json()
        check("ok true", d.get("ok") is True)
        # old s1 span vs new: compute expected delta from line counts
        old_span = s1["end_line"] - s1["start_line"] + 1
        exp_delta = len(new_text.split("\n")) - old_span
        check(f"delta == {exp_delta}", d.get("delta") == exp_delta, d.get("delta"))
        check("payload text refreshed",
              d.get("chunk") and "再增一行内容" in d["chunk"]["text"],
              (d.get("chunk") or {}).get("text", "")[:80])
        check("payload title from rewritten heading",
              d.get("chunk", {}).get("title") == "一、浅层结构（改）",
              d.get("chunk", {}).get("title"))
        check("payload line_start unchanged",
              d.get("line_start") == s1["start_line"], d.get("line_start"))
        check("payload line_end == start+len-1",
              d.get("line_end") == s1["start_line"] + len(new_text.split("\n")) - 1,
              d.get("line_end"))

        # on-disk file
        disk = (NOTES / A_PATH).read_text(encoding="utf-8")
        check("file on disk has new text", "再增一行内容" in disk)
        check("file on disk keeps other sections", "颈动脉三角" in disk and "颈筋膜" in disk)

        # state: sha + segments
        e = _entry(A_PATH)
        check("file_sha == sha256(new file)",
              e["file_sha"] == hashlib.sha256(disk.encode()).hexdigest(), e["file_sha"])
        check("needs_resync NOT set", not e.get("needs_resync"))
        segs2 = _segs(A_PATH)
        ns1 = next(s for s in segs2 if s["seg_id"] == s1["seg_id"])
        ns2 = next(s for s in segs2 if s["seg_id"] == s2["seg_id"])
        ns3 = next(s for s in segs2 if s["seg_id"] == s3["seg_id"])
        check("s1 end_line grew", ns1["end_line"] == s1["end_line"] + exp_delta,
              (ns1["end_line"], s1["end_line"] + exp_delta))
        check("s2 shifted by delta",
              (ns2["start_line"], ns2["end_line"]) ==
              (s2["start_line"] + exp_delta, s2["end_line"] + exp_delta),
              (ns2["start_line"], ns2["end_line"]))
        check("s3 shifted by delta",
              ns3["start_line"] == s3["start_line"] + exp_delta, ns3["start_line"])
        check("pure-shift s2 keeps fingerprint (no fuse)",
              ns2["fingerprint"] == s2["fingerprint"])
        check("edited s1 fingerprint recomputed",
              ns1["fingerprint"] != s1["fingerprint"] and ns1["fingerprint"])
        # status view agrees (goes through _file_view → would re-anchor/trip
        # the fuse if sha or fingerprints were inconsistent)
        r = await c.get("/api/reading/status")
        a_sum = next(s for s in r.json()["list"] if s["path"] == A_PATH)
        check("status: 3 todo sections, no orphans, no resync",
              a_sum["todo"] == 3 and a_sum["orphans"] == 0
              and not a_sum["needs_resync"], a_sum)

        print("== 3. mid-round edit keeps round membership ==")
        r = await c.get("/api/reading/state")
        rd = r.json().get("round") or {}
        pend = rd.get("pending") or rd.get("chunks") or []
        check("round still active", rd.get("status") == "active", rd.get("status"))
        keys = {p.get("chunk_key") for p in pend} if pend and isinstance(pend[0], dict) else set()
        check("s1 seg still in round", str(s1["seg_id"]) in keys, keys)

        print("== 4. shorter edit (negative delta) on s2 ==")
        cur = _segs(A_PATH)
        cs1, cs2, cs3 = (next(s for s in cur if s["seg_id"] == x["seg_id"])
                         for x in (s1, s2, s3))
        short = "## 二、颈筋膜\n\n浅、中、深三层。"
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": s2["seg_id"], "new_text": short})
        d2 = r.json()
        exp_d2 = len(short.split("\n")) - (cs2["end_line"] - cs2["start_line"] + 1)
        check("negative delta", d2.get("delta") == exp_d2 < 0, d2.get("delta"))
        cur = _segs(A_PATH)
        ns3b = next(s for s in cur if s["seg_id"] == s3["seg_id"])
        ns1b = next(s for s in cur if s["seg_id"] == s1["seg_id"])
        check("s3 shifted up", ns3b["start_line"] == cs3["start_line"] + exp_d2,
              (ns3b["start_line"], cs3["start_line"] + exp_d2))
        check("s1 untouched (before the edit)",
              (ns1b["start_line"], ns1b["end_line"]) == (cs1["start_line"], cs1["end_line"]))
        disk = (NOTES / A_PATH).read_text(encoding="utf-8")
        check("sha consistent after 2nd edit",
              _entry(A_PATH)["file_sha"] == hashlib.sha256(disk.encode()).hexdigest())
        check("s3 fingerprint still intact",
              ns3b["fingerprint"] == s3["fingerprint"], "pure shift must not break fp")

        print("== 5. split → container read-only; child edit stretches parent ==")
        cur = _segs(A_PATH)
        cs3 = next(s for s in cur if s["seg_id"] == s3["seg_id"])
        mid = (cs3["start_line"] + cs3["end_line"]) // 2
        r = await c.post("/api/reading/split", json={
            "path": A_PATH, "seg_id": s3["seg_id"],
            "selections": [{"start_line": cs3["start_line"], "end_line": mid}],
            "gap_policy": "bookmark"})
        check("split 200", r.status_code == 200, r.text[:200])
        kids = [x for x in r.json().get("children", []) if not x.get("tail")]
        check("split produced a non-tail child", len(kids) >= 1, r.json().get("children"))
        kid = kids[0]
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": s3["seg_id"], "new_text": "x"})
        check("container edit → 409", r.status_code == 409, r.status_code)

        before_parent = next(s for s in _segs(A_PATH) if s["seg_id"] == s3["seg_id"])
        before_kid = next(s for s in _segs(A_PATH) if s["seg_id"] == kid["seg_id"])
        add_lines = "\n".join([f"补充行{i}" for i in range(3)])
        kid_text = "\n".join(
            (NOTES / A_PATH).read_text(encoding="utf-8").split("\n")
            [before_kid["start_line"] - 1 : before_kid["end_line"]]
        ) + "\n" + add_lines
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": kid["seg_id"], "new_text": kid_text})
        check("child edit 200", r.status_code == 200, r.text[:200])
        after_parent = next(s for s in _segs(A_PATH) if s["seg_id"] == s3["seg_id"])
        after_kid = next(s for s in _segs(A_PATH) if s["seg_id"] == kid["seg_id"])
        check("container end_line stretched by +3",
              after_parent["end_line"] == before_parent["end_line"] + 3,
              (after_parent["end_line"], before_parent["end_line"]))
        check("container start unchanged",
              after_parent["start_line"] == before_parent["start_line"])
        check("container fingerprint recomputed",
              after_parent["fingerprint"] != before_parent["fingerprint"])
        check("child end grew", after_kid["end_line"] == before_kid["end_line"] + 3)
        disk = (NOTES / A_PATH).read_text(encoding="utf-8")
        check("child text on disk", "补充行2" in disk)
        check("sha consistent after child edit",
              _entry(A_PATH)["file_sha"] == hashlib.sha256(disk.encode()).hexdigest())
        # status view still clean → the fuse never tripped across 3 edits
        r = await c.get("/api/reading/status")
        a_sum = next(s for s in r.json()["list"] if s["path"] == A_PATH)
        check("status clean after split+edit", not a_sum["needs_resync"], a_sum)

        print("== 6. validation paths ==")
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": kid["seg_id"], "new_text": "   \n  "})
        check("empty text → 400", r.status_code == 400, r.status_code)
        r = await c.post("/api/reading/edit", json={
            "path": "2026/不存在.md", "seg_id": 1, "new_text": "x"})
        check("unknown file → 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/edit", json={
            "path": A_PATH, "seg_id": 99999, "new_text": "x"})
        check("unknown seg → 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/edit", json={
            "path": "../outside.md", "seg_id": 1, "new_text": "x"})
        check("traversal file → 404", r.status_code == 404, r.status_code)

        print("== 7. media upload + serve (P5) ==")
        r = await c.post("/api/reading/media/upload",
                         files={"file": ("paste.png", PNG_BYTES, "image/png")})
        check("upload 200", r.status_code == 200, r.text[:120])
        fname = r.json().get("filename", "")
        check("filename returned", fname.endswith(".png") and fname.startswith("note-"), fname)
        stored = NOTES / "_assets" / fname
        check("file stored in _assets", stored.is_file() and stored.read_bytes() == PNG_BYTES)
        r = await c.get(f"/api/reading/media/{fname}")
        check("GET serves bytes", r.status_code == 200 and r.content == PNG_BYTES,
              r.status_code)
        check("content-type image/png", r.headers.get("content-type") == "image/png",
              r.headers.get("content-type"))
        # basename lookup finds images ANYWHERE in the corpus
        write_note("2026/局部解剖学/fig1.png", "x")  # not valid png but serves
        manual = NOTES / "2026/局部解剖学/fig1.png"
        r = await c.get("/api/reading/media/fig1.png")
        check("basename lookup outside _assets", r.status_code == 200 and r.content == b"x",
              r.status_code)
        manual.unlink(missing_ok=True)
        r = await c.get("/api/reading/media/nope.png")
        check("missing image → 404", r.status_code == 404, r.status_code)
        r = await c.get("/api/reading/media/../../etc/passwd")
        check("traversal → 404", r.status_code == 404, r.status_code)
        r = await c.get("/api/reading/media/evil.md")
        check("non-image ext → 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/media/upload",
                         files={"file": ("note.txt", b"hello", "text/plain")})
        check("upload .txt → 400", r.status_code == 400, r.status_code)
        r = await c.get("/api/reading/corpus")
        corpus_paths = [f["path"] for f in r.json()["files"]]
        check("_assets excluded from corpus",
              not any(p.startswith("_assets/") for p in corpus_paths), corpus_paths)

        await c.post("/api/reading/finish")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def gate_off_check():
    backend.READING_MODE = False
    transport = httpx.ASGITransport(app=backend.app)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/reading/edit", json={
                "path": A_PATH, "seg_id": 1, "new_text": "x"})
            check("gate off: edit 404", r.status_code == 404, r.status_code)
            r = await c.get("/api/reading/media/x.png")
            check("gate off: media 404", r.status_code == 404, r.status_code)
            r = await c.post("/api/reading/media/upload",
                             files={"file": ("x.png", PNG_BYTES, "image/png")})
            check("gate off: upload 404", r.status_code == 404, r.status_code)
    asyncio.get_event_loop().run_until_complete(run())
    backend.READING_MODE = True


print("== 0. gate OFF ==")
gate_off_check()
print("== main suite ==")
rc = asyncio.run(main())
sys.exit(rc)
