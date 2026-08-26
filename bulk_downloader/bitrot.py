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
import sqlite3
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
            #
            # Ask FIRST rather than guess from the failure. Until v3.66.931
            # this was a bare `except Exception: pass`, so every failure read
            # as "column already exists" -- a missing provenance table, a
            # locked database and a read-only mount all returned quietly and
            # the init reported a schema it had not created. _candidates then
            # SELECTs last_verified_ts (below), so the real fault surfaced
            # later and somewhere else.
            #
            # The SQLite result code cannot separate these: `duplicate column
            # name` and `no such table` are BOTH SQLITE_ERROR (1), measured.
            # So the structural check carries the common path, and the
            # message-matched tolerance underneath it covers only the genuine
            # race where two processes pass the check and both ALTER.
            have = {r[1] for r in cx.execute("PRAGMA table_info(provenance)")}
            if "last_verified_ts" not in have:
                try:
                    cx.execute("ALTER TABLE provenance "
                               "ADD COLUMN last_verified_ts REAL DEFAULT 0")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        raise
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] schema init failed: {e}\n")


class InventoryUnavailable(RuntimeError):
    """The integrity inventory could not be measured."""


def _table_is_absent(error: sqlite3.OperationalError, table: str) -> bool:
    """Return whether SQLite reported one exact, expected schema absence."""
    return str(error).casefold() == f"no such table: {table}".casefold()


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
        raise InventoryUnavailable(f"candidate inventory unreadable: {e}") from e


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
    """Append one integrity_issues row, unless that finding is already open.

    v3.66.925 -- the dedup half. A non-intact verdict returns before
    _mark_verified, so the row keeps its last_verified_ts of 0 and _candidates
    re-selects it on the very next scan. Without this guard ONE genuinely
    missing file adds one row per night, forever: `stats()` counts unrepaired
    rows as `open_issues`, so alerts_engine.py:75's `bitrot_growing` rule fires
    on a library whose only fault is a single file that has been gone for a
    week and is being re-noticed nightly.

    Keyed on (provenance_id, kind) rather than including the path, so the same
    finding stays one row even if download_dir moves. `repaired=0` is part of
    the key on purpose: once the operator resolves an issue, a later
    recurrence opens a FRESH row, which is the signal they want.
    """
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            already_open = cx.execute(
                """SELECT 1 FROM integrity_issues
                   WHERE provenance_id = ? AND kind = ? AND repaired = 0
                   LIMIT 1""", (int(provenance_id), kind)).fetchone()
            if already_open:
                return
            cx.execute("""INSERT INTO integrity_issues(
                ts, provenance_id, path, expected_sha256, actual_sha256,
                kind, notes
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), int(provenance_id), path,
                 expected or "", actual or "", kind, notes[:500]))
    except Exception as e:
        import sys
        sys.stderr.write(f"[bitrot] record_issue failed: {e}\n")


def verify_one(row: dict, *, download_dir: str = "",
               index: Optional[dict] = None) -> dict:
    """Verify one provenance row's file against its recorded hash.

    Returns {ok, kind, message}:
      ok=True,  kind='intact':    hash matches
      ok=False, kind='missing':   file genuinely gone from disk
      ok=False, kind='modified':  hash mismatch
      ok=False, kind='truncated': size mismatch (caught before hash)
      ok=False, kind='error':     couldn't read the file
      ok=False, kind='ambiguous': several files share the recorded basename
      ok=False, kind='unknown':   no download_dir to resolve against

    Records an integrity_issues row for missing/modified/truncated/error.
    Records NOTHING for ambiguous/unknown. Stamps last_verified_ts on intact.

    v3.66.925 -- `final_filename` IS A BARE BASENAME. runner.py:2040 records
    `extra["filename"]`, and runner_transport.py:1297 shows that key is the
    basename ("path" is the separate key holding the full path). This function
    fed it to `Path(path).exists()`, which resolves against the PROCESS CWD, so
    every relative row missed and took the branch that WRITES a "your file is
    gone" row for a file sitting on disk.

    That made this the only producer in item 12 that persisted its wrong
    answer, and it did not converge: the missing branch returns before
    _mark_verified, so last_verified_ts stayed 0, _candidates re-selected the
    same rows the next night, and the false rows were written again. Measured
    before the fix -- three present files, three consecutive scans, 3 -> 6 -> 9
    rows. bg_scheduler.py:254 runs it nightly and alerts_engine.py:75 alarms on
    exactly that growth, so the system reported its own bookkeeping as rot.

    Resolution goes through library_final._resolve_recorded rather than a sixth
    hand-rolled `download_dir / fn` join -- a flat join cannot find a file the
    filename template put in a subdirectory, since the recorded basename has
    already lost it. Its "ambiguous" and "unknown" states are deliberately NOT
    folded into "missing": a row this scanner cannot place is not evidence of
    rot, and guessing first-match-wins would hash the wrong twin and report a
    modification that is an artefact of the guess.
    """
    from .library_final import _basename_index, _resolve_recorded

    recorded = row.get("final_filename") or ""
    if index is None:
        index = _basename_index(download_dir)
    p, state = _resolve_recorded(recorded, download_dir, index)
    if state == "ambiguous":
        return {"ok": False, "kind": "ambiguous",
                "message": f"several files match {recorded!r}; refusing to guess"}
    if state == "unknown":
        return {"ok": False, "kind": "unknown",
                "message": f"nothing to resolve {recorded!r} against"}
    if state == "absent" or p is None:
        shown = str(p) if p is not None else recorded
        _record_issue(provenance_id=row["id"], path=shown,
                      expected=row.get("sha256", ""), actual="",
                      kind="missing")
        return {"ok": False, "kind": "missing",
                "message": f"file gone: {shown}"}
    path = str(p)
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
            reverify_after_days: int = 90,
            download_dir: str = "",
            download_dirs=()) -> dict:
    """Verify a random subset of provenance rows. Returns summary.

    Designed to run from a nightly scheduler. The fraction × library
    size produces the candidate count, capped at max_files to keep
    each run bounded.

    `download_dir` is what a recorded BASENAME is resolved against; see
    verify_one. It is optional and defaults to "" so every existing caller
    keeps working -- but omitted, no relative row can be placed and the summary
    reports them under `unknown` rather than writing them off as missing.

    THAT NO-OP IS DELIBERATE AND IT IS VISIBLE. bg_scheduler.py:254 calls this
    with no download_dir, so until that call site is given one the nightly scan
    decides nothing on a relative library. A scan that reports `unknown: N` is
    saying it could not see its subject; the behaviour this replaced reported
    `missing: N` and persisted it, which is the same blindness wearing a
    verdict. Sourcing the configured roots belongs with the path-allowlist
    validation the library routes already do (app_library.py:262) and is a
    separate cut, not a line here.

    The index is built ONCE per scan rather than per row: a library is a few
    thousand files and a per-row rglob would make the nightly job quadratic.
    """
    def unavailable(error: Exception, *, total_library=None) -> dict:
        import sys
        sys.stderr.write(f"[bitrot] inventory unavailable: {error}\n")
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": str(error)[:200],
            "checked": None,
            "intact": None,
            "missing": None,
            "modified": None,
            "truncated": None,
            "errors": None,
            "ambiguous": None,
            "unknown": None,
            "total_library": total_library,
        }

    _ensure_integrity_table()
    # Determine total candidate pool size to compute the fraction
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) AS n FROM provenance
                                 WHERE sha256 != ''""").fetchone()
        total = int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception as e:
        return unavailable(
            InventoryUnavailable(f"provenance inventory unreadable: {e}"))
    if total == 0:
        return {"ok": True, "available": True,
                "inventory_status": "measured",
                "checked": 0, "intact": 0, "missing": 0, "modified": 0,
                "truncated": 0, "errors": 0, "ambiguous": 0, "unknown": 0,
                "total_library": 0}
    target = max(1, int(total * scan_fraction))
    target = min(target, max_files)
    try:
        candidates = _candidates(min_age_days=min_age_days, limit=target,
                                reverify_after_days=reverify_after_days)
    except InventoryUnavailable as e:
        return unavailable(e, total_library=total)
    # Additive keys, verified rather than assumed: no test references this
    # module, `/api/bitrot/scan` hands the dict straight to jsonify
    # (app_bitrot.py:28), and frontend/src/lib/api-types.ts declares no bitrot
    # scan type -- so `ambiguous` and `unknown` cost zero TS edits. They are
    # _resolve_recorded's own state strings verbatim, matching the v3.66.916
    # precedent, so a reader maps counter to state with no translation step.
    summary = {"ok": True, "available": True,
               "inventory_status": "measured",
               "checked": 0, "intact": 0, "missing": 0, "modified": 0,
               "truncated": 0, "errors": 0, "ambiguous": 0, "unknown": 0,
               "total_library": total}
    from . import library_final as _lf
    # `download_dirs` is the multi-site form and `download_dir` the original
    # single one; a caller may pass either. Both are threaded to verify_one so
    # the flat join and the index agree on the same root set -- handing the
    # index every root while verify_one saw only one would resolve a row the
    # per-row call could not place.
    roots = list(download_dirs) or ([download_dir] if download_dir else [])
    index = _lf._basename_index(roots)
    for row in candidates:
        try:
            r = verify_one(row, download_dir=roots, index=index)
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
    out = {"ok": True, "available": True, "inventory_status": "measured",
           "error": "", "open_issues": 0, "by_kind": {}, "repaired": 0,
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
            try:
                row = cx.execute("""SELECT MAX(last_verified_ts) AS t
                                     FROM provenance""").fetchone()
            except sqlite3.OperationalError as e:
                if not _table_is_absent(e, "provenance"):
                    raise
                row = None
            out["last_scan_ts"] = float(row[0] or 0) if row else 0.0
    except sqlite3.OperationalError as e:
        # A fresh install has no inventory table until the first scan.  That
        # absence is a measured empty inventory, not a failed measurement.
        # Keep every other SQLite operational failure on the fail-closed path
        # below: locked, read-only, and otherwise unreadable stores are not
        # evidence of zero open issues.
        if _table_is_absent(e, "integrity_issues"):
            return out
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": f"integrity issue inventory unreadable: {e}"[:200],
            "open_issues": None,
            "by_kind": None,
            "repaired": None,
            "last_scan_ts": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "error": f"integrity issue inventory unreadable: {e}"[:200],
            "open_issues": None,
            "by_kind": None,
            "repaired": None,
            "last_scan_ts": None,
        }
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
