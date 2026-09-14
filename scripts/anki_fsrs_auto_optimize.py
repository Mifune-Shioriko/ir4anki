#!/usr/bin/env python3
"""FSRS auto-optimizer (no_agent cron — stdout delivered verbatim to QQ).

User spec 2026-09-14: 复习量积累到一定程度后自动更新 FSRS 参数，然后 QQ 通知。

Runs daily (04:30, right after the Anki day rollover while the user sleeps):
  1. READ-ONLY sqlite query on the live collection: count real graded
     reviews (revlog type IN (0,1,2), ease 1-4) since the last optimization.
     IMPORTANT: type=4 (rescheduled) and type=5 (fsrs_reschedule rewrites)
     are EXCLUDED — a single re-opt reschedule writes hundreds of type-5
     rows that would otherwise instantly re-trigger the threshold
     (verified 2026-09-14: getNumCardsReviewedToday reported 929 vs 170
     real reviews on a reschedule day).
  2. Threshold not reached (default 1000, env FSRS_AUTOOPT_THRESHOLD):
     exit SILENTLY (empty stdout ⇒ no QQ message — watchdog pattern).
  3. Threshold reached: run ~/anki-server/fsrs-reopt-20260914/run_reopt.sh
     (stop anki-headless → backup collection → native COMPUTE_ALL_PARAMS
     with fsrs_reschedule=True → start → AnkiConnect sync → verify),
     update the state file, print a QQ summary of what changed.
  4. Failure: print a short ⚠️ message (the user should know it skipped).

State: ~/.local/share/anki-fsrs-auto/state.json
  {"last_optimize_ms": <epoch ms>, "last_optimize_reviews": N, "history": [...]}
Seeded 2026-09-14 with the manual re-opt timestamp (09:52 CST) so counting
starts from there.

Concurrency: fcntl flock on ~/.local/share/anki-fsrs-auto/optimize.lock —
a manual invocation and a cron tick can't overlap (the re-opt stops the
container; two at once would race on the sqlite files).

Manual check without side effects:  python3 anki_fsrs_auto_optimize.py --check
"""
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

COL = os.path.expanduser(
    os.environ.get(
        "ANKI_COL_PATH",
        "~/anki-server/headless/data/shioriko/collection.anki2",
    )
)
STATE_DIR = Path(os.path.expanduser("~/.local/share/anki-fsrs-auto"))
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "optimize.lock"
REOPT_SH = os.path.expanduser("~/anki-server/fsrs-reopt-20260914/run_reopt.sh")
AUDIT_DIR = Path(os.path.expanduser("~/anki-server/fsrs-reopt-20260914"))
THRESHOLD = int(os.environ.get("FSRS_AUTOOPT_THRESHOLD", "1000"))

# Manual re-opt on 2026-09-14 09:52 CST is the counting baseline
DEFAULT_LAST_MS = int(
    datetime(2026, 9, 14, 9, 52).timestamp() * 1000
)


def log(*a):
    print(*a)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {
            "last_optimize_ms": DEFAULT_LAST_MS,
            "last_optimize_reviews": 0,
            "history": [],
        }


def save_state(st: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def count_reviews_since(ms: int) -> int:
    """Real graded reviews (type 0/1/2, ease 1-4) with revlog id > ms.

    revlog.id is epoch ms. Read-only URI so this never fights the live
    container for a write lock (WAL mode allows concurrent readers)."""
    con = sqlite3.connect(f"file:{COL}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM revlog "
            "WHERE id > ? AND type IN (0,1,2) AND ease BETWEEN 1 AND 4",
            (ms,),
        ).fetchone()[0]
        return int(n)
    finally:
        con.close()


def latest_audit_path() -> Path | None:
    files = sorted(AUDIT_DIR.glob("applied-*.json"))
    return files[-1] if files else None


def latest_audit() -> dict | None:
    """Newest applied-*.json written by apply_reopt.py (the re-opt audit)."""
    f = latest_audit_path()
    if f is None:
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def fmt_weights_diff(before: list, after: list, k: int = 3) -> str:
    """Show the k largest relative weight changes, compactly."""
    if not before or len(before) != len(after):
        return ""
    diffs = []
    for i, (b, a) in enumerate(zip(before, after)):
        if abs(b) < 1e-9:
            continue
        diffs.append((abs(a - b) / abs(b), i, b, a))
    diffs.sort(reverse=True)
    parts = [f"w{i}: {b:.3f}→{a:.3f}" for _, i, b, a in diffs[:k]]
    return ", ".join(parts)


def do_check_only() -> int:
    st = load_state()
    n = count_reviews_since(st["last_optimize_ms"])
    since = datetime.fromtimestamp(st["last_optimize_ms"] / 1000)
    log(f"[check] 上次优化: {since:%Y-%m-%d %H:%M}")
    log(f"[check] 之后真实复习数: {n} / 阈值 {THRESHOLD}")
    log(f"[check] {'达到阈值,下次 cron 会执行优化' if n >= THRESHOLD else '未达阈值,保持静默'}")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return do_check_only()

    st = load_state()
    try:
        n = count_reviews_since(st["last_optimize_ms"])
    except Exception as e:
        log(f"⚠️ FSRS 自动优化跳过：读复习记录失败（{e}）。刷卡不受影响。")
        return 0

    if n < THRESHOLD:
        return 0  # silent — no QQ message

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another optimize in flight; next tick re-counts

    run_start_ms = int(time.time() * 1000)
    days = (run_start_ms - st["last_optimize_ms"]) / 86400000
    before_audit_path = latest_audit_path()

    try:
        proc = subprocess.run(
            ["bash", REOPT_SH],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        log("⚠️ FSRS 自动优化超时（15 分钟），已中止。请检查 anki-headless 容器状态。")
        return 0
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        log("⚠️ FSRS 自动优化失败，本次跳过（刷卡不受影响，集合已从备份自动恢复）。")
        for line in tail:
            log(f"  {line[:160]}")
        return 0

    # A NEW applied-*.json must have appeared — that's the proof the re-opt
    # actually ran and wrote its audit (path identity, NOT dict `is`).
    after_audit_path = latest_audit_path()
    audit_changed = after_audit_path is not None and after_audit_path != before_audit_path
    after_audit = latest_audit() if audit_changed else None
    # state advances even if audit parsing fails — the re-opt ran
    st["last_optimize_ms"] = run_start_ms
    st["last_optimize_reviews"] = n
    hist_entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reviews_since_last": n,
        "days_since_last": round(days, 1),
    }
    if after_audit:
        hist_entry["due_before"] = after_audit.get("due_before_reschedule")
        hist_entry["due_after"] = after_audit.get("due_after_reschedule")
    st.setdefault("history", []).append(hist_entry)
    st["history"] = st["history"][-20:]
    save_state(st)

    # ---- QQ summary ----
    log("🔧 FSRS 参数已自动更新")
    log(f"· 距上次优化 {days:.1f} 天，期间完成 {n} 次真实复习（阈值 {THRESHOLD}）")
    if after_audit:
        dr = after_audit.get("desired_retention")
        if dr:
            log(f"· 期望留存 {dr:.2f}（保持不变）")
        db, da = (
            after_audit.get("due_before_reschedule"),
            after_audit.get("due_after_reschedule"),
        )
        if db is not None and da is not None:
            pct = ((da - db) / db * 100) if db else 0
            log(f"· 按新参数重排到期：{db} → {da} 张（{pct:+.0f}%）")
        wd = fmt_weights_diff(
            after_audit.get("weights_before") or [],
            after_audit.get("weights_after") or [],
        )
        if wd:
            log(f"· 权重变化最大项：{wd}")
    else:
        log("· （未找到本次优化审计文件，权重详情略；re-opt 已执行）")
    log("· 已同步到手机/电脑；不满意可回滚（备份在 ~/anki-server/fsrs-reopt-20260914/）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
