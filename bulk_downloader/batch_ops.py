"""Batch operations — bulk retry / delete / re-enrich / move
(Phase 127, Block Q).

When BD has 20k+ history rows, single-item operations don't scale.
This module exposes bulk endpoints that operate on filtered sets:

  • bulk_retry(filter)   — re-queue all matching failed/needs_review rows
  • bulk_delete(filter)  — delete history rows + optionally the files
  • bulk_move(filter, target_dir) — move files matching filter
  • bulk_reenrich(filter) — re-run TPDB + subtitle + quality on a set
  • bulk_dedup_scan()    — find duplicate files by size + hash

Filter shape (consistent across operations):
  {
    site_id: str | None,
    status: str | None,
    older_than_days: int | None,
    message_contains: str | None,
    id_in: [int] | None,    # explicit row IDs
    limit: int (default 500, max 10000)
  }

All operations return:
  {ok, candidates_matched, processed, errors, dry_run}

Default dry_run=True so the operator can preview before committing.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional


# v3.66.524 (VR-P10): the ONLY table the bulk ops legitimately operate on is
# `history` (every filter references history columns). `table` is reachable from
# the API (POST /api/batch/{retry,delete,move} -> filter.table) and was
# f-string-interpolated straight into the SQL, allowing a bounded SQL identifier
# injection. Allowlist the identifier so a non-`history` value can never reach SQL.
_ALLOWED_TABLES = frozenset({"history"})


def _build_query(*, table: str = "history",
                site_id: Optional[str] = None,
                status: Optional[str] = None,
                older_than_days: Optional[int] = None,
                message_contains: Optional[str] = None,
                id_in: Optional[list] = None,
                limit: int = 500) -> tuple:
    """Compose WHERE clause + params from filter dict. Returns
    (sql, params)."""
    where = []
    params: list = []
    if id_in is not None:
        # An explicitly supplied selection is authoritative INCLUDING when it is
        # empty. This was `if id_in:`, which made a supplied-but-empty list
        # indistinguishable from an absent one: no clause was appended, `where`
        # stayed empty, and the query degenerated to an unfiltered
        # `SELECT * FROM history`. Measured over HTTP:
        #   POST /api/batch/delete {"filter":{"id_in":[]},"dry_run":false}
        #     -> processed 7, history 7 -> 0, including organic rows.
        # "Select nothing" and "select everything" are the two most different
        # answers available, and the falsy test picked the destructive one.
        # _matching_rows is shared, so the same empty list drove bulk_delete
        # (DELETE, and os.unlink under delete_files), bulk_retry (re-queue every
        # row) and bulk_move (relocate every file) alike — which is why the fix
        # belongs here rather than at any one call site.
        # An ABSENT id_in still means "unrestricted by id", so whole-table
        # filters like older_than_days keep working.
        # SQLite IN-list — cap explicit ID lists at 1000 to avoid
        # SQLITE_MAX_VARIABLE_NUMBER issues
        ids = [int(i) for i in id_in[:1000]]
        if ids:
            placeholders = ",".join("?" * len(ids))
            where.append(f"id IN ({placeholders})")
            params.extend(ids)
        else:
            where.append("1 = 0")
    if site_id:
        where.append("site_id = ?")
        params.append(site_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if older_than_days is not None:
        where.append("ts < datetime('now', ?)")
        params.append(f"-{int(older_than_days)} days")
    if message_contains:
        where.append("message LIKE ?")
        params.append(f"%{message_contains}%")
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"refusing non-allowlisted table identifier: {table!r}")
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(int(limit), 10000))
    return sql, params


def _matching_rows(filter_dict: dict) -> list:
    """Resolve filter to a list of history row dicts."""
    try:
        # v3.66.524 (VR-P10): _build_query now raises on a non-allowlisted table.
        # Build inside the try so a malicious/malformed filter resolves to zero
        # rows (fail-safe) rather than surfacing as a 500.
        sql, params = _build_query(**filter_dict)
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def bulk_retry(filter_dict: dict, *, dry_run: bool = True,
              reset_to_status: str = "pending") -> dict:
    """Reset matching rows to `reset_to_status` so workers pick them
    up again. Default 'pending' re-queues; 'needs_review' to mark for
    manual handling."""
    rows = _matching_rows(filter_dict or {})
    out = {"ok": True, "candidates_matched": len(rows),
           "processed": 0, "errors": 0, "dry_run": dry_run,
           "reset_to": reset_to_status}
    if dry_run or not rows:
        out["sample"] = [{"id": r.get("id"), "url": r.get("url"),
                          "status": r.get("status"),
                          "message": r.get("message")} for r in rows[:10]]
        return out
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            for r in rows:
                try:
                    cx.execute("""UPDATE history
                                  SET status = ?, message = ''
                                  WHERE id = ?""",
                               (reset_to_status, r["id"]))
                    out["processed"] += 1
                except Exception:
                    out["errors"] += 1
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
    return out


def bulk_delete(filter_dict: dict, *, dry_run: bool = True,
               delete_files: bool = False) -> dict:
    """Delete matching history rows. Optionally also unlink the files."""
    rows = _matching_rows(filter_dict or {})
    out = {"ok": True, "candidates_matched": len(rows),
           "processed": 0, "files_deleted": 0,
           "errors": 0, "dry_run": dry_run}
    if dry_run or not rows:
        out["sample"] = [{"id": r.get("id"),
                          "filename": r.get("filename"),
                          "size_mb": round(int(r.get("file_size", 0) or 0) / (1024**2), 1)}
                         for r in rows[:10]]
        out["total_size_gb"] = round(
            sum(int(r.get("file_size", 0) or 0) for r in rows) / (1024**3), 2)
        return out
    try:
        from . import db as _db
        for r in rows:
            if delete_files:
                fn = r.get("filename") or ""
                if fn and os.path.isfile(fn):
                    try:
                        os.unlink(fn)
                        out["files_deleted"] += 1
                    except OSError:
                        out["errors"] += 1
            try:
                with _db.db_conn() as cx:
                    cx.execute("DELETE FROM history WHERE id = ?", (r["id"],))
                out["processed"] += 1
            except Exception:
                out["errors"] += 1
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
    return out


def bulk_move(filter_dict: dict, *, target_dir: str,
             dry_run: bool = True) -> dict:
    """Move matching files to `target_dir`. Updates history.filename
    to the new path on success. Preserves the basename."""
    if not target_dir:
        return {"ok": False, "error": "target_dir required"}
    if not os.path.isdir(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"can't create target: {e}"}
    rows = _matching_rows(filter_dict or {})
    rows = [r for r in rows if r.get("filename") and os.path.isfile(r["filename"])]
    out = {"ok": True, "candidates_matched": len(rows),
           "processed": 0, "errors": 0, "dry_run": dry_run,
           "target_dir": target_dir}
    if dry_run or not rows:
        out["sample"] = [{"id": r.get("id"), "from": r.get("filename"),
                          "to": os.path.join(target_dir, os.path.basename(r["filename"]))}
                         for r in rows[:10]]
        return out
    for r in rows:
        src = r["filename"]
        dst = os.path.join(target_dir, os.path.basename(src))
        if os.path.normpath(src) == os.path.normpath(dst):
            continue
        try:
            shutil.move(src, dst)
            from . import db as _db
            with _db.db_conn() as cx:
                cx.execute("UPDATE history SET filename = ? WHERE id = ?",
                           (dst, r["id"]))
            out["processed"] += 1
        except Exception as e:
            out["errors"] += 1
            sys.stderr.write(f"[batch_ops] move {src}: {e}\n")
    return out


def bulk_dedup_scan(*, site_id: Optional[str] = None,
                    sample_bytes: int = 65536,
                    min_file_size_mb: int = 50,
                    limit: int = 5000) -> dict:
    """Find likely-duplicate files. Two-pass:
      Pass 1: group by file_size (cheap, no I/O)
      Pass 2: for size-collision groups, hash first sample_bytes from
              each file and find genuine duplicates

    Returns:
      {duplicate_groups: [[{id, filename, size}, ...], ...],
       total_files_scanned, candidates_with_size_collisions,
       confirmed_dup_groups, recoverable_gb}
    """
    try:
        from . import db as _db
        sql = """SELECT id, site_id, filename, file_size
                  FROM history
                  WHERE status='done' AND filename != ''
                    AND file_size >= ?"""
        params: list = [min_file_size_mb * 1024 * 1024]
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _db.db_conn() as cx:
            rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return {"error": "DB read failed", "duplicate_groups": []}
    # Pass 1: group by size
    size_groups: dict = {}
    for r in rows:
        sz = int(r.get("file_size", 0) or 0)
        size_groups.setdefault(sz, []).append(r)
    collisions = [g for g in size_groups.values() if len(g) > 1]
    # Pass 2: hash for each collision group
    confirmed_groups = []
    for group in collisions:
        by_hash: dict = {}
        for r in group:
            fn = r.get("filename") or ""
            if not fn or not os.path.isfile(fn):
                continue
            try:
                with open(fn, "rb") as f:
                    h = hashlib.sha256(f.read(sample_bytes)).hexdigest()
                by_hash.setdefault(h, []).append(r)
            except OSError:
                continue
        for h, items in by_hash.items():
            if len(items) > 1:
                confirmed_groups.append([
                    {"id": x["id"], "filename": x["filename"],
                     "size_bytes": x["file_size"], "site_id": x["site_id"]}
                    for x in items
                ])
    # Recoverable space: keep one copy per group, delete the rest
    recoverable = sum(
        sum(int(x["size_bytes"]) for x in group[1:])
        for group in confirmed_groups
    )
    return {
        "duplicate_groups": confirmed_groups,
        "total_files_scanned": len(rows),
        "candidates_with_size_collisions": len(collisions),
        "confirmed_dup_groups": len(confirmed_groups),
        "recoverable_gb": round(recoverable / (1024**3), 2),
    }


def bulk_reenrich(filter_dict: dict, *, dry_run: bool = True) -> dict:
    """Re-run quality grade + audio fingerprint over matching files.
    Updates a sidecar JSON next to each file with the latest enrichment
    results. Useful after upgrading ffmpeg or after Phase 107's
    enrichment module gets smarter."""
    rows = _matching_rows(filter_dict or {})
    rows = [r for r in rows if r.get("filename") and os.path.isfile(r["filename"])]
    out = {"ok": True, "candidates_matched": len(rows),
           "processed": 0, "errors": 0, "dry_run": dry_run}
    if dry_run or not rows:
        out["sample"] = [r.get("filename") for r in rows[:10]]
        return out
    try:
        from . import enrichment as _enr
        if not _enr.is_available():
            return {"ok": False, "error": "ffmpeg/ffprobe required"}
        import json
        for r in rows:
            fn = r["filename"]
            try:
                result = _enr.enrich(fn, do_chapters=False)
                # v3.47.8 (#43): atomic sidecar write — prevents half-written
                # JSON if the process is killed mid-flush (would crash the
                # next read with json.JSONDecodeError).
                sidecar = Path(fn).with_suffix(".bd-enrich.json")
                tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
                tmp.write_text(json.dumps(result, indent=2),
                               encoding="utf-8")
                import os as _os
                _os.replace(tmp, sidecar)
                out["processed"] += 1
            except Exception:
                out["errors"] += 1
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
    return out
