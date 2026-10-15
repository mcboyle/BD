"""Storage rebalancer (Phase 124, Block Q).

Multi-disk operators want BD to spread files across mount points
according to policy (e.g. hot files on SSD, cold on spinning rust;
or round-robin to fill all disks evenly). This module:

  • inventory(paths) — scan a list of storage paths, return capacity
    + free + current usage per path
  • plan_rebalance(paths, strategy) — return a list of suggested
    moves to satisfy the strategy without executing
  • execute_plan(plan, dry_run) — perform the moves, update history.filename

Strategies:
  • 'even_fill'   — equalize free-bytes across all paths
  • 'tier_by_age' — newest files on path[0] ('hot'), oldest on path[-1]
                     ('cold'). Useful for SSD→HDD tiering.
  • 'fill_first'  — keep filling paths in order; only move when full

Never reads cross-disk by default — execute_plan uses os.rename when
src/dst are on the same device, falls back to shutil.move when not.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional


def inventory(paths: list) -> list:
    """Snapshot capacity + current usage per path.

    Returns list of {path, total_gb, free_gb, used_gb, mount_id,
                     file_count, total_known_bytes}."""
    out = []
    for p in paths:
        if not p or not os.path.isdir(p):
            out.append({"path": p, "error": "missing"})
            continue
        try:
            usage = shutil.disk_usage(p)
            mount_id = os.stat(p).st_dev
        except OSError as e:
            out.append({"path": p, "error": str(e)})
            continue
        # Count known files (history rows that point at this path)
        file_count = 0
        known_bytes = 0
        try:
            from . import db as _db
            with _db.db_conn() as cx:
                # Match by path prefix — files under this dir
                pattern = os.path.normpath(p) + "%"
                row = cx.execute(
                    """SELECT COUNT(*), COALESCE(SUM(file_size), 0)
                       FROM history
                       WHERE status='done'
                         AND filename LIKE ?""",
                    (pattern,)).fetchone()
                file_count = int(row[0] or 0)
                known_bytes = int(row[1] or 0)
        except Exception:
            pass
        out.append({
            "path": p,
            "total_gb": round(usage.total / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_gb": round((usage.total - usage.free) / (1024**3), 1),
            "free_pct": round(100 * usage.free / max(1, usage.total), 1),
            "mount_id": mount_id,
            "file_count": file_count,
            "known_bytes": known_bytes,
        })
    return out


def plan_rebalance(paths: list, strategy: str = "even_fill",
                  *, max_moves: int = 100) -> dict:
    """Compute a rebalance plan. Returns {strategy, moves: [{from, to,
    filename, size_bytes, reason}], summary}."""
    inv = inventory(paths)
    available = [i for i in inv if "error" not in i]
    if len(available) < 2:
        return {"strategy": strategy, "moves": [],
                "warning": "need at least 2 healthy paths to rebalance"}

    moves = []
    if strategy == "even_fill":
        moves = _plan_even_fill(available, max_moves)
    elif strategy == "tier_by_age":
        moves = _plan_tier_by_age(available, max_moves)
    elif strategy == "fill_first":
        moves = _plan_fill_first(available, max_moves)
    else:
        return {"strategy": strategy, "moves": [],
                "error": f"unknown strategy: {strategy}"}
    # Planners target join(root, basename(src)): two same-named files from
    # different folders land on ONE path, and executing that plan renames the
    # second over the first. A collision is a plan error, never a silent pick.
    targets: dict = {}
    for m in moves:
        t = os.path.normpath(m["to"])
        if t in targets:
            return {"strategy": strategy, "moves": [],
                    "error": f"target collision: {targets[t]} and "
                             f"{m['from']} both map to {m['to']}"}
        targets[t] = m["from"]
    total_gb = sum(m["size_bytes"] for m in moves) / (1024**3)
    return {
        "strategy": strategy,
        "moves": moves,
        "summary": {
            "move_count": len(moves),
            "total_gb_to_move": round(total_gb, 1),
            "inventory": available,
        },
    }


def _root_prefix(root: str) -> str:
    """`root` + separator: the exact prefix of a file UNDER root. A bare
    `LIKE root%` also selects sibling roots (lib1 -> lib10/...) and reads any
    `_`/`%` in the root as a wildcard; the planners compare this prefix with
    substr() instead."""
    return os.path.join(os.path.normpath(root), "")


def _plan_even_fill(paths: list, max_moves: int) -> list:
    """Find files on the most-full disk and move to the least-full
    until free-bytes are within 10%."""
    paths_sorted = sorted(paths, key=lambda x: x["free_gb"])
    fullest = paths_sorted[0]
    emptiest = paths_sorted[-1]
    target_diff = abs(fullest["free_gb"] - emptiest["free_gb"]) / 2
    if target_diff < 5:
        return []  # already balanced
    moves = []
    bytes_to_move = int(target_diff * 1024**3)
    moved = 0
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            prefix = _root_prefix(fullest["path"])
            rows = cx.execute(
                """SELECT id, filename, file_size
                   FROM history
                   WHERE status='done'
                     AND substr(filename, 1, length(?)) = ?
                     AND file_size > 0
                   ORDER BY ts DESC LIMIT ?""",
                (prefix, prefix, int(max_moves * 2))).fetchall()
    except Exception:
        return moves
    for r in rows:
        if moved >= bytes_to_move or len(moves) >= max_moves:
            break
        d = dict(r)
        fn = d.get("filename") or ""
        if not fn or not os.path.isfile(fn):
            continue
        moves.append({
            "from": fn,
            "to": os.path.join(emptiest["path"], os.path.basename(fn)),
            "history_id": d["id"],
            "size_bytes": int(d["file_size"]),
            "reason": "balance fullest to emptiest",
        })
        moved += int(d["file_size"])
    return moves


def _plan_tier_by_age(paths: list, max_moves: int) -> list:
    """Newest files on paths[0], oldest on paths[-1]. Move misplaced
    files toward their tier."""
    if len(paths) < 2:
        return []
    moves = []
    # Threshold: split history by age into len(paths) tiers
    cutoff_days = 30  # everything >30d is "cold", <30d is "hot"
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Cold tier candidates currently on hot path
            prefix = _root_prefix(paths[0]["path"])
            rows = cx.execute(
                """SELECT id, filename, file_size, ts
                   FROM history
                   WHERE status='done'
                     AND substr(filename, 1, length(?)) = ?
                     AND ts < datetime('now', ?)
                     AND file_size > 0
                   ORDER BY ts ASC LIMIT ?""",
                (prefix, prefix, f"-{cutoff_days} days",
                 int(max_moves))).fetchall()
    except Exception:
        return moves
    for r in rows:
        d = dict(r)
        fn = d.get("filename") or ""
        if not fn or not os.path.isfile(fn):
            continue
        moves.append({
            "from": fn,
            "to": os.path.join(paths[-1]["path"], os.path.basename(fn)),
            "history_id": d["id"],
            "size_bytes": int(d["file_size"]),
            "reason": f"cold tier (>{cutoff_days}d old)",
        })
    return moves


def _plan_fill_first(paths: list, max_moves: int) -> list:
    """No moves needed unless paths[0] is full. If so, plan to
    redirect new writes to paths[1] — but that's a future-write
    policy, not a move. Returns empty for now."""
    return []


def execute_plan(plan: dict, *, dry_run: bool = True) -> dict:
    """Carry out a rebalance plan. Updates history.filename to the
    new path on success."""
    out = {"ok": True, "moved": 0, "skipped": 0, "errors": 0,
           "dry_run": dry_run, "total_bytes": 0}
    moves = (plan.get("moves") or []) if isinstance(plan, dict) else None
    if not isinstance(moves, list):
        return {**out, "ok": False, "errors": 1, "total_gb": 0.0,
                "error": "plan.moves must be a list", "refused": []}
    # Every caller hands this a plan a client may have written, so the sink
    # checks it: each move's source AND destination must lie inside a
    # configured download root, or NOTHING moves -- checked for the whole plan
    # before the first rename, so one bad entry cannot ride in after good ones.
    from .paths import within_download_roots
    refused = [m for m in moves
               if not isinstance(m, dict)
               or not within_download_roots(m.get("from"))
               or not within_download_roots(m.get("to"))]
    if refused:
        return {**out, "ok": False, "errors": len(refused), "total_gb": 0.0,
                "error": "plan names a path outside the download roots",
                "refused": refused[:20]}
    for m in moves:
        src = m.get("from")
        dst = m.get("to")
        if not src or not dst or not os.path.isfile(src):
            out["skipped"] += 1
            continue
        if dry_run:
            continue
        # os.rename and shutil.move both silently REPLACE an existing file
        # (and shutil.move into an existing dir moves inside it). Checked per
        # move, right before the move: two moves to one new path only collide
        # once the first has run.
        if os.path.lexists(dst):
            out["errors"] += 1
            sys.stderr.write(
                f"[rebalance] move {src} → {dst}: destination exists; "
                f"refusing to overwrite\n")
            continue
        try:
            # Ensure target dir exists
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # Same-disk rename is atomic; cross-disk needs copy+delete
            try:
                os.rename(src, dst)
            except OSError:
                shutil.move(src, dst)
            from . import db as _db
            with _db.db_conn() as cx:
                # The rows that name THIS file, by exact path. The plan's
                # history_id is not trusted: a wrong id rewrote an unrelated
                # row, and a null one raised after the file had moved.
                ids = [r[0] for r in cx.execute(
                    "SELECT id FROM history WHERE filename = ?",
                    (src,)).fetchall()]
                # filename is FTS-INDEXED; snapshot before the write.
                _old = _db.db_fts_snapshot(cx, ids)
                cx.executemany("UPDATE history SET filename = ? WHERE id = ?",
                               [(dst, i) for i in ids])
                _db.db_fts_resync(cx, _old)
            out["moved"] += 1
            out["total_bytes"] += int(m.get("size_bytes", 0))
        except Exception as e:
            out["errors"] += 1
            sys.stderr.write(f"[rebalance] move {src} → {dst}: {e}\n")
    out["total_gb"] = round(out["total_bytes"] / (1024**3), 2)
    return out
