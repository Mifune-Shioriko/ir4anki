#!/usr/bin/env python3
"""Real-corpus reading smoke test (READ-ONLY w.r.t. the Anki collection).

Adds a REAL file from ~/anki-notes to the isolated-state reading list on the
throwaway :8902 backend, deals a round, walks the state machine (mark_active
→ complete), verifies frontier locking + advancement against the ACTUAL
chunker output, then cleans up the reading list. Never touches the live
app/state/collection: reading.json lives in the throwaway state dir and no
card is created. Provenance (cards_created) is covered by reading_test.py
against the fake AnkiConnect.
"""
import json, sys, urllib.request, urllib.error, urllib.parse
BASE = "http://127.0.0.1:8902"
PASS = FAIL = 0
def check(c, m, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  ok  {m}")
    else: FAIL += 1; print(f"  FAIL {m} {d}")
def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE+path, method=method, data=data,
                               headers={"Content-Type":"application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp: return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "detail": e.read().decode()[:200]}

target = "2026/局部解剖学/颈部.md"
corpus = req("/api/reading/corpus")
check(any(f["path"] == target for f in corpus["files"]), "real corpus has 颈部.md",
      [f["path"] for f in corpus["files"]][:5])

req("/api/reading/list/remove", "POST", {"path": target})  # clear leftovers
r = req("/api/reading/list/add", "POST", {"path": target})
check(r.get("ok") is True, "added real file to reading list", r)

st = req("/api/reading/status")
row = next(s for s in st["list"] if s["path"] == target)
check(row["total_chunks"] >= 2, f"颈部 chunked into {row['total_chunks']} pieces", row["total_chunks"])
check(row["frontier"] is not None, "frontier present", row["frontier"])
check(row["orphans"] == 0, "no drift orphans on fresh add", row["orphans"])
fr = row["frontier"]["chunk_key"]
first_title = row["frontier"]["title"]
print(f"  (frontier: {first_title!r} @ {fr})")

d = req("/api/reading/start?mode=focus", "POST")
check(len(d["chunks"]) == 1, "one frontier chunk dealt (single file)", len(d["chunks"]))
ch = d["chunks"][0]
check(ch["chunk_key"] == fr, "dealt chunk == frontier", (ch["chunk_key"], fr))
check(len(ch["text"]) >= 10, "chunk text non-trivial", ch["text"][:40])

def act(action, key=fr):
    q = urllib.parse.urlencode({"path": target, "chunk_key": key, "action": action})
    return req(f"/api/reading/act?{q}", "POST")

r = act("mark_active")
check(r.get("status") == "active", "mark_active → active", r)
check(r.get("round_complete") is False, "mark_active keeps round open", r)

# the active frontier must resurface on a fresh round (priority重现)
req("/api/reading/finish", "POST")
d2 = req("/api/reading/start?mode=focus", "POST")
check(len(d2["chunks"]) == 1 and d2["chunks"][0]["chunk_key"] == fr,
      "active chunk resurfaces first", [c["chunk_key"] for c in d2["chunks"]])
check(d2["chunks"][0]["status"] == "active", "still active on resurface",
      d2["chunks"][0]["status"])

r = act("complete")
check(r.get("status") == "done", "complete → done", r)
st = req("/api/reading/status")
row = next(s for s in st["list"] if s["path"] == target)
check(row["done"] == 1, "one chunk done", row["done"])
check(row["frontier"] and row["frontier"]["chunk_key"] != fr,
      "frontier advanced past completed chunk", row["frontier"])
print(f"  (new frontier: {row['frontier']['title']!r})")

req("/api/reading/finish", "POST")
req("/api/reading/list/remove", "POST", {"path": target})
st = req("/api/reading/status")
check(all(s["path"] != target for s in st["list"]), "cleanup: removed from list")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
