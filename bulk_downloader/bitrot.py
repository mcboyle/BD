"""Bit-rot detection scanner (Phase 105, Block N).

Builds on Phase 104's provenance ledger. Every download recorded with
a SHA-256 hash is a candidate for periodic re-verification. Silent
corruption — bit flips on the storage device, partial writes, fs
metadata desync, ransomware encryption — is invisible until you try
to play a file that's been rotting for months.

Strategy:
  • Sweep a configurable fraction of the library per night (default 5%)
  • For each candidate, recompute SHA-256 and compare to the
    ledger value
  • Mismatch → record a 'bitrot' row in a separate `integrity_issues`
    table, notify operator, optionally auto-replace from a configured
    backup mirror
  • Quotas: cap CPU/IO impact by limiting concurrent rehashes and
    pausing during high-load periods

Schema (lazy creation):
  integrity_issues(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    provenance_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    expected_sha256 TEXT,
    actual_sha256 TEXT,
    kind TEXT,                  -- 'missing' | 'modified' | 'truncated'
    repaired INTEGER DEFAULT 0,
    notes TEXT
  )

Operator config:
  • bitrot_scan_enabled: bool
  • bitrot_scan_fraction: float (0.0 - 1.0, default 0.05 = 5%/run)
  • bitrot_min_age_days: int (skip files <N days old; recent files
    just downloaded are pointless to re-verify)
  • bitrot_max_files_per_run: int (hard cap on work per invocation)

The scan is read-only on the data plane; the only DB writes are to
`integrity_issues` and the provenance row's verification timestamp.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Optional


def _ensure_integrity_table():
    """Lazy schema creation for integrity_issues."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS integrity_issues(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provenance_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                expected_sha256 TEXT DEFAULT '',
                actual_sha256 TEXT DEFAULT '',
                kind TEXT NOT NULL,
                repaired INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_ii_kind ON integrity_issues(kind)")
            cx.execute("CREATE INDEX IF NOT EXISTS idx_ii_ts ON integrity_issues(ts DESC)")
            # Also ensure provenance has the extra column we need to
            # avoid re-scanning recently-verified rows. Add if missing.
            try:
                cx.execute("ALTER TABLE provenance ADD COLUMN last_verified_ts REAL DEFAULT 0")
            except Exception:
                pass  # column already exists
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] schema init failed: {e}\n")


def _candidates(
    *,
    min_age_days: int,
    limit: int,
    reverify_after_days: int = 90,
) -> list[dict]:
    """Pick rows due for verification: have a sha256, are old enough,
    and either never verified or verified > reverify_after_days ago."""
    _ensure_integrity_table()
    try:
        from . import db as _db
        cutoff_old = time.time() - (min_age_days * 86400)
        cutoff_reverify = time.time() - (reverify_after_days * 86400)
        with _db.db_conn() as cx:
            rows = cx.execute("""
                SELECT id, source_url, final_filename, file_size, sha256,
                       last_verified_ts, ts
                FROM provenance
                WHERE sha256 != ''
                  AND ts <= ?
                  AND (last_verified_ts = 0 OR last_verified_ts < ?)
                ORDER BY RANDOM()
                LIMIT ?
            """, (cutoff_old, cutoff_reverify, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] candidate query failed: {e}\n")
        return []


def _mark_verified(prov_id: int):
    """Stamp last_verified_ts on the provenance row."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("UPDATE provenance SET last_verified_ts = ? WHERE id = ?",
                       (time.time(), int(prov_id)))
    except Exception:
        pass


def _record_issue(*, provenance_id: int, path: str, expected: str,
                  actual: str, kind: str, notes: str = ""):
    """Append one integrity_issues row."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO integrity_issues(
                ts, provenance_id, path, expected_sha256, actual_sha256,
                kind, notes
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), int(provenance_id), path,
                 expected or "", actual or "", kind, notes[:500]))
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] record_issue failed: {e}\n")


def verify_one(row: dict) -> dict:
    """Verify one provenance row's file against its recorded hash.

    Returns {ok, kind, message}:
      ok=True, kind='intact': hash matches
      ok=False, kind='missing': file gone from disk
      ok=False, kind='modified': hash mismatch
      ok=False, kind='truncated': size mismatch (caught before hash)
      ok=False, kind='error': couldn't read the file

    Records an integrity_issues row on any non-intact outcome.
    Stamps last_verified_ts on intact outcomes."""
    path = row.get("final_filename") or ""
    if not path:
        return {"ok": False, "kind": "missing", "message": "no path"}
    p = Path(path)
    if not p.exists():
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="missing")
        return {"ok": False, "kind": "missing",
                "message": f"file gone: {path}"}
    expected_size = int(row.get("file_size", 0) or 0)
    try:
        actual_size = p.stat().st_size
    except OSError as e:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="error", notes=f"stat: {e}")
        return {"ok": False, "kind": "error", "message": str(e)}
    if expected_size > 0 and actual_size != expected_size:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="truncated",
                      notes=f"expected {expected_size} got {actual_size}")
        return {"ok": False, "kind": "truncated",
                "message": f"size {actual_size} ≠ recorded {expected_size}"}
    try:
        from .provenance import compute_sha256
        actual_hash = compute_sha256(str(p))
    except Exception as e:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=row.get("sha256", ""), actual="",
                      kind="error", notes=f"hash: {e}")
        return {"ok": False, "kind": "error", "message": str(e)}
    expected = row.get("sha256", "") or ""
    if actual_hash != expected:
        _record_issue(provenance_id=row["id"], path=path,
                      expected=expected, actual=actual_hash or "",
                      kind="modified")
        return {"ok": False, "kind": "modified",
                "message": f"hash {actual_hash[:8]} ≠ recorded {expected[:8]}"}
    _mark_verified(row["id"])
    return {"ok": True, "kind": "intact", "message": "hash matches"}


def run_scan(*,
            scan_fraction: float = 0.05,
            min_age_days: int = 7,
            max_files: int = 100,
            reverify_after_days: int = 90) -> dict:
    """Verify a random subset of provenance rows. Returns summary.

    Designed to run from a nightly scheduler. The fraction × library
    size produces the candidate count, capped at max_files to keep
    each run bounded.
    """
    _ensure_integrity_table()
    # Determine total candidate pool size to compute the fraction
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) AS n FROM provenance
                                 WHERE sha256 != ''""").fetchone()
        total = int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception:
        total = 0
    if total == 0:
        return {"checked": 0, "intact": 0, "missing": 0, "modified": 0,
                "truncated": 0, "errors": 0, "total_library": 0}
    target = max(1, int(total * scan_fraction))
    target = min(target, max_files)
    candidates = _candidates(min_age_days=min_age_days, limit=target,
                            reverify_after_days=reverify_after_days)
    summary = {"checked": 0, "intact": 0, "missing": 0, "modified": 0,
               "truncated": 0, "errors": 0, "total_library": total}
    for row in candidates:
        try:
            r = verify_one(row)
            summary["checked"] += 1
            kind = r.get("kind", "error")
            if kind in summary:
                summary[kind] += 1
            else:
                summary["errors"] += 1
        except Exception as e:
            summary["errors"] += 1
            import sys
            sys.stderr.write(f"[bitrot] verify {row.get('id')} raised: {e}\n")
    return summary


def list_issues(*, kind: Optional[str] = None, repaired: Optional[bool] = None,
                limit: int = 100) -> list:
    """Return recent integrity_issues rows. Filter by kind ('missing',
    'modified', 'truncated', 'error') and repaired status."""
    _ensure_integrity_table()
    sql = "SELECT * FROM integrity_issues WHERE 1=1"
    params: list = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if repaired is not None:
        sql += " AND repaired = ?"
        params.append(1 if repaired else 0)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(int(limit))
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def stats() -> dict:
    """Aggregate counters for the bit-rot dashboard."""
    _ensure_integrity_table()
    out = {"open_issues": 0, "by_kind": {}, "repaired": 0,
           "last_scan_ts": 0}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            for row in cx.execute("""SELECT kind, COUNT(*) AS n
                                     FROM integrity_issues
                                     WHERE repaired = 0
                                     GROUP BY kind"""):
                out["by_kind"][row[0]] = int(row[1])
                out["open_issues"] += int(row[1])
            row = cx.execute("""SELECT COUNT(*) AS n FROM integrity_issues
                                 WHERE repaired = 1""").fetchone()
            out["repaired"] = int(row[0]) if row else 0
            row = cx.execute("""SELECT MAX(last_verified_ts) AS t
                                 FROM provenance""").fetchone()
            out["last_scan_ts"] = float(row[0] or 0) if row else 0.0
    except Exception:
        pass
    return out


def mark_repaired(issue_id: int, *, notes: str = "") -> bool:
    """Flag an integrity_issue as resolved. Caller might re-download
    the file, restore from backup, or accept the drift."""
    _ensure_integrity_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""UPDATE integrity_issues SET repaired = 1,
                              notes = COALESCE(notes,'') || ?
                              WHERE id = ?""",
                              (f" | repaired {time.strftime('%Y-%m-%d')}: {notes}"[:500],
                               int(issue_id)))
            return cur.rowcount > 0
    except Exception:
        return False
