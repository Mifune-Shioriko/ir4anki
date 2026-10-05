#!/usr/bin/env python3
"""API tests for file management (user spec 2026-10-06).

Covers /api/files/{upload,rename,move,delete} — the app is the single source
of truth for the ~/anki-notes corpus. The critical guarantee under test:
RENAME / MOVE migrate EVERY path-keyed reading-store row (rfiles, segments,
rcards, rorphans, the active round's pending list, and archived progress) in
one transaction, so reading progress survives a rename verbatim (file_sha
unchanged → segment line numbers/fingerprints stay valid). DELETE is hard:
disk + all reading progress go (Anki cards untouched). Traversal / protected
dir / bad-name are all rejected.

Harness pattern = list_dealing_test.py: real FastAPI app, FAKE AnkiConnect,
TEMPORARY corpus/state. PREVIEW_MODE off (dealing/gate never touch Anki).

Run: backend/.venv/bin/python scripts/file_mgmt_test.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="filemgmt-state-")
NOTES = Path(tempfile.mkdtemp(prefix="filemgmt-notes-"))
os.environ["ANKI_STATE_DIR"] = STATE
os.environ["ANKI_NOTES_DIR"] = str(NOTES)
os.environ["ANKI_PREVIEW_MODE"] = "0"
os.environ["ANKI_READING_MODE"] = "1"

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
    async def __call__(self, action, params=None, timeout=30):
        if action == "findCards":
            return []
        return None


backend.anki = FakeAnki()
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")

NOTE_BODY = ("# 颈部\n\n## 一、浅层结构\n\n"
             "皮肤薄，移动性大。浅筋膜内含有颈阔肌，由面神经支配。\n\n"
             "## 二、颈筋膜\n\n分为浅、中、深三层，形成筋膜鞘。\n")
# a note that references an image by BARE BASENAME (the corpus convention).
# It references pic.png, which ALREADY exists elsewhere in the corpus
# (2026/pic.png, created in §3) → uploading this image must trigger the
# basename-collision auto-prefix + reference rewrite.
NOTE_WITH_IMG = ("# 图解\n\n正文一行说明文字内容。\n\n"
                 "![插图](pic.png)\n\n结尾另一行。\n")

# corpus layout
(NOTES / "2026" / "解剖").mkdir(parents=True)
(NOTES / "2026" / "解剖" / "颈部.md").write_text(NOTE_BODY, encoding="utf-8")
A_REL = "2026/解剖/颈部.md"


def _state():
    return backend._reading_read()


def _entry(path):
    return backend._find_entry(_state(), path)


def _segs(path):
    e = _entry(path)
    return e.get("segments") or [] if e else []


def _record_card(path, seg_id, note_id):
    backend._reading_record_card({"path": path, "chunk_key": str(seg_id)}, note_id)


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        print("== 0. baseline: add A to the reading list, split + record a card ==")
        r = await c.post("/api/reading/list/add", json={"path": A_REL})
        check("add A ok", r.json().get("ok") is True, r.text[:150])
        whole = _segs(A_REL)[0]
        a_sha = _entry(A_REL).get("file_sha")
        # make a card so rcards provenance exists (note_id 900001)
        _record_card(A_REL, whole["seg_id"], 900001)
        check("card recorded on A's segment",
              900001 in (_segs(A_REL)[0].get("cards_created") or []),
              _segs(A_REL)[0])

        print("== 1. rename migrates list + segments + rcards + sha ==")
        r = await c.post("/api/files/rename",
                         json={"path": A_REL, "new_name": "颈部（改）"})
        check("rename ok", r.json().get("ok") is True, r.text[:200])
        NEW_REL = r.json().get("path")
        check("rename auto-appends .md", NEW_REL == "2026/解剖/颈部（改）.md",
              NEW_REL)
        check("old path gone on disk", not (NOTES / A_REL).exists())
        check("new path exists on disk", (NOTES / NEW_REL).exists())
        check("reading list now has the NEW path", _entry(NEW_REL) is not None)
        check("reading list no longer has the OLD path", _entry(A_REL) is None)
        check("segments migrated (still 1)", len(_segs(NEW_REL)) == 1,
              len(_segs(NEW_REL)))
        check("cards_created provenance survived rename",
              900001 in (_segs(NEW_REL)[0].get("cards_created") or []),
              _segs(NEW_REL)[0])
        check("file_sha unchanged (content untouched)",
              _entry(NEW_REL).get("file_sha") == a_sha,
              (_entry(NEW_REL).get("file_sha"), a_sha))
        check("status preserved across rename",
              _segs(NEW_REL)[0].get("status") == "active",
              _segs(NEW_REL)[0].get("status"))
        # /api/files/list reflects the new name; the raw reader follows it
        r = await c.get("/api/files/list")
        listed = {f["path"] for f in r.json()["files"]}
        check("files/list shows new path", NEW_REL in listed, sorted(listed))
        r = await c.get("/api/files/raw", params={"path": NEW_REL})
        check("files/raw reads new path", r.status_code == 200
              and "颈阔肌" in r.json()["text"], r.text[:120])

        print("== 2. rename into an existing name → 409 ==")
        (NOTES / "2026" / "解剖" / "占位.md").write_text("# 占位\n\n内容内容内容内容。\n", encoding="utf-8")
        r = await c.post("/api/files/rename",
                         json={"path": NEW_REL, "new_name": "占位"})
        check("rename collision → 409", r.status_code == 409, r.status_code)

        print("== 3. rename rejects bad names + images ==")
        r = await c.post("/api/files/rename",
                         json={"path": NEW_REL, "new_name": "../逃逸"})
        check("rename with slash → 400", r.status_code == 400, r.status_code)
        r = await c.post("/api/files/rename",
                         json={"path": NEW_REL, "new_name": ".hidden"})
        check("rename to dot-name → 400", r.status_code == 400, r.status_code)
        # an image can't be renamed (v1 limitation — breaks basename refs)
        (NOTES / "2026" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        r = await c.post("/api/files/rename",
                         json={"path": "2026/pic.png", "new_name": "pic2.png"})
        check("rename image → 400 (v1 limitation)", r.status_code == 400, r.status_code)

        print("== 4. move a file to another dir ==")
        (NOTES / "2027").mkdir(exist_ok=True)
        r = await c.post("/api/files/move",
                         json={"path": NEW_REL, "new_dir": "2027"})
        check("move ok", r.json().get("ok") is True, r.text[:200])
        MOVED_REL = r.json().get("path")
        check("moved to 2027/颈部（改）.md", MOVED_REL == "2027/颈部（改）.md", MOVED_REL)
        check("old location gone on disk", not (NOTES / NEW_REL).exists())
        check("reading list follows the move", _entry(MOVED_REL) is not None
              and _entry(NEW_REL) is None)
        check("provenance survived the move",
              900001 in (_segs(MOVED_REL)[0].get("cards_created") or []))

        print("== 5. move a DIRECTORY migrates every file under it ==")
        # 2026/解剖 currently holds 占位.md + pic is elsewhere; add another
        (NOTES / "2026" / "解剖" / "上肢.md").write_text(
            "# 上肢\n\n## 一、腋窝\n\n四壁一顶一底。内容：腋动脉、腋静脉、臂丛。\n",
            encoding="utf-8")
        await c.post("/api/reading/list/add", json={"path": "2026/解剖/上肢.md"})
        B_REL = "2026/解剖/上肢.md"
        b_sha = _entry(B_REL).get("file_sha")
        r = await c.post("/api/files/move", json={"path": "2026/解剖", "new_dir": ""})
        check("move dir ok", r.json().get("ok") is True, r.text[:200])
        check("dir moved to root", (NOTES / "解剖" / "上肢.md").exists(),
              [str(p.relative_to(NOTES)) for p in NOTES.rglob("*.md")])
        B_NEW = "解剖/上肢.md"
        check("listed file B followed the dir move", _entry(B_NEW) is not None
              and _entry(B_REL) is None,
              [e["path"] for e in _state()["list"]])
        check("B sha unchanged after dir move",
              _entry(B_NEW).get("file_sha") == b_sha)

        print("== 6. move into itself rejected ==")
        r = await c.post("/api/files/move", json={"path": "解剖", "new_dir": "解剖"})
        check("move dir into itself → 400", r.status_code == 400, r.status_code)

        print("== 7. rename/move keep the ACTIVE round's pending in sync ==")
        B_NEW2 = B_NEW
        r = await c.post("/api/reading/start?mode=daily")
        dealt = r.json().get("chunks", [])
        check("round dealt", len(dealt) >= 1, dealt)
        b_dealt = next((ch for ch in dealt if ch["path"] == B_NEW), None)
        check("B is in the round (its file's one slot)", b_dealt is not None,
              [ch["path"] for ch in dealt])
        if b_dealt:
            r2 = await c.post("/api/files/rename",
                              json={"path": B_NEW, "new_name": "臂"})
            check("rename B mid-round ok", r2.json().get("ok") is True, r2.text[:150])
            B_NEW2 = r2.json()["path"]
            st = await c.get("/api/reading/state")
            pend = [p["path"] for p in (st.json().get("round") or {}).get("chunks", [])]
            check("active round pending re-pointed to the renamed path",
                  B_NEW2 in pend and B_NEW not in pend, pend)
        await c.post("/api/reading/finish")

        print("== 8. archived progress follows a rename ==")
        # remove B from the list (parks progress in archive), rename it, then
        # re-add → progress should come back under the NEW path
        r = await c.post("/api/reading/list/remove", json={"path": B_NEW2})
        check("remove B ok", r.json().get("ok") is True, r.text[:150])
        archived = _state().get("archive") or {}
        check("B progress archived under its path", B_NEW2 in archived,
              list(archived.keys()))
        r = await c.post("/api/files/rename", json={"path": B_NEW2, "new_name": "上肢2"})
        check("rename archived B ok", r.json().get("ok") is True, r.text[:150])
        B_NEW3 = r.json()["path"]
        archived2 = _state().get("archive") or {}
        check("archive key migrated to the new path",
              B_NEW3 in archived2 and B_NEW2 not in archived2,
              list(archived2.keys()))

        print("== 9. delete: 防呆 confirm + hard removal ==")
        r = await c.post("/api/files/delete",
                         json={"path": MOVED_REL, "confirm": "错的名字.md"})
        check("delete with wrong confirm → 400", r.status_code == 400, r.status_code)
        check("file still exists after failed delete", (NOTES / MOVED_REL).exists())
        r = await c.post("/api/files/delete",
                         json={"path": MOVED_REL, "confirm": "颈部（改）.md"})
        check("delete with correct confirm ok", r.json().get("ok") is True, r.text[:200])
        check("file gone from disk", not (NOTES / MOVED_REL).exists())
        check("reading progress purged (entry gone)", _entry(MOVED_REL) is None)

        print("== 10. delete a DIRECTORY recursively + purges all ==")
        # 解剖 dir holds 占位.md + 臂.md(B_NEW3) ; re-add B to give it progress
        r = await c.post("/api/reading/list/add", json={"path": B_NEW3})
        check("re-add B under new name ok", r.json().get("ok") is True, r.text[:200])
        r = await c.post("/api/files/delete", json={"path": "解剖", "confirm": "解剖"})
        check("delete dir ok", r.json().get("ok") is True, r.text[:200])
        check("dir gone from disk", not (NOTES / "解剖").exists())
        check("all dir files purged from reading list",
              all(_entry(p) is None for p in (B_NEW3, "解剖/占位.md")),
              [e["path"] for e in _state()["list"]])

        print("== 11. upload: .md + folder subtree + image prefix + ref rewrite ==")
        # single .md upload into a subdir
        r = await c.post("/api/files/upload", data={
            "rel_paths": "新笔记.md", "dir": "2027", "on_conflict": "error"},
            files={"files": ("新笔记.md", NOTE_BODY.encode(), "text/markdown")})
        check("upload single .md ok", r.json().get("ok") is True, r.text[:250])
        check("uploaded file landed in the target dir",
              (NOTES / "2027" / "新笔记.md").exists())
        # a note + its image where the image BASENAME already exists in the
        # corpus (2026/pic.png from §3) → image auto-prefixed + ref rewritten
        files = [
            ("files", ("图解.md", NOTE_WITH_IMG.encode(), "text/markdown")),
            ("files", ("pic.png", b"\x89PNG\r\n\x1a\n", "image/png")),
        ]
        data = {"rel_paths": ["图解.md", "pic.png"], "dir": "", "on_conflict": "error"}
        r = await c.post("/api/files/upload", data=data, files=files)
        check("upload note+image ok", r.json().get("ok") is True, r.text[:300])
        renamed = r.json().get("images_renamed") or {}
        check("image basename collision auto-renamed", "pic.png" in renamed,
              renamed)
        new_img = renamed.get("pic.png")
        if new_img:
            check("prefixed image written to disk", (NOTES / new_img).exists(), new_img)
            md_text = (NOTES / "图解.md").read_text(encoding="utf-8")
            check("md image reference rewritten to the prefixed name",
                  f"]({new_img})" in md_text and "](pic.png)" not in md_text,
                  md_text)

        print("== 12. upload conflict policy ==")
        # re-upload 新笔记.md into 2027 → 409 with error policy
        r = await c.post("/api/files/upload", data={
            "rel_paths": "新笔记.md", "dir": "2027", "on_conflict": "error"},
            files={"files": ("新笔记.md", NOTE_BODY.encode(), "text/markdown")})
        check("upload .md collision → 409 (error policy)", r.status_code == 409,
              r.status_code)
        import json as _json
        detail = _json.loads(r.json().get("detail", "{}"))
        check("409 detail lists the conflicting path",
              "新笔记.md" in (detail.get("conflicts") or []), detail)
        # skip policy → skipped, original untouched
        before = (NOTES / "2027" / "新笔记.md").read_text(encoding="utf-8")
        r = await c.post("/api/files/upload", data={
            "rel_paths": "新笔记.md", "dir": "2027", "on_conflict": "skip"},
            files={"files": ("新笔记.md", ("# 不同\n\n另一段内容。\n").encode(), "text/markdown")})
        check("upload skip policy ok", r.json().get("ok") is True, r.text[:200])
        saved = r.json().get("saved") or []
        check("skip policy reports skipped", any(s.get("action") == "skipped" for s in saved), saved)
        check("skip policy left the original untouched",
              (NOTES / "2027" / "新笔记.md").read_text(encoding="utf-8") == before)
        # rename policy → creates 新笔记 (1).md
        r = await c.post("/api/files/upload", data={
            "rel_paths": "新笔记.md", "dir": "2027", "on_conflict": "rename"},
            files={"files": ("新笔记.md", NOTE_BODY.encode(), "text/markdown")})
        check("upload rename policy ok", r.json().get("ok") is True, r.text[:200])
        check("rename policy created 新笔记 (1).md",
              (NOTES / "2027" / "新笔记 (1).md").exists())

        print("== 13. upload rejects non-md/non-image + traversal ==")
        r = await c.post("/api/files/upload", data={
            "rel_paths": "x.txt", "dir": "", "on_conflict": "error"},
            files={"files": ("x.txt", b"hello", "text/plain")})
        check("upload .txt → 400", r.status_code == 400, r.status_code)
        r = await c.post("/api/files/upload", data={
            "rel_paths": "../逃逸.md", "dir": "", "on_conflict": "error"},
            files={"files": ("逃逸.md", NOTE_BODY.encode(), "text/markdown")})
        check("upload traversal rel_path → 400", r.status_code == 400, r.status_code)

        print("== 14. traversal + protected-dir rejection on all endpoints ==")
        for ep, body in [
            ("/api/files/rename", {"path": "../../etc/passwd", "new_name": "x"}),
            ("/api/files/rename", {"path": "/etc/passwd", "new_name": "x"}),
            ("/api/files/move", {"path": "../../etc", "new_dir": ""}),
            ("/api/files/delete", {"path": "../outside.md", "confirm": "outside.md"}),
            ("/api/files/delete", {"path": "_assets", "confirm": "_assets"}),
            ("/api/files/rename", {"path": ".obsidian", "new_name": "x"}),
        ]:
            r = await c.post(ep, json=body)
            check(f"{ep} {body['path']} rejected (4xx)",
                  400 <= r.status_code < 500, (r.status_code, r.text[:100]))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
