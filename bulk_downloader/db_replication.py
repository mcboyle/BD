"""bulk_downloader.db_replication -- Cut 622 / C5: continuous SQLite replication.

Ships a durable, continuously-updated replica of the SQLite stores (queue.db and
friends) via Litestream, so a disk failure loses at most the un-shipped WAL tail
(near-zero RPO) instead of everything since the last one-click ``backup.py`` zip.
This strengthens the A0 gold-backup the automation program gates L2 autonomy on:
the DBs that hold reviewed-state / runtime-enablement now survive the volume.

Design / charter constraints
----------------------------
- **Default-OFF.** ``replication.enabled`` defaults ``False``. Every entry point
  no-ops or fails closed until the operator turns it on AND the ``litestream``
  binary is present. Nothing here spawns a process or writes a replica by default.
- **Fail-closed.** ``start_replication`` / ``restore_store`` return
  ``{"ok": False, "reason": ...}`` (never raise, never silently "succeed") when
  disabled or when the binary is absent.
- **Binary is un-sandbox-verifiable** (like yt-dlp / gallery-dl). The pieces here
  that DON'T need it -- config generation, store enumeration, status, the
  lifecycle guards, and the restore-then-verify plumbing -- are fully unit-tested
  in-sandbox; live WAL shipping is validated on-stash with the real binary.
- **WAL prerequisite already met:** ``db.db_conn`` runs ``PRAGMA journal_mode=WAL``,
  which is exactly what Litestream monitors.

Config lives under the ``"replication"`` key of ``app_config.json`` (read here
directly; no settings-center field is added, so the editable-field count pins are
untouched and the HTTP route surface stays byte-identical). A UI/route surface can
be layered on in a small follow-cut.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import signal
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

# Candidate stores in priority order. ``replication_stores`` filters this to the
# ones that actually exist on disk, so an install that uses only some of them
# replicates only what it has. ``queue.db`` is the hot path (job queue + WAL);
# ``downloader_history.db`` is ``constants.DB_PATH``.
REPLICABLE_STORES = (
    "queue.db",
    "downloader_history.db",
    "video_hashes.db",
    "bulk_downloader.db",
)

_CONFIG_BASENAME = "litestream.yml"
_PIDFILE_BASENAME = ".litestream.pid"
_LOCKFILE_BASENAME = ".litestream.lifecycle.lock"
_PID_SCHEMA = "bd-litestream-pid/1"


# ── paths / config ──────────────────────────────────────────────────────

def _install_root() -> str:
    """Project root == the dir containing the ``bulk_downloader`` package.

    Mirrors the idiom in ``healthcheck._check_disk`` so replication resolves the
    same base the rest of the app treats as the install root.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _base(base_dir: str | os.PathLike | None) -> str:
    return str(base_dir) if base_dir else _install_root()


def _load_repl_cfg(base_dir: str | os.PathLike | None = None) -> dict:
    """Return the effective replication config with defaults applied.

    Reads the ``"replication"`` block of ``<base>/app_config.json`` if present.
    Defaults are charter-safe: disabled, replica dir under the base, no explicit
    store override (=> enumerate the existing candidates).
    """
    base = _base(base_dir)
    raw: dict = {}
    cfg_path = os.path.join(base, "app_config.json")
    try:
        with open(cfg_path, "r") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("replication"), dict):
            raw = doc["replication"]
    except (OSError, ValueError):
        raw = {}
    replica_root = raw.get("replica_root") or os.path.join(base, "replicas")
    stores = raw.get("stores")
    if not isinstance(stores, (list, tuple)) or not stores:
        stores = None
    return {
        "enabled": bool(raw.get("enabled", False)),
        "replica_root": str(replica_root),
        "stores": list(stores) if stores else None,
    }


def replication_stores(base_dir: str | os.PathLike | None = None) -> list[Path]:
    """Resolved absolute paths of the SQLite stores to replicate.

    If the config names an explicit ``stores`` list, those basenames are used;
    otherwise the default candidate set. In both cases only stores that exist on
    disk under ``base`` are returned (Litestream errors on a missing db path).
    """
    base = _base(base_dir)
    cfg = _load_repl_cfg(base_dir)
    names = cfg["stores"] or list(REPLICABLE_STORES)
    out: list[Path] = []
    for name in names:
        # names are basenames by contract; guard against traversal in config.
        safe = os.path.basename(str(name))
        p = Path(base) / safe
        if p.exists():
            out.append(p.resolve())
    return out


# ── binary availability ─────────────────────────────────────────────────

def litestream_available() -> bool:
    """True iff a ``litestream`` executable is on PATH."""
    return shutil.which("litestream") is not None


# ── config generation (pure) ────────────────────────────────────────────

def render_litestream_config(stores, replica_root: str) -> str:
    """Render a deterministic Litestream YAML config.

    Each store gets one file-type replica under ``replica_root`` named for the
    store's basename. Hand-rolled (no YAML dependency) but valid Litestream:

        dbs:
          - path: /abs/queue.db
            replicas:
              - type: file
                path: /abs/replicas/queue.db
    """
    root = str(replica_root)
    lines = ["dbs:"]
    for s in stores:
        sp = Path(s)
        db_path = str(sp)
        rep_path = os.path.join(root, sp.name)
        lines.append(f"  - path: {db_path}")
        lines.append("    replicas:")
        lines.append("      - type: file")
        lines.append(f"        path: {rep_path}")
    return "\n".join(lines) + "\n"


def write_litestream_config(base_dir: str | os.PathLike | None = None) -> dict:
    """Materialise the config file for the current stores. Returns
    ``{ok, path, dbs}``. Creating the file is harmless when disabled -- it is
    only *consumed* by ``start_replication`` (which is itself gated)."""
    base = _base(base_dir)
    cfg = _load_repl_cfg(base_dir)
    stores = replication_stores(base_dir)
    text = render_litestream_config(stores, cfg["replica_root"])
    path = os.path.join(base, _CONFIG_BASENAME)
    try:
        with open(path, "w") as fh:
            fh.write(text)
    except OSError as e:
        return {"ok": False, "reason": f"cannot write config: {e}", "dbs": 0}
    return {"ok": True, "path": path, "dbs": len(stores)}


# ── status (never raises) ───────────────────────────────────────────────

def _pid_path(base_dir: str | os.PathLike | None = None) -> str:
    return os.path.join(_base(base_dir), _PIDFILE_BASENAME)


def _proc_start(pid: int) -> str | None:
    """Return a Linux process's start tick (field 22), when available."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            line = fh.read()
    except OSError:
        return None
    tail = line.rsplit(")", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    return fields[19] if len(fields) > 19 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid_record(base_dir: str | os.PathLike | None = None) -> dict | None:
    try:
        with open(_pid_path(base_dir), "r", encoding="utf-8") as fh:
            row = json.load(fh)
        if (not isinstance(row, dict) or row.get("schema") != _PID_SCHEMA
                or not isinstance(row.get("pid"), int) or row["pid"] <= 0
                or not isinstance(row.get("start"), str) or not row["start"]):
            return None
        return {"pid": row["pid"], "start": row["start"]}
    except (OSError, ValueError, TypeError):
        return None


def _write_pid_record(base_dir, pid: int, start: str) -> None:
    """Atomically publish the PID together with its non-reusable identity."""
    base = _base(base_dir)
    fd, tmp = tempfile.mkstemp(prefix=".litestream.pid.", suffix=".tmp", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": _PID_SCHEMA, "pid": pid, "start": start}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _pid_path(base_dir))
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _lock_lifecycle_file(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_lifecycle_file(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _lifecycle_lock(base_dir: str | os.PathLike | None = None):
    """Serialize sidecar start and stop transactions across processes."""
    lock_path = Path(_base(base_dir)) / _LOCKFILE_BASENAME
    with lock_path.open("a+b") as lock_file:
        if hasattr(os, "fchmod"):
            os.fchmod(lock_file.fileno(), 0o600)
        _lock_lifecycle_file(lock_file)
        try:
            yield
        finally:
            _unlock_lifecycle_file(lock_file)


def _run_lifecycle_locked(base_dir, verb: str, operation):
    """Run one lifecycle transaction and keep lock failures fail-closed."""
    try:
        with _lifecycle_lock(base_dir):
            return operation()
    except OSError as e:
        return {"ok": False, "reason": f"cannot acquire {verb} lifecycle lock: {e}"}


def _legacy_pid(base_dir: str | os.PathLike | None = None) -> int | None:
    try:
        with open(_pid_path(base_dir), "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def _remove_pidfile(base_dir) -> None:
    try:
        os.remove(_pid_path(base_dir))
    except OSError:
        pass


def _running_pid(base_dir: str | os.PathLike | None = None) -> int | None:
    """Return only a live PID whose start identity matches our record."""
    row = _read_pid_record(base_dir)
    if row is None or not _pid_alive(row["pid"]):
        return None
    return row["pid"] if _proc_start(row["pid"]) == row["start"] else None


def replication_status(base_dir: str | os.PathLike | None = None) -> dict:
    """A durability-signal snapshot. Never raises -- safe to call from health /
    diagnostics on any box, binary present or not."""
    cfg = _load_repl_cfg(base_dir)
    try:
        stores = [p.name for p in replication_stores(base_dir)]
    except Exception:
        stores = []
    return {
        "enabled": cfg["enabled"],
        "binary_present": litestream_available(),
        "configured_stores": stores,
        "replica_root": cfg["replica_root"],
        "running": _running_pid(base_dir) is not None,
    }


# ── lifecycle (fail-closed) ─────────────────────────────────────────────

def start_replication(base_dir: str | os.PathLike | None = None) -> dict:
    """Spawn ``litestream replicate`` for the configured stores. Fails closed:
    returns ``{ok: False, reason}`` when disabled, when the binary is absent, or
    when there is nothing to replicate. Only when all preconditions hold does it
    write the config and launch the detached sidecar."""
    cfg = _load_repl_cfg(base_dir)
    if not cfg["enabled"]:
        return {"ok": False, "reason": "replication disabled in config"}
    if not litestream_available():
        return {"ok": False, "reason": "litestream binary not found on PATH"}
    stores = replication_stores(base_dir)
    if not stores:
        return {"ok": False, "reason": "no replicable SQLite stores found"}
    proc = None

    def _start_locked():
        nonlocal proc
        row = _read_pid_record(base_dir)
        if row is not None and _pid_alive(row["pid"]):
            actual_start = _proc_start(row["pid"])
            if actual_start == row["start"]:
                return {"ok": False,
                        "reason": f"replication already running as pid {row['pid']}"}
            if actual_start is None:
                return {"ok": False,
                        "reason": "existing process identity cannot be verified"}
            # A different start tick proves this PID is a foreign reused one.
            _remove_pidfile(base_dir)
        elif row is not None:
            _remove_pidfile(base_dir)
        elif os.path.exists(_pid_path(base_dir)):
            # A live legacy numeric record may represent an older Litestream
            # process. A malformed record is equally unknowable. Neither is
            # authority to start a potentially untracked duplicate.
            legacy = _legacy_pid(base_dir)
            if legacy is None or _pid_alive(legacy):
                return {"ok": False,
                        "reason": "existing pidfile has no verifiable process identity"}
            _remove_pidfile(base_dir)

        wc = write_litestream_config(base_dir)
        if not wc["ok"]:
            return {"ok": False,
                    "reason": wc.get("reason", "config write failed")}
        try:
            os.makedirs(cfg["replica_root"], exist_ok=True)
            proc = subprocess.Popen(
                ["litestream", "replicate", "-config", wc["path"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            start = _proc_start(proc.pid)
            if start is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                return {"ok": False,
                        "reason": "launched process identity is unavailable"}
            _write_pid_record(base_dir, proc.pid, start)
            return {"ok": True, "pid": proc.pid, "dbs": len(stores),
                    "config": wc["path"]}
        except OSError as e:
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            return {"ok": False, "reason": f"failed to launch litestream: {e}"}

    return _run_lifecycle_locked(base_dir, "start", _start_locked)


def _open_pidfd(pid: int) -> int:
    opener = getattr(os, "pidfd_open", None)
    if opener is None:
        raise OSError("pidfd_open is unavailable; refusing numeric-PID signal")
    return opener(pid)


def _send_pidfd_term(fd: int) -> None:
    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:
        raise OSError("pidfd_send_signal is unavailable; refusing numeric-PID signal")
    sender(fd, signal.SIGTERM)


def _close_pidfd(fd: int) -> None:
    os.close(fd)


def _terminate_owned(row: dict) -> bool:
    """Signal the recorded process without a PID-reuse window."""
    try:
        fd = _open_pidfd(row["pid"])
    except ProcessLookupError:
        return False
    try:
        if _proc_start(row["pid"]) != row["start"]:
            return False
        _send_pidfd_term(fd)
        return True
    finally:
        _close_pidfd(fd)


def stop_replication(base_dir: str | os.PathLike | None = None) -> dict:
    """Terminate the running sidecar (if any) and clear the pidfile. Idempotent;
    returns ``{ok, stopped}``."""
    def _stop_locked():
        row = _read_pid_record(base_dir)
        if row is None:
            # Numeric legacy and malformed records carry no authority to signal
            # OR to declare stopped. Preserve the evidence so a later start
            # also refuses instead of creating an untracked duplicate.
            if os.path.exists(_pid_path(base_dir)):
                return {"ok": False,
                        "reason": "pidfile has no verifiable process identity"}
            return {"ok": True, "stopped": False}
        try:
            stopped = _terminate_owned(row)
        except OSError as e:
            return {"ok": False,
                    "reason": f"failed to stop pid {row['pid']}: {e}"}
        _remove_pidfile(base_dir)
        return {"ok": True, "stopped": stopped}

    return _run_lifecycle_locked(base_dir, "stop", _stop_locked)


# ── restore + verify (fail-closed) ──────────────────────────────────────

def restore_store(db_name: str, dest: str | os.PathLike,
                  base_dir: str | os.PathLike | None = None) -> dict:
    """Reconstruct ``db_name`` from its file replica into ``dest`` via
    ``litestream restore``, then verify the restored file with
    ``backup_verify.verify_db_dump``. Fails closed if the binary is absent.

    Returns ``{ok, reason?, verified?, dest?}``.
    """
    if not litestream_available():
        return {"ok": False, "reason": "litestream binary not found on PATH"}
    cfg = _load_repl_cfg(base_dir)
    safe = os.path.basename(str(db_name))
    replica_path = os.path.join(cfg["replica_root"], safe)
    dest = str(dest)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        r = subprocess.run(
            ["litestream", "restore", "-o", dest,
             "-replica-path", replica_path, safe],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
        )
        if r.returncode != 0:
            return {"ok": False,
                    "reason": (r.stderr.decode("utf-8", "replace")[:200]
                               or f"litestream restore exit {r.returncode}")}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "reason": f"restore failed: {e}"}
    # Verify the restored file is a healthy SQLite db (reuse backup_verify).
    verified = False
    try:
        from . import backup_verify as _bv
        v = _bv.verify_db_dump(dest)
        verified = bool(v.get("ok"))
    except Exception:
        verified = False
    return {"ok": True, "dest": dest, "verified": verified}
