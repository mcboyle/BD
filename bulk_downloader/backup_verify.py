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
import shutil
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


# -- Row 423: the archive verdict is a TRISTATE, and the third state is not a pass.
#
# "ok"      the measurement ran and the archive is healthy
# "failed"  the measurement ran and the archive is BAD (missing, empty, or short
#           an expected member)
# "unknown" the measurement was NOT AVAILABLE -- a type we cannot open, or an
#           unreadable/truncated archive. CLAUDE.md A7: an unavailable
#           measurement reports UNKNOWN rather than OK.
#
# `ok` stays False for BOTH refusing states, so every existing consumer that
# reads only `ok` fails closed; `state` is what lets an operator tell "this
# backup is bad" apart from "I could not tell you whether it is".
#
# String literals rather than an import of automation_status.OK/FAILED/UNKNOWN
# on purpose: that module imports THIS one, so importing it back would close a
# cycle. The values are identical to it.
_STATE_OK = "ok"
_STATE_FAILED = "failed"
_STATE_UNKNOWN = "unknown"


def verify_tarball(path: str, *, expected_members: Optional[list] = None) -> dict:
    """Open `path` as a tar/zip and check structure.
    `expected_members` is a list of substrings to require in the
    archive (e.g. ['history.csv', 'README.txt']).

    Returns {ok, state, size_bytes, member_count, sample_members, error}. An
    archive that OPENS is not thereby verified: a backup job that dies after
    writing the archive header leaves a readable archive with ZERO members, and
    that is the loudest possible backup failure, not a pass."""
    started = time.time()
    if not os.path.isfile(path):
        result = {"ok": False, "state": _STATE_FAILED,
                  "error": f"file not found: {path}"}
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
            # Not a measured failure: we never got to look inside it.
            result = {"ok": False, "state": _STATE_UNKNOWN,
                      "error": f"unsupported archive type: {path}"}
            _record(path, "tarball", result, started)
            return result
        result: dict = {
            "ok": True,
            "state": _STATE_OK,
            "size_bytes": size,
            "member_count": len(members),
            "sample_members": members[:10],
        }
        if not members:
            # THE zero-member refusal. Downgraded BEFORE _record, so the audit
            # row an operator reads back months later carries it too. The
            # message NAMES the emptiness: a missing file and an unreadable
            # archive are also falsy, and a refusal that cannot be told apart
            # from those is how a dead backup job reads as a verified one.
            result["ok"] = False
            result["state"] = _STATE_FAILED
            result["error"] = (
                f"backup archive is EMPTY: 0 members in {path} "
                f"({size} bytes) -- there is nothing here to restore")
        if expected_members:
            missing = [exp for exp in expected_members
                       if not any(exp in m for m in members)]
            result["missing_expected"] = missing
            if missing:
                # A measured miss, not an unavailable measurement.
                result["ok"] = False
                result["state"] = _STATE_FAILED
        _record(path, "tarball", result, started)
        return result
    except Exception as e:
        # The archive would not open (truncated, corrupt, wrong magic): the
        # measurement is UNAVAILABLE, which is never OK and is not the same
        # claim as "measured, and bad".
        result = {"ok": False, "state": _STATE_UNKNOWN, "error": str(e)[:300]}
        _record(path, "tarball", result, started)
        return result


# BK-REST F9: per-member extraction cap, equal to backup._RESTORE_MEMBER_CAP (the
# cap restore_backup applies to the same member); kept local so this module adds
# no import edge to backup.py.
_SMOKE_MEMBER_CAP = 256 * 1024 * 1024


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
        # BK-REST F9: the same per-member cap restore_backup applies.
        size = m.size if is_tar else m.file_size
        if size > _SMOKE_MEMBER_CAP:
            raise ValueError(f"backup archive member {name!r} is {size} bytes "
                             f"> restore cap {_SMOKE_MEMBER_CAP}")
        dest = os.path.realpath(os.path.join(sandbox, name))
        if dest != base and not dest.startswith(base + os.sep):
            raise ValueError(f"backup archive member escapes sandbox: {name!r}")
    archive.extractall(sandbox)


def smoke_restore(path: str, *, expected_tables: Optional[list] = None) -> dict:
    """Restore a backup into a temp dir and confirm BD could read it.
    For SQLite dumps: open r/o; for tarballs: extract + spot-check.

    Returns {ok, restored_to, summary, error}. The sandbox is removed on every
    return that does not hand it back in ``restored_to`` (BK-REST F9); a caller
    that receives ``restored_to`` owns it."""
    started = time.time()
    sandbox = tempfile.mkdtemp(prefix="bd-restore-test-")
    handed_back = False
    try:
        if path.endswith(".db") or path.endswith(".sqlite"):
            # Copy file in (so we can rw-open if needed)
            dest = os.path.join(sandbox, os.path.basename(path))
            shutil.copyfile(path, dest)
            v = verify_db_dump(dest)
            v["restored_to"] = sandbox
            handed_back = True
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
                handed_back = True
                v["extracted_files"] = os.listdir(sandbox)[:20]
            _record(path, "smoke_restore", v, started)
            return v
        if path.endswith(".bdbk"):
            # BR-F4: a whole-wrapped encrypted backup cannot be opened without its
            # passphrase, which is never stored -- say so; never call it restorable.
            result = {"ok": False, "state": _STATE_UNKNOWN,
                      "error": f"encrypted backup (.bdbk) cannot be rehearsed "
                               f"without its passphrase: {path}"}
            _record(path, "smoke_restore", result, started)
            return result
        result = {"ok": False, "state": _STATE_UNKNOWN,
                  "error": f"unknown backup format: {path}"}
        _record(path, "smoke_restore", result, started)
        return result
    except Exception as e:
        # The restore was ATTEMPTED and did not succeed (an escaping member, a
        # write that failed): measured, so failed rather than unknown.
        result = {"ok": False, "state": _STATE_FAILED, "error": str(e)[:300]}
        _record(path, "smoke_restore", result, started)
        return result
    finally:
        if not handed_back:
            shutil.rmtree(sandbox, ignore_errors=True)


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


# BR-F4: what the app writes as a backup -- backup.default_backup_filename() is a .zip,
# renamed .bdbk when whole-wrapped with a passphrase -- plus DB dumps kept in backups/.
# NOT a *.db at the BD_HOME root: those are the LIVE stores (downloader_history.db), and
# counting them made the rehearsal "restore" the live DB and pass with no backup on disk.
_BACKUP_PATTERNS = ("*.zip", "*.bdbk", "backups/*.zip", "backups/*.bdbk", "backups/*.db")


def _file_id(path) -> Optional[tuple]:
    """(st_dev, st_ino) of the file `path` resolves to, or None."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_dev, st.st_ino)


def _live_store_ids() -> set:
    """BR-F4 r2 (cx147): file identities of the LIVE stores -- the app DB wherever
    db._resolve_db_path() puts it, plus every *.db at the BD_HOME / BD_INSTALL_DIR
    root. A candidate that IS one of them (a symlink or hard link in backups/) is the
    live database under another name, not a backup."""
    import glob as _glob
    paths = []
    for root in (os.environ.get("BD_HOME") or ".", os.environ.get("BD_INSTALL_DIR")):
        if root:
            paths.extend(_glob.glob(os.path.join(root, "*.db")))
    try:
        from . import db as _db
        paths.append(_db._resolve_db_path())
    except Exception:
        pass
    return {i for i in (_file_id(p) for p in paths) if i is not None}


def _latest_backup() -> Optional[str]:
    """The newest backup on disk, or None."""
    import glob as _glob
    base = os.environ.get("BD_HOME") or "."
    cands = []
    for pat in _BACKUP_PATTERNS:
        cands.extend(_glob.glob(os.path.join(base, pat)))
    live = _live_store_ids()
    cands = [c for c in cands
             if os.path.isfile(c) and _file_id(c) not in live]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


_ARCHIVE_TOP = (".zip", ".bdbk")              # BD_HOME (or CWD) top level: a .db there is the live DB
_ARCHIVE_SUB = (".zip", ".bdbk", ".db")       # BD_HOME/backups/


class _Unreadable(Exception):
    """A store directory that exists but cannot be listed or measured."""

    def __init__(self, rel: str, err):
        super().__init__(rel)
        self.rel = rel
        self.reason = err if isinstance(err, str) else (err.strerror or type(err).__name__)


_NOFOLLOW_FD = (os.open in os.supports_dir_fd and os.scandir in os.supports_fd
                and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))
_SYMLINK = "symlink not followed"


class _Dir:
    """An open store directory. The configured root (BD_HOME, the DB dir, the project root) is
    followed as configured; every component BELOW it is opened no-follow (O_NOFOLLOW relative to
    its parent's fd, or an islink refusal where fds are unavailable), so a symlinked store
    boundary is never entered -- it is reported, not followed."""

    def __init__(self, path: Path, fd, rel: str):
        self.path, self.fd, self.rel = path, fd, rel

    @classmethod
    def root(cls, path: Path, rel: str = "."):
        """None when absent; _Unreadable when present but not openable."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY) if _NOFOLLOW_FD else None
            if fd is None and not path.is_dir():
                return None
            return cls(path, fd, rel)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as e:
            raise _Unreadable(rel, e)

    def child(self, name: str, rel: str):
        """Open component `name` without following a symlink; None when absent."""
        path = self.path / name
        if _NOFOLLOW_FD:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            except FileNotFoundError:
                return None
            except OSError as e:
                if os.path.islink(path):
                    raise _Unreadable(rel, _SYMLINK)
                if isinstance(e, NotADirectoryError):
                    return None
                raise _Unreadable(rel, e)
            return _Dir(path, fd, rel)
        if os.path.islink(path):
            raise _Unreadable(rel, _SYMLINK)
        if not path.is_dir():
            return None
        return _Dir(path, None, rel)

    def entries(self) -> list:
        """Children that are not symlinks (a symlinked entry is never listed or entered)."""
        try:
            with os.scandir(self.fd if self.fd is not None else self.path) as it:
                return sorted((e for e in it if not e.is_symlink()), key=lambda e: e.name)
        except OSError as e:
            raise _Unreadable(self.rel, e)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _tree_size(d: "_Dir") -> int:
    """Bytes of the regular files under a generation dir, opened no-follow all the way down."""
    total = 0
    for e in d.entries():
        if e.is_dir(follow_symlinks=False):
            sub = d.child(e.name, f"{d.rel}/{e.name}")
            if sub is not None:
                with sub:
                    total += _tree_size(sub)
        elif e.is_file(follow_symlinks=False):
            total += e.stat(follow_symlinks=False).st_size
    return total


def _inventory_row(kind: str, name: str, parent: "_Dir", entry) -> dict:
    """Metadata only (stat, never contents): a directory's size is the sum of its files."""
    st = entry.stat(follow_symlinks=False)
    size = st.st_size
    if entry.is_dir(follow_symlinks=False):
        gen = parent.child(entry.name, name)
        size = 0
        if gen is not None:
            with gen:
                size = _tree_size(gen)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
    return {"name": name, "size": size, "created": created, "kind": kind, "_mtime": st.st_mtime}


def backup_inventory() -> dict:
    """Every backup BD keeps on disk (F020 "list backups"), newest first.

    kinds -- archive: backup .zip/.bdbk the rehearsal reads (BD_HOME or CWD; in its backups/
    .db copies count too; a .db at the top level is the live DB, not a backup); premigration:
    the <db>.premigration.bak migrations write; template_gold: templates/.gold_backups/<host>/<ts>/
    generations; profile_sync: profiles/<site>/<profile>/.sync_backups/<ts>/ move-asides.
    Returns {"backups": [{name, size, created, kind}], "errors": [{kind, name, error}]}. An absent
    store is simply empty; a store directory that exists but cannot be read is reported in errors
    (name relative to its store, error = the OS reason only) and the rest of the scan continues --
    never an empty success. Symlinks are neither listed nor followed: below the configured roots
    (BD_HOME or CWD, the DB dir, the project root) every directory is opened no-follow, and a
    symlinked store directory is reported as "symlink not followed"."""
    rows: list = []
    errors: list = []

    def _guard(kind, fn, *args):
        try:
            fn(*args)
        except _Unreadable as u:
            errors.append({"kind": kind, "name": u.rel, "error": u.reason})
        except Exception as e:  # any other store failure: reported by type only, never dropped
            errors.append({"kind": kind, "name": ".", "error": type(e).__name__})

    def _gen(kind, name, parent, entry):
        try:
            rows.append(_inventory_row(kind, name, parent, entry))
        except _Unreadable as u:
            errors.append({"kind": kind, "name": u.rel, "error": u.reason})
        except OSError as e:
            errors.append({"kind": kind, "name": name, "error": e.strerror or type(e).__name__})

    def _archives():
        base = _Dir.root(Path(os.environ.get("BD_HOME") or "."))
        if base is None:
            return
        with base:
            for e in base.entries():
                if e.is_file(follow_symlinks=False) and e.name.endswith(_ARCHIVE_TOP):
                    _gen("archive", e.name, base, e)
            sub = base.child("backups", "backups")
            if sub is not None:
                with sub:
                    for e in sub.entries():
                        if e.is_file(follow_symlinks=False) and e.name.endswith(_ARCHIVE_SUB):
                            _gen("archive", f"backups/{e.name}", sub, e)

    def _premigration():
        from . import db as _db
        src = _db._resolve_db_path()
        if not src:
            return
        bak = Path(str(src) + ".premigration.bak")
        parent = _Dir.root(bak.parent)
        if parent is None:
            return
        with parent:
            for e in parent.entries():
                if e.name == bak.name and e.is_file(follow_symlinks=False):
                    _gen("premigration", e.name, parent, e)

    def _template_gold():
        from . import template_backup as _tb
        root = _tb._backups_root()  # <project root>/templates/.gold_backups
        top = _Dir.root(root.parent.parent)
        if top is None:
            return
        with top:
            tpl = top.child(root.parent.name, root.parent.name)
            if tpl is None:
                return
            with tpl:
                gold = tpl.child(root.name, root.name)
                if gold is None:
                    return
                with gold:
                    for host in gold.entries():
                        if host.is_dir(follow_symlinks=False):
                            _guard("template_gold", _gold_host, gold, host)

    def _gold_host(gold, host):
        hd = gold.child(host.name, host.name)
        if hd is None:
            return
        with hd:
            for gen in hd.entries():
                if gen.is_dir(follow_symlinks=False):
                    _gen("template_gold", f"{host.name}/{gen.name}", hd, gen)

    def _profile_sync():
        cwd = _Dir.root(Path("."))
        if cwd is None:
            return
        with cwd:
            prof_root = cwd.child("profiles", "profiles")
            if prof_root is None:
                return
            with prof_root:
                for site in prof_root.entries():
                    if site.is_dir(follow_symlinks=False):
                        _guard("profile_sync", _sync_site, prof_root, site)

    def _sync_site(prof_root, site):
        sd = prof_root.child(site.name, site.name)
        if sd is None:
            return
        with sd:
            for prof in sd.entries():
                if not prof.is_dir(follow_symlinks=False):
                    continue
                rel = f"{site.name}/{prof.name}"
                pd = sd.child(prof.name, rel)
                if pd is None:
                    continue
                with pd:
                    sb = pd.child(".sync_backups", f"{rel}/.sync_backups")
                    if sb is None:
                        continue
                    with sb:
                        for gen in sb.entries():
                            if gen.is_dir(follow_symlinks=False):
                                _gen("profile_sync", f"{rel}/{gen.name}", sb, gen)

    for kind, scan in (("archive", _archives), ("premigration", _premigration),
                       ("template_gold", _template_gold), ("profile_sync", _profile_sync)):
        _guard(kind, scan)
    rows.sort(key=lambda r: r["_mtime"], reverse=True)
    for r in rows:
        del r["_mtime"]
    return {"backups": rows, "errors": errors}


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
        # BK-REST F9: the rehearsal only needs the verdict, not the sandbox.
        if res.get("restored_to"):
            shutil.rmtree(res["restored_to"], ignore_errors=True)
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
