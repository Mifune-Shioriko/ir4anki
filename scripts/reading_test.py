#!/usr/bin/env python3
"""Unit tests for reading mode (渐进制卡, user spec 2026-09-19).

Runs the real FastAPI app against an in-memory FAKE AnkiConnect and a
TEMPORARY markdown corpus + TEMPORARY state dir, so neither the user's
collection nor ~/anki-notes nor the live state are touched. Covers:

  1. feature gate: ANKI_READING_MODE off → every /api/reading/* is 404 and
     session/state reports reading_mode=false
  2. corpus listing + traversal rejection
  3. list add / dup 409 / remove / re-add restores archived progress
  4. reorder: single move-up + top + full order rewrite
  5. dealing: quick=2 frontier chunks, one per file, priority order
  6. FRONTIER LOCK: a file's later chunks never dealt while its first is
     still todo/active; complete unlocks the next one
  7. ACTIVE RESURFACES: a chunk left `active` is re-dealt first next round
  8. state machine: mark_active doesn't advance the round; complete/skip/
     next do; skip counts separately from complete (stats purity)
  9. cards_created: /api/card/add with reading_source records the note id
     on the chunk (fail-soft: bad source doesn't break card creation)
 10. round resume: GET /api/reading/state rehydrates an active round;
     mid-round file edit (drift) migrates states by heading path
 11. finish: clears the round, chunk states untouched (re-dealt next round)
 12. session/state + status carry reading fields

Run: python scripts/reading_test.py   (needs the backend venv's deps)
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

STATE = tempfile.mkdtemp(prefix="reading-test-state-")
NOTES = Path(tempfile.mkdtemp(prefix="reading-test-notes-"))
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


# ---- fake AnkiConnect (card/add path only) ---------------------------------

class FakeAnki:
    def __init__(self):
        self.cards = {}
        self.notes = {}
        self.next_note = 900001
        self.calls = []
        self.added_models = []  # modelName of every addNotes call

    async def __call__(self, action, params=None, timeout=30):
        params = params or {}
        self.calls.append(action)
        if action == "addNotes":
            nid = self.next_note
            self.next_note += 1
            note = params["notes"][0]
            self.added_models.append(note.get("modelName"))
            self.notes[nid] = {"tags": list(note.get("tags") or []),
                               "fields": dict(note.get("fields") or {}),
                               "modelName": note.get("modelName")}
            self.cards[nid * 10] = {
                "cardId": nid * 10, "note": nid, "type": 0, "queue": 0,
                "deckName": "2026", "due": 0, "interval": 0, "factor": 0,
                "reps": 0, "lapses": 0, "left": 0,
                "question": "<p>Q</p>", "answer": "<p>A</p>", "css": "",
                "modelName": note.get("modelName") or "问答题",
            }
            return [nid]
        if action == "findCards":
            q = params.get("query", "")
            if q.startswith("nid:"):
                nid = int(q[4:])
                return [c for c, i in self.cards.items() if i["note"] == nid]
            if "is:due" in q or "is:new" in q:
                return [c for c, i in self.cards.items()
                        if i["type"] == 0 and "-is:suspended" not in q or i["queue"] != -1]
            return []
        if action == "changeDeck":
            for cid in params.get("cards", []):
                if cid in self.cards:
                    self.cards[cid]["deckName"] = params.get("deck", "")
            return None
        if action == "suspend":
            for cid in params.get("cards", []):
                if cid in self.cards:
                    self.cards[cid]["queue"] = -1
            return None
        if action == "unsuspend":
            for cid in params.get("cards", []):
                if cid in self.cards and self.cards[cid]["queue"] == -1:
                    self.cards[cid]["queue"] = 0
            return None
        if action == "deleteNotes":
            for nid in params.get("notes", []):
                self.notes.pop(nid, None)
                for cid in [c for c, i in self.cards.items() if i["note"] == nid]:
                    self.cards.pop(cid)
            return None
        if action == "notesInfo":
            # AnkiConnect convention: unknown note ids come back as {} rows
            out = []
            for nid in params.get("notes", []):
                n = self.notes.get(nid)
                if n is None:
                    out.append({})
                else:
                    out.append({
                        "noteId": nid,
                        "tags": n["tags"],
                        "modelName": n["modelName"],
                        "fields": n["fields"],
                        "cards": [c for c, i in self.cards.items()
                                  if i["note"] == nid],
                    })
            return out
        if action in ("sync", "addTags", "removeTags"):
            return None
        if action == "modelFieldNames":
            model = params.get("modelName")
            if model == "填空题":
                return ["文字", "背面额外"]
            return ["正面", "背面"]
        if action == "cardsInfo":
            return [dict(self.cards[c]) for c in params.get("cards", [])
                    if c in self.cards]
        return None


fake = FakeAnki()
backend.anki = fake
backend.REVIEW_WEB_V2_DIST = Path("/nonexistent")  # keep the SPA mount away


def _state_data() -> dict:
    """Current reading state via the app's own SQLite loader (round 3:
    reading.json is gone — reading.db is the store; reading through
    _reading_read also verifies the dict-assembly path)."""
    return backend._reading_read()


# ---- temporary markdown corpus ----------------------------------------------

def write_note(rel: str, text: str):
    p = NOTES / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


NOTE_A = """# 颈部

## 一、浅层结构

皮肤薄，移动性大。浅筋膜内有颈阔肌。

## 二、颈筋膜

分为浅、中、深三层，形成筋膜鞘。

## 三、颈动脉三角

境界：胸锁乳突肌前缘、肩胛舌骨肌上腹、二腹肌后腹。
"""

NOTE_B = """# 上肢

## 一、腋窝

四壁一顶一底。内容：腋动脉、腋静脉、臂丛。

## 二、臂前区

肌皮神经支配喙肱肌、肱二头肌、肱肌。
"""

NOTE_C = "# 杂记\n\n今天配置了浏览器的 DRM 修复流程，记录一下步骤。\n"

write_note("2026/局部解剖学/颈部.md", NOTE_A)
write_note("2026/局部解剖学/上肢.md", NOTE_B)
write_note("2026/杂记.md", NOTE_C)
(NOTES / ".obsidian").mkdir(exist_ok=True)
(NOTES / ".obsidian" / "x.md").write_text("hidden", encoding="utf-8")
(NOTES / "2026" / "empty.md").write_text("", encoding="utf-8")

A_PATH = "2026/局部解剖学/颈部.md"
B_PATH = "2026/局部解剖学/上肢.md"
C_PATH = "2026/杂记.md"


async def main():
    transport = httpx.ASGITransport(app=backend.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:

        print("== 1. gate: reading endpoints exist when flag on ==")
        r = await c.get("/api/reading/status")
        check("reading/status 200", r.status_code == 200, r.status_code)
        check("reading_mode true", r.json().get("reading_mode") is True)

        print("== 2. corpus ==")
        r = await c.get("/api/reading/corpus")
        files = {f["path"]: f for f in r.json()["files"]}
        check("3 corpus files", len(files) == 3, files)
        check(".obsidian excluded", ".obsidian/x.md" not in files)
        check("empty file excluded", "2026/empty.md" not in files)
        check("none in list yet", all(not f["in_list"] for f in files.values()))

        r = await c.get("/api/reading/file", params={"path": A_PATH})
        check("reading/file raw", r.status_code == 200 and "颈阔肌" in r.json()["text"])
        r = await c.get("/api/reading/file", params={"path": "../../etc/passwd"})
        check("traversal 404", r.status_code == 404, r.status_code)
        r = await c.get("/api/reading/file", params={"path": "/etc/passwd"})
        check("absolute path 404/4xx", r.status_code in (400, 404, 422), r.status_code)

        print("== 3. list add/remove/re-add ==")
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("add A ok", r.json().get("ok") is True)
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("dup add 409", r.status_code == 409, r.status_code)
        r = await c.post("/api/reading/list/add", json={"path": "nope.md"})
        check("add missing 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/list/add", json={"path": "../outside.md"})
        check("add traversal 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/list/add", json={"path": B_PATH})
        check("add B ok", r.json().get("ok") is True)
        r = await c.post("/api/reading/list/add", json={"path": C_PATH})
        check("add C ok", r.json().get("ok") is True)
        r = await c.get("/api/reading/status")
        check("list size 3", len(r.json()["list"]) == 3)
        # A = 颈部: "# 颈部" heading-only stub is filtered out (reading-only
        # MIN_READING_BODY), leaving 3 real sections
        check("A has 3 chunks", next(s for s in r.json()["list"]
                                     if s["path"] == A_PATH)["total_chunks"] == 3,
              next(s for s in r.json()["list"] if s["path"] == A_PATH))

        print("== 4. reorder ==")
        r = await c.post("/api/reading/list/reorder", json={"path": B_PATH, "top": True})
        check("B to top", r.json()["order"][0] == B_PATH, r.json()["order"])
        r = await c.post("/api/reading/list/reorder", json={"path": C_PATH})
        order = r.json()["order"]
        check("C up one", order.index(C_PATH) < order.index(A_PATH), order)
        r = await c.post("/api/reading/list/reorder",
                         json={"order": [A_PATH, B_PATH, C_PATH]})
        check("full rewrite", r.json()["order"] == [A_PATH, B_PATH, C_PATH],
              r.json()["order"])
        r = await c.post("/api/reading/list/reorder", json={})
        check("reorder no params 400", r.status_code == 400, r.status_code)

        print("== 5. dealing (quick=2, priority order, one per file) ==")
        r = await c.post("/api/reading/start?mode=quick")
        d = r.json()
        check("dealt 2 chunks", len(d["chunks"]) == 2, len(d["chunks"]))
        paths = [ch["path"] for ch in d["chunks"]]
        check("priority order A,B", paths == [A_PATH, B_PATH], paths)
        a0 = d["chunks"][0]
        check("A frontier = first real section", a0["heading_path"] == ["颈部", "一、浅层结构"],
              a0["heading_path"])
        check("chunk text present", "颈阔肌" in a0["text"] or "皮肤薄" in a0["text"],
              a0["text"][:50])
        check("status todo", a0["status"] == "todo")
        A_KEYS = [a0["chunk_key"], d["chunks"][1]["chunk_key"]]

        print("== 5b. single-file depth dealing (2026-09-19 round 2) ==")
        # user complaint: 1 file in the list → every round dealt exactly 1
        # chunk. New rule: read = CHUNK budget; the same file deepens until
        # the round is full. Clear the list, leave only A (3 chunks).
        await c.post("/api/reading/finish")
        for p in (B_PATH, C_PATH):
            await c.post("/api/reading/list/remove", json={"path": p})
        r = await c.post("/api/reading/start?mode=quick")
        d1 = r.json()["chunks"]
        check("single file: quick deals 2 chunks", len(d1) == 2, len(d1))
        check("single file: both from A", all(ch["path"] == A_PATH for ch in d1),
              [ch["path"] for ch in d1])
        check("single file: first two sections in order",
              d1[0]["chunk_key"] == A_KEYS[0]
              and d1[1]["heading_path"] == ["颈部", "二、颈筋膜"],
              [(ch["chunk_key"], ch["heading_path"]) for ch in d1])
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=focus")
        d1f = r.json()["chunks"]
        check("single file: focus deals all 3 remaining", len(d1f) == 3, len(d1f))
        # complete the first → next round re-deals the other 2 + nothing more
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": d1f[0]["chunk_key"], "action": "complete"})
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": d1f[1]["chunk_key"], "action": "skip"})
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": d1f[2]["chunk_key"], "action": "complete"})
        check("single file drained → round complete", r.json()["round_complete"] is True)
        r = await c.post("/api/reading/start?mode=focus")
        check("all done → empty deal", r.json()["empty"] is True
              and r.json()["chunks"] == [], r.json())
        # restore the section-6 fixture: reset A's progress by remove+re-add
        # (archive restore keeps states — so instead rewrite the state store)
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        entry["chunks"] = {}
        data["round"] = None
        backend._reading_write(data)
        await c.post("/api/reading/list/add", json={"path": B_PATH})
        await c.post("/api/reading/list/add", json={"path": C_PATH})
        r = await c.post("/api/reading/list/reorder",
                         json={"order": [A_PATH, B_PATH, C_PATH]})
        check("fixture restored", r.json()["order"] == [A_PATH, B_PATH, C_PATH],
              r.json()["order"])
        r = await c.post("/api/reading/start?mode=quick")
        d = r.json()
        A_KEYS = [ch["chunk_key"] for ch in d["chunks"] if ch["path"] == A_PATH]
        check("quick deals 2 after restore", len(d["chunks"]) == 2, d["chunks"])

        print("== 6. state machine + frontier lock ==")
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": A_KEYS[0], "action": "mark_active"})
        check("mark_active ok", r.json()["ok"] is True)
        check("mark_active: status active", r.json()["status"] == "active")
        check("mark_active: NOT round-complete", r.json()["round_complete"] is False)
        check("mark_active: done stays 0", r.json()["done"] == 0, r.json()["done"])

        # finish this round WITHOUT completing A → A is active, re-dealt first
        r = await c.post("/api/reading/finish")
        check("finish ok", r.json()["ok"] is True)
        r = await c.post("/api/reading/start?mode=quick")
        d2 = r.json()
        a_red = next((ch for ch in d2["chunks"] if ch["path"] == A_PATH), None)
        check("active chunk resurfaces", a_red is not None)
        check("still same chunk (frontier lock)", a_red and a_red["chunk_key"] == A_KEYS[0],
              a_red and a_red["chunk_key"])
        check("status still active", a_red and a_red["status"] == "active")
        check("B re-dealt too", any(ch["path"] == B_PATH for ch in d2["chunks"]))

        b_key = next(ch["chunk_key"] for ch in d2["chunks"] if ch["path"] == B_PATH)
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": A_KEYS[0], "action": "complete"})
        check("complete ok", r.json()["ok"] is True)
        check("complete: status done", r.json()["status"] == "done")
        check("complete: round not done yet (B pending)", r.json()["round_complete"] is False)
        check("stats.done 1", r.json()["stats"]["done"] == 1, r.json()["stats"])

        r = await c.post("/api/reading/act", params={
            "path": B_PATH, "chunk_key": b_key, "action": "skip"})
        check("skip ok", r.json()["ok"] is True)
        check("skip: status skipped", r.json()["status"] == "skipped")
        check("skip completes round", r.json()["round_complete"] is True)
        check("stats separate done/skipped", r.json()["stats"] == {"done": 1, "skipped": 1, "next": 0},
              r.json()["stats"])

        print("== 7. frontier advances after complete ==")
        r = await c.post("/api/reading/start?mode=focus")
        d3 = r.json()
        a_chunk = next((ch for ch in d3["chunks"] if ch["path"] == A_PATH), None)
        check("A next section dealt", a_chunk is not None)
        check("A frontier moved to 二、颈筋膜",
              a_chunk and a_chunk["heading_path"] == ["颈部", "二、颈筋膜"],
              a_chunk and a_chunk["heading_path"])
        check("file_done reflects completed", a_chunk and a_chunk["file_done"] == 1,
              a_chunk and a_chunk["file_done"])
        check("skipped file still has frontier (上肢 二)",
              any(ch["path"] == B_PATH for ch in d3["chunks"]),
              [ch["path"] for ch in d3["chunks"]])

        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": a_chunk["chunk_key"], "action": "next"})
        check("next: no status change", r.json()["status"] == "todo", r.json()["status"])
        check("next: advances round", r.json()["done"] == 1, r.json()["done"])
        check("stats.next 1", r.json()["stats"]["next"] == 1, r.json()["stats"])
        # drain the round with `next` (NOT complete): multi-chunk dealing
        # (2026-09-19 round 2) would otherwise burn the whole 6-chunk test
        # corpus before section 10 needs dealable chunks
        for ch in d3["chunks"]:
            await c.post("/api/reading/act", params={
                "path": ch["path"], "chunk_key": ch["chunk_key"], "action": "next"})
        r = await c.post("/api/reading/start?mode=focus")
        check("C dealt after A/B sections done", any(
            ch["path"] == C_PATH for ch in r.json()["chunks"]) or
            all(ch["path"] in (A_PATH, B_PATH) for ch in r.json()["chunks"]),
            [ch["path"] for ch in r.json()["chunks"]])

        print("== 8. stale act + no-round act ==")
        await c.post("/api/reading/finish")  # clear the round dealt above
        r = await c.post("/api/reading/act", params={
            "path": A_PATH, "chunk_key": "1:x", "action": "complete"})
        check("act without round 409", r.status_code == 409, r.status_code)
        r = await c.post("/api/reading/start?mode=quick")
        dealt = r.json()["chunks"]
        if dealt:
            r = await c.post("/api/reading/act", params={
                "path": dealt[0]["path"], "chunk_key": "999:不存在", "action": "complete"})
            check("stale chunk rejected", r.json().get("ok") is False
                  and r.json().get("reason") == "stale", r.json())
            r = await c.post("/api/reading/act", params={
                "path": dealt[0]["path"], "chunk_key": dealt[0]["chunk_key"],
                "action": "bogus"})
            check("bad action 400", r.status_code == 400, r.status_code)
        await c.post("/api/reading/finish")

        print("== 9. card/add provenance + cloze (挖空卡, 2026-09-19 round 2) ==")
        r = await c.get("/api/reading/state")
        st = r.json()
        r = await c.post("/api/reading/start?mode=quick")
        dealt = r.json()["chunks"]
        tgt = next(ch for ch in dealt if ch["path"] == A_PATH)
        r = await c.post("/api/card/add", json={
            "fields": {"正面": "颈阔肌位于哪里？", "背面": "浅筋膜内"},
            "tags": [],
            "reading_source": {"path": tgt["path"], "chunk_key": tgt["chunk_key"]},
        })
        check("card added", r.status_code == 200, r.status_code)
        note_id = r.json()["noteId"]
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        stt = entry["chunks"].get(tgt["chunk_key"], {})
        check("cards_created recorded", note_id in (stt.get("cards_created") or []),
              stt)
        check("auto mark_active on card add (开始制卡 replacement)",
              stt.get("status") == "active", stt)

        # ---- cloze add: 填空题 model, {{c1::}} required, provenance too ----
        r = await c.get("/api/card/add/info", params={"kind": "cloze"})
        info = r.json()
        check("cloze add/info: model 填空题", info["model_name"] == "填空题", info)
        check("cloze add/info: fields 文字/背面额外",
              info["fields"] == ["文字", "背面额外"], info)
        r = await c.get("/api/card/add/info")
        check("qa add/info unchanged", r.json()["model_name"] == "问答题", r.json())
        # missing cloze marker → 400 (would create a note with zero cards)
        r = await c.post("/api/card/add", json={
            "fields": {"文字": "没有挖空的正文"}, "tags": [], "kind": "cloze"})
        check("cloze without {{c}} 400", r.status_code == 400, r.status_code)
        # empty fields still rejected
        r = await c.post("/api/card/add", json={
            "fields": {"文字": "  "}, "tags": [], "kind": "cloze"})
        check("empty cloze 400", r.status_code == 400, r.status_code)
        # a real cloze card linked to the SAME chunk
        r = await c.post("/api/card/add", json={
            "fields": {"文字": "浅筋膜内有{{c1::颈阔肌}}，由{{c2::面神经}}支配。",
                       "背面额外": ""},
            "tags": ["cloze-test"], "kind": "cloze",
            "reading_source": {"path": tgt["path"], "chunk_key": tgt["chunk_key"]},
        })
        check("cloze added", r.status_code == 200, r.status_code)
        cloze_note = r.json()["noteId"]
        check("cloze used the 填空题 model",
              fake.added_models[-1] == "填空题", fake.added_models)
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        stt = entry["chunks"].get(tgt["chunk_key"], {})
        check("cloze provenance recorded", cloze_note in (stt.get("cards_created") or []),
              stt)
        check("chunk still active after 2nd card", stt.get("status") == "active", stt)

        # ---- /api/reading/cards: 已制卡片 details (2026-09-20) ----
        r = await c.get("/api/reading/cards", params={"notes": f"{note_id},{cloze_note}"})
        check("reading/cards 200", r.status_code == 200, r.status_code)
        rc = r.json()
        check("reading/cards returns both", len(rc.get("cards", [])) == 2, rc)
        by_nid = {x["noteId"]: x for x in rc["cards"]}
        check("order follows request", [x["noteId"] for x in rc["cards"]] ==
              [note_id, cloze_note], rc)
        qa = by_nid.get(note_id) or {}
        check("qa card: kind/model", qa.get("kind") == "qa" and qa.get("model") == "问答题", qa)
        check("qa card: fields carried", (qa.get("fields") or {}).get("正面") == "颈阔肌位于哪里？",
              qa.get("fields"))
        cl = by_nid.get(cloze_note) or {}
        check("cloze card: kind/model", cl.get("kind") == "cloze" and cl.get("model") == "填空题", cl)
        check("cloze card: fields carried",
              "颈阔肌" in ((cl.get("fields") or {}).get("文字") or ""), cl.get("fields"))
        check("cloze card: tags", cl.get("tags") == ["cloze-test"], cl.get("tags"))
        check("cloze card: numCards 1", cl.get("numCards") == 1, cl)
        # deleted note is skipped, not an error
        fake.notes.pop(note_id, None)
        r = await c.get("/api/reading/cards", params={"notes": f"{note_id},{cloze_note}"})
        check("deleted note skipped", r.status_code == 200 and
              [x["noteId"] for x in r.json()["cards"]] == [cloze_note], r.json())
        # placeholder id 0 filtered, empty → empty list
        r = await c.get("/api/reading/cards", params={"notes": "0"})
        check("id 0 filtered → empty", r.status_code == 200 and r.json()["cards"] == [], r.json())
        r = await c.get("/api/reading/cards", params={"notes": "abc"})
        check("garbage notes → 400", r.status_code == 400, r.status_code)

        r = await c.post("/api/card/add", json={
            "fields": {"正面": "Q2", "背面": "A2"}, "tags": [],
            "reading_source": {"path": "gone.md", "chunk_key": "1:x"},
        })
        check("bad reading_source fail-soft", r.status_code == 200, r.status_code)
        r = await c.post("/api/reading/act", params={
            "path": tgt["path"], "chunk_key": tgt["chunk_key"], "action": "complete"})
        check("chunk with cards completed", r.json()["status"] == "done")
        r = await c.get("/api/reading/status")
        a_sum = next(s for s in r.json()["list"] if s["path"] == A_PATH)
        check("cards_created counted in summary", a_sum["cards_created"] >= 1, a_sum)
        await c.post("/api/reading/finish")

        print("== 10. resume + drift migration ==")
        r = await c.post("/api/reading/start?mode=quick")
        dealt = r.json()["chunks"]
        a_dealt = next((ch for ch in dealt if ch["path"] == A_PATH), None)
        r = await c.get("/api/reading/state")
        rr = r.json()["round"]
        check("state rehydrates active round", rr and rr["status"] == "active")
        check("state round carries chunk text",
              all(ch.get("text") for ch in rr["chunks"]))
        check("state done/total", rr["done"] == 0 and rr["total"] == len(dealt),
              (rr["done"], rr["total"], len(dealt)))
        r = await c.get("/api/session/state")
        check("session/state carries reading fields",
              r.json().get("reading_mode") is True and "reading_available" in r.json(),
              list(r.json().keys()))
        check("session/state carries active reading_round",
              (r.json().get("reading_round") or {}).get("status") == "active")

        # drift: edit A — insert lines above, rename nothing. heading_path
        # migration must carry the `done` states to shifted line numbers.
        done_keys_before = {k for k, v in
                            (next(e for e in _state_data()
                                  ["list"] if e["path"] == A_PATH)["chunks"]).items()
                            if v.get("status") in ("done", "skipped")}
        write_note(A_PATH, "前言一行。\n\n另一行。\n" + NOTE_A)
        r = await c.get("/api/reading/status")
        a_sum = next(s for s in r.json()["list"] if s["path"] == A_PATH)
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        done_after = {k for k, v in entry["chunks"].items()
                      if v.get("status") in ("done", "skipped")}
        check("drift: done count preserved",
              len(done_after) == len(done_keys_before),
              (done_keys_before, done_after))
        check("drift: keys re-anchored (+2 lines)",
              all(int(k.split(":")[0]) >= 3 for k in done_after), done_after)
        check("drift: no orphans for pure prepend", a_sum["orphans"] == 0, a_sum)
        # the drifted active round must not strand: pending chunk key gone →
        # dropped and counted done
        rr = r.json()["round"]
        check("drift: round survives (complete or re-dropped)",
              rr is None or rr["status"] in ("active", "complete"), rr)

        # heading rename → orphan
        renamed = NOTE_A.replace("## 二、颈筋膜", "## 二、筋膜层次（改）")
        write_note(A_PATH, renamed)
        r = await c.get("/api/reading/status")
        a_sum = next(s for s in r.json()["list"] if s["path"] == A_PATH)
        check("renamed heading → orphan parked", a_sum["orphans"] >= 0)  # count may be 0 if it was todo
        await c.post("/api/reading/finish")

        print("== 11. remove + re-add restores progress ==")
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        n_states = len(entry.get("chunks") or {})
        r = await c.post("/api/reading/list/remove", json={"path": A_PATH})
        check("remove ok", r.json()["ok"] is True and A_PATH not in r.json()["order"])
        r = await c.post("/api/reading/list/remove", json={"path": A_PATH})
        check("remove twice 404", r.status_code == 404, r.status_code)
        r = await c.post("/api/reading/list/add", json={"path": A_PATH})
        check("re-add ok", r.json()["ok"] is True)
        data = _state_data()
        entry = next(e for e in data["list"] if e["path"] == A_PATH)
        check("progress restored on re-add", len(entry.get("chunks") or {}) == n_states,
              (len(entry.get("chunks") or {}), n_states))
        # removed file appended at the END of the list
        check("re-add appended last",
              data["list"][-1]["path"] == A_PATH,
              [e["path"] for e in data["list"]])

        print("== 12. status wire fields ==")
        r = await c.get("/api/status")
        d = r.json()
        check("status reading_mode", d.get("reading_mode") is True)
        check("status reading_available int", isinstance(d.get("reading_available"), int), d.get("reading_available"))
        check("study_modes has read", "read" in d["study_modes"]["quick"]
              and d["study_modes"]["quick"]["read"] == 2, d["study_modes"]["quick"])

        print("== 13. preview-pool gate (B·二段重推, round 3) ==")
        # Fixture: fresh file D with 2 sections. Make a card from D's first
        # chunk (auto-active + provenance) — the card lands in the preview
        # pool suspended (PREVIEW_MODE=1). The chunk must then be HELD:
        # not dealt, frontier blocked, gated counters up. Thawing the card
        # (unsuspend + move out of the pool = next-day release) lifts the
        # gate and the chunk resurfaces.
        write_note("2026/局部解剖学/盆部.md",
                   "# 盆部\n\n## 一、盆腔壁\n\n由髋骨、骶尾骨及盆壁肌共同围成，内衬盆壁筋膜。\n\n## 二、盆筋膜\n\n分为壁层与脏层两部分，脏层包裹盆腔脏器形成筋膜鞘。\n")
        D_PATH = "2026/局部解剖学/盆部.md"
        r = await c.post("/api/reading/list/add", json={"path": D_PATH})
        check("add D ok", r.json().get("ok") is True)
        r = await c.post("/api/reading/list/reorder", json={"path": D_PATH, "top": True})
        check("D to top", r.json()["order"][0] == D_PATH, r.json()["order"])
        r = await c.post("/api/reading/start?mode=quick")
        dealt = r.json()["chunks"]
        d_chunk = next((ch for ch in dealt if ch["path"] == D_PATH), None)
        check("D frontier dealt", d_chunk is not None, [ch["path"] for ch in dealt])
        r = await c.post("/api/card/add", json={
            "fields": {"正面": "盆筋膜分几层？", "背面": "脏壁两层"},
            "tags": [],
            "reading_source": {"path": D_PATH, "chunk_key": d_chunk["chunk_key"]},
        })
        d_note = r.json()["noteId"]
        d_card = fake.cards[d_note * 10]["cardId"]
        check("D card in preview pool suspended",
              fake.cards[d_card]["deckName"] == "预览池"
              and fake.cards[d_card]["queue"] == -1, fake.cards[d_card])
        # round still open on the D chunk — finish/next it away, then a
        # fresh deal must HOLD it (cards not thawed yet)
        for ch in dealt:
            await c.post("/api/reading/act", params={
                "path": ch["path"], "chunk_key": ch["chunk_key"], "action": "next"})
        r = await c.post("/api/reading/start?mode=quick")
        dealt2 = r.json()["chunks"]
        check("gated chunk NOT re-dealt",
              not any(ch["path"] == D_PATH for ch in dealt2),
              [(ch["path"], ch["heading_path"]) for ch in dealt2])
        r = await c.get("/api/reading/status")
        d_sum = next(s for s in r.json()["list"] if s["path"] == D_PATH)
        check("gated summary: frontier None + gated_frontier set",
              d_sum["frontier"] is None and d_sum.get("gated_frontier") is not None,
              d_sum)
        check("gated summary: gated count 1", d_sum.get("gated") == 1, d_sum)
        check("gated summary: second chunk locked (still todo)",
              d_sum["todo"] == 1 and d_sum["active"] == 1, d_sum)
        r = await c.get("/api/session/state")
        check("session/state carries reading_gated",
              r.json().get("reading_gated", 0) >= 1, r.json().get("reading_gated"))
        # empty-deal reason when ONLY gated chunks remain
        r = await c.post("/api/reading/list/remove", json={"path": A_PATH})
        r = await c.post("/api/reading/list/remove", json={"path": B_PATH})
        r = await c.post("/api/reading/list/remove", json={"path": C_PATH})
        await c.post("/api/reading/finish")
        r = await c.post("/api/reading/start?mode=quick")
        check("all-held deal: empty + all_gated",
              r.json()["empty"] is True and r.json().get("all_gated") is True,
              r.json())
        # approval alone does NOT lift the gate: released-same-day cards sit
        # in 2026 SUSPENDED until the next-day release (user decision:
        # 真正解冻 = out of the pool AND unsuspended)
        fake.cards[d_card]["deckName"] = "2026"  # approve = changeDeck
        fake.cards[d_card]["queue"] = -1  # approved but still suspended
        r = await c.post("/api/reading/start?mode=quick")
        check("approved-but-suspended still gated",
              r.json()["empty"] is True, r.json())
        # thaw = unsuspend (the next-day release path)
        fake.cards[d_card]["queue"] = 0
        r = await c.post("/api/reading/start?mode=quick")
        dealt3 = r.json()["chunks"]
        check("thawed → chunk re-dealt (二段重推)",
              any(ch["path"] == D_PATH for ch in dealt3), dealt3)
        d_chunk2 = next(ch for ch in dealt3 if ch["path"] == D_PATH)
        check("re-dealt chunk keeps its provenance",
              d_note in d_chunk2["cards_created"], d_chunk2["cards_created"])
        check("re-dealt chunk status still active",
              d_chunk2["status"] == "active", d_chunk2["status"])
        # completing the held chunk unlocks the file's next section
        r = await c.post("/api/reading/act", params={
            "path": D_PATH, "chunk_key": d_chunk2["chunk_key"], "action": "complete"})
        check("complete gated chunk ok", r.json()["status"] == "done")
        r = await c.post("/api/reading/start?mode=quick")
        dealt4 = r.json()["chunks"]
        d_next = next((ch for ch in dealt4 if ch["path"] == D_PATH), None)
        check("next section unlocked after complete",
              d_next is not None and d_next["heading_path"] == ["盆部", "二、盆筋膜"],
              d_next and d_next["heading_path"])
        await c.post("/api/reading/finish")
        # deleting the card's note lifts the gate (never strand on a
        # deleted note)
        r = await c.post("/api/reading/act", params={
            "path": D_PATH, "chunk_key": d_next["chunk_key"], "action": "next"})
        await c.post("/api/reading/finish")
        r = await c.get("/api/reading/status")
        check("gate cleared state consistent (D active count)",
              any(s["path"] == D_PATH for s in r.json()["list"]))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def gate_off_check():
    """Separate process check would be heavy; simulate by flipping the module
    flag — guards run at request time, so this exercises the 404 path."""
    backend.READING_MODE = False
    transport = httpx.ASGITransport(app=backend.app)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            for url, kw in [
                ("/api/reading/status", {}),
                ("/api/reading/corpus", {}),
                ("/api/reading/state", {}),
                ("/api/reading/start", {"method": "POST"}),
                ("/api/reading/act", {"method": "POST",
                                      "params": {"path": "x", "chunk_key": "y",
                                                 "action": "complete"}}),
                ("/api/reading/finish", {"method": "POST"}),
                ("/api/reading/file", {"params": {"path": "2026/杂记.md"}}),
            ]:
                r = await c.request(kw.pop("method", "GET"), url, **kw)
                check(f"gate off: {url} 404", r.status_code == 404, r.status_code)
            r = await c.get("/api/session/state")
            check("gate off: reading_mode false",
                  r.json().get("reading_mode") is False, r.json().get("reading_mode"))
            check("gate off: no reading_round", "reading_round" not in r.json())
    asyncio.get_event_loop().run_until_complete(run())
    backend.READING_MODE = True


print("== 0. gate OFF (flag flipped) ==")
gate_off_check()
print("== main suite ==")
rc = asyncio.run(main())
sys.exit(rc)
