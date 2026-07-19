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
            pattern = os.path.normpath(fullest["path"]) + "%"
            rows = cx.execute(
                """SELECT id, filename, file_size
                   FROM history
                   WHERE status='done'
                     AND filename LIKE ?
                     AND file_size > 0
                   ORDER BY ts DESC LIMIT ?""",
                (pattern, int(max_moves * 2))).fetchall()
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
            pattern = os.path.normpath(paths[0]["path"]) + "%"
            rows = cx.execute(
                """SELECT id, filename, file_size, ts
                   FROM history
                   WHERE status='done'
                     AND filename LIKE ?
                     AND ts < datetime('now', ?)
                     AND file_size > 0
                   ORDER BY ts ASC LIMIT ?""",
                (pattern, f"-{cutoff_days} days", int(max_moves))).fetchall()
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
    moves = plan.get("moves") or []
    for m in moves:
        src = m.get("from")
        dst = m.get("to")
        hid = m.get("history_id")
        if not src or not dst or not os.path.isfile(src):
            out["skipped"] += 1
            continue
        if dry_run:
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
                cx.execute("UPDATE history SET filename = ? WHERE id = ?",
                           (dst, hid))
            out["moved"] += 1
            out["total_bytes"] += int(m.get("size_bytes", 0))
        except Exception as e:
            out["errors"] += 1
            sys.stderr.write(f"[rebalance] move {src} → {dst}: {e}\n")
    out["total_gb"] = round(out["total_bytes"] / (1024**3), 2)
    return out
