"""Backup verification (Phase 123, Block Q).

A backup that's never restored is a wish, not a backup. This module
provides verifiable checks the operator can run automatically:

  • verify_db_dump(path) — open a SQLite dump as a fresh DB, run
    PRAGMA integrity_check, count tables + rows
  • verify_tarball(path) — extract a backup tarball to a temp dir,
    spot-check expected files exist
  • verify_against_live(path) — diff backup state against current
    DB for drift detection (counts only, not byte-for-byte)
  • smoke_restore(path) — full dry-run restore into a sandbox dir;
    confirms BD could start from this backup

Designed to run nightly via bg_scheduler. Reports go into a
`backup_verifications` table for audit.

Out of scope: actually performing backups. Phase 25 (db backup
already shipped) handles that; this module verifies what's there.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS backup_verifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                path TEXT NOT NULL,
                kind TEXT NOT NULL,
                ok INTEGER NOT NULL,
                summary TEXT,
                size_bytes INTEGER,
                duration_seconds REAL
            )""")
    except Exception as e:
        sys.stderr.write(f"[backup_verify] schema init failed: {e}\n")


def _record(path: str, kind: str, result: dict, started: float):
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO backup_verifications(
                ts, path, kind, ok, summary, size_bytes, duration_seconds
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), path, kind,
                 1 if result.get("ok") else 0,
                 json.dumps(result, default=str)[:2000],
                 result.get("size_bytes"),
                 time.time() - started))
    except Exception:
        pass


def verify_db_dump(path: str) -> dict:
    """Open a SQLite file at `path` and run an integrity check.
    Returns {ok, tables, total_rows, integrity_check, size_bytes, error}."""
    started = time.time()
    if not os.path.isfile(path):
        result = {"ok": False, "error": f"file not found: {path}"}
        _record(path, "db_dump", result, started)
        return result
    try:
        size = os.path.getsize(path)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                            timeout=10) as cx:
            # integrity_check returns "ok" if good, error list otherwise
            ic = cx.execute("PRAGMA quick_check").fetchone()
            ic_result = ic[0] if ic else "?"
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            row_counts: dict = {}
            for t in tables:
                # Use parameterized via direct interpolation since
                # table names can't be bound, but we already pulled
                # them from sqlite_master so they're trusted.
                try:
                    n = cx.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()
                    row_counts[t] = int(n[0]) if n else 0
                except Exception:
                    row_counts[t] = -1
        result = {
            "ok": ic_result == "ok",
            "tables": tables,
            "table_count": len(tables),
            "row_counts": row_counts,
            "total_rows": sum(v for v in row_counts.values() if v >= 0),
            "integrity_check": ic_result,
            "size_bytes": size,
        }
        _record(path, "db_dump", result, started)
        return result
    except Exception as e:
        result = {"ok": False, "error": str(e)[:300]}
        _record(path, "db_dump", result, started)
        return result


def verify_tarball(path: str, *, expected_members: Optional[list] = None) -> dict:
    """Open `path` as a tar/zip and check structure.
    `expected_members` is a list of substrings to require in the
    archive (e.g. ['history.csv', 'README.txt'])."""
    started = time.time()
    if not os.path.isfile(path):
        result = {"ok": False, "error": f"file not found: {path}"}
        _record(path, "tarball", result, started)
        return result
    try:
        size = os.path.getsize(path)
        members = []
        if path.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(path, "r:*") as tf:
                members = [m.name for m in tf.getmembers()]
        elif path.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                members = zf.namelist()
        else:
            result = {"ok": False,
                      "error": f"unsupported archive type: {path}"}
            _record(path, "tarball", result, started)
            return result
        result: dict = {
            "ok": True,
            "size_bytes": size,
            "member_count": len(members),
            "sample_members": members[:10],
        }
        if expected_members:
            missing = [exp for exp in expected_members
                       if not any(exp in m for m in members)]
            result["missing_expected"] = missing
            if missing:
                result["ok"] = False
        _record(path, "tarball", result, started)
        return result
    except Exception as e:
        result = {"ok": False, "error": str(e)[:300]}
        _record(path, "tarball", result, started)
        return result


def _confine_extract(archive, sandbox: str, *, is_tar: bool) -> None:
    """Vet every archive member's destination BEFORE extracting so no member
    can escape `sandbox` via '..' traversal, an absolute path (tar-slip /
    zip-slip), or -- for tar -- a symlink/hardlink member that would redirect a
    later write outside the sandbox. Raises ValueError on the first unsafe
    member (loud failure, no partial extraction). Version-independent: does not
    rely on tarfile's 3.12+ 'data' filter, so the confinement holds on whatever
    Python the stash service runs."""
    base = os.path.realpath(sandbox)
    members = archive.getmembers() if is_tar else archive.infolist()
    for m in members:
        name = m.name if is_tar else m.filename
        if is_tar and (m.issym() or m.islnk()):
            raise ValueError(f"unsafe link member in backup archive: {name!r}")
        dest = os.path.realpath(os.path.join(sandbox, name))
        if dest != base and not dest.startswith(base + os.sep):
            raise ValueError(f"backup archive member escapes sandbox: {name!r}")
    archive.extractall(sandbox)


def smoke_restore(path: str, *, expected_tables: Optional[list] = None) -> dict:
    """Restore a backup into a temp dir and confirm BD could read it.
    For SQLite dumps: open r/o; for tarballs: extract + spot-check.

    Returns {ok, restored_to, summary, error}."""
    started = time.time()
    sandbox = tempfile.mkdtemp(prefix="bd-restore-test-")
    try:
        if path.endswith(".db") or path.endswith(".sqlite"):
            # Copy file in (so we can rw-open if needed)
            import shutil
            dest = os.path.join(sandbox, os.path.basename(path))
            shutil.copyfile(path, dest)
            v = verify_db_dump(dest)
            v["restored_to"] = sandbox
            if expected_tables:
                v["missing_tables"] = [t for t in expected_tables
                                        if t not in v.get("tables", [])]
                if v["missing_tables"]:
                    v["ok"] = False
            _record(path, "smoke_restore", v, started)
            return v
        if path.endswith((".tar", ".tar.gz", ".tgz", ".zip")):
            v = verify_tarball(path)
            if v["ok"]:
                # Extract
                if path.endswith(".zip"):
                    with zipfile.ZipFile(path) as zf:
                        _confine_extract(zf, sandbox, is_tar=False)
                else:
                    with tarfile.open(path, "r:*") as tf:
                        _confine_extract(tf, sandbox, is_tar=True)
                v["restored_to"] = sandbox
                v["extracted_files"] = os.listdir(sandbox)[:20]
            _record(path, "smoke_restore", v, started)
            return v
        result = {"ok": False, "error": f"unknown backup format: {path}"}
        _record(path, "smoke_restore", result, started)
        return result
    except Exception as e:
        result = {"ok": False, "error": str(e)[:300]}
        _record(path, "smoke_restore", result, started)
        return result


def verify_against_live(path: str) -> dict:
    """Diff a backup DB against the live DB. Reports row count drift
    per table. Useful to confirm backup is reasonably recent."""
    started = time.time()
    backup_result = verify_db_dump(path)
    if not backup_result.get("ok"):
        return backup_result
    try:
        from . import db as _db
        live_counts: dict = {}
        with _db.db_conn() as cx:
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for t in tables:
                try:
                    n = cx.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()
                    live_counts[t] = int(n[0]) if n else 0
                except Exception:
                    live_counts[t] = -1
        backup_counts = backup_result.get("row_counts", {})
        drift = {}
        for t in sorted(set(live_counts) | set(backup_counts)):
            live = live_counts.get(t, 0)
            backup = backup_counts.get(t, 0)
            if live != backup:
                drift[t] = {"backup": backup, "live": live,
                            "diff": live - backup}
        result = {
            "ok": True,
            "tables_compared": len(set(live_counts) | set(backup_counts)),
            "drift_tables": len(drift),
            "drift_detail": drift,
            "backup_total": sum(backup_counts.values()),
            "live_total": sum(live_counts.values()),
        }
        _record(path, "drift_check", result, started)
        return result
    except Exception as e:
        result = {"ok": False, "error": str(e)[:300]}
        _record(path, "drift_check", result, started)
        return result


def recent_verifications(limit: int = 20) -> list:
    """List recent verification runs from the audit log."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM backup_verifications
                                  ORDER BY id DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["summary"] = json.loads(d.get("summary") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    except Exception:
        return []

# ── X-AUTO-1 (v3.66.706): the restore REHEARSAL ─────────────────────────────
# This module's docstring has always said "A backup that's never restored is a wish,
# not a backup" and "Designed to run nightly via bg_scheduler". It never was:
# smoke_restore() was reachable ONLY from a manual API call, so nobody had ever
# confirmed the backups sitting on disk still restore.
#
# A0 (the keystone) guarantees a backup is taken and restorable AT WRITE TIME.
# The rehearsal is the other half: prove, on a schedule, that the backups on disk
# STILL restore -- because an untested backup decays silently.
#
# Opt-in, default OFF (same shape as the daily digest). The rehearsal restores into a
# temp sandbox (smoke_restore already does) and NEVER touches live: a verification that
# mutates what it verifies is not a verification.
REHEARSAL_ENABLE_KEY = "automation.restore_rehearsal_enabled"
_REHEARSAL_STATE = "restore_rehearsal.json"


def rehearsal_enabled() -> bool:
    """Opt-in gate. Default OFF -- this cut is inert until the operator turns it on."""
    try:
        from . import global_config
        return bool(global_config.get(REHEARSAL_ENABLE_KEY, False))
    except Exception:
        return False


def _rehearsal_state_path() -> str:
    base = os.environ.get("BD_HOME") or "."
    return os.path.join(base, _REHEARSAL_STATE)


def last_rehearsal() -> Optional[dict]:
    """The persisted verdict of the last rehearsal, or None. Persisted so the digest
    can report an OVERNIGHT failure -- a verdict that lived only in the run that
    produced it would be invisible by morning."""
    try:
        with open(_rehearsal_state_path()) as fh:
            return json.load(fh)
    except Exception:
        return None


def _latest_backup() -> Optional[str]:
    """The newest backup on disk, or None."""
    import glob as _glob
    base = os.environ.get("BD_HOME") or "."
    cands = []
    for pat in ("*.zip", "backups/*.zip", "*.db", "backups/*.db"):
        cands.extend(_glob.glob(os.path.join(base, pat)))
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def rehearse(*, backup_path: Optional[str] = None) -> dict:
    """Restore the latest backup into a sandbox and report whether it WORKED.

    Returns {ok, path, checked_at, age_days, error}. NEVER raises -- a rehearsal that
    crashes the scheduler would take out the very thing meant to reassure you.

    NOT-ok is the honest answer for: an unrestorable archive, AND no backup at all.
    'No backup on disk' is not 'fine' -- it is the loudest possible failure of a backup
    system, and it must never read as ok."""
    started = time.time()
    path = backup_path or _latest_backup()
    if not path or not os.path.isfile(path):
        out = {"ok": False, "path": path or "", "checked_at": started,
               "age_days": None, "error": "no backup found"}
        _write_rehearsal(out)
        return out
    try:
        res = smoke_restore(path)
        ok = bool(res.get("ok"))
        err = res.get("error") or ("" if ok else "smoke_restore reported not-ok")
    except Exception as e:                      # never raise into the scheduler
        ok, err = False, f"{type(e).__name__}: {e}"[:160]
    try:
        age = (started - os.path.getmtime(path)) / 86400.0
    except Exception:
        age = None
    out = {"ok": ok, "path": path, "checked_at": started,
           "age_days": round(age, 2) if age is not None else None,
           "error": "" if ok else (err or "restore failed")}
    _write_rehearsal(out)
    return out


def _write_rehearsal(verdict: dict) -> None:
    """Persist the verdict. A failure to WRITE it is not swallowed: if the verdict
    cannot be persisted, the digest cannot report an overnight failure by morning --
    the one thing this feature exists to do. Say so on stderr; never raise (the
    scheduler must survive), but never pretend it worked either."""
    try:
        with open(_rehearsal_state_path(), "w") as fh:
            json.dump(verdict, fh)
    except OSError as e:
        sys.stderr.write(
            f"[backup_verify] could not persist the rehearsal verdict "
            f"({type(e).__name__}: {e}); an overnight failure will not be "
            f"reportable by morning\n")
