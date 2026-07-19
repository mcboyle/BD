"""Secrets-store audit log — NEW-8.

Opt-in, redacted audit trail for credential-store operations. OFF by
default. When off, ``secrets_store.get_backend()`` returns the raw
backend and this module is never touched on the credential path
(byte-identical behaviour, near-zero overhead).

Configuration (all environment, read fresh per event so tests and
operators can toggle without restarting):

  BD_SECRETS_AUDIT
      off / unset (default)        -> auditing disabled
      1 | on | true | yes | mutations  -> log ``set`` and ``delete``
      all | reads                  -> additionally log ``get`` (reads)

      The friendly, recommended setting is mutations-only: ``set`` /
      ``delete`` are the high-signal, low-volume events. ``get`` fires on
      essentially every login fill (resolve_password -> backend.get), so
      logging reads is mostly expected noise and is opt-in on top.

  BD_SECRETS_AUDIT_SINK
      file (default)  -> a dedicated JSONL file (one event per line)
      log             -> route through the existing app logger
                         ("bulk_downloader.secrets_audit"), which already
                         has a RotatingFileHandler via log.py

  BD_SECRETS_AUDIT_FILE
      Override the file-sink path. Default: ``secrets_audit.jsonl``.
      A relative path resolves against the current working directory,
      which is BD_HOME under the chdir-only isolation pattern — the same
      convention ``secrets.json`` uses, so the audit log travels beside
      the vault it describes.

  BD_SECRETS_AUDIT_MAX_BYTES
      File-sink rotation threshold in bytes (default 1 MiB). On reaching
      it, the file is rotated to ``<name>.1`` (single generation). Set to
      0 to disable rotation.

Redaction contract: only the credential key NAME, the action, the
outcome, the backend name, and a UTC timestamp are recorded. The secret
value is never passed into this module and never written.

Fail-open contract: any error while auditing is swallowed. A logging
failure must never block or alter a get/set/delete.

Import-cleanliness: importing this module does no I/O and starts no
threads — it only defines functions and stdlib-level constants.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

_DEFAULT_FILE = "secrets_audit.jsonl"
_DEFAULT_MAX_BYTES = 1_048_576  # 1 MiB

# Accepted truthy values for the friendly mutations-only mode.
_MUTATIONS_VALUES = frozenset({"1", "on", "true", "yes", "mutations", "mutate"})
# Accepted values that additionally enable read (get) auditing.
_ALL_VALUES = frozenset({"all", "reads", "read", "verbose"})

# Serialises concurrent writers (the runner is multi-threaded). Process-
# local; the file-sink is append-only so cross-process interleave is at
# worst line-ordering, never corruption.
_write_lock = threading.Lock()


def _store_get(key):
    """v3.66.315 (CLI->GUI parity): store value for ``key`` or None when unset."""
    try:
        from bulk_downloader import global_config as _gc
        v = _gc.get(key, None)
        return v if v not in (None, "") else None
    except Exception:
        return None


def _raw_mode() -> str:
    sv = _store_get("secrets_audit")
    if sv is not None:
        return str(sv).strip().lower()
    return (os.environ.get("BD_SECRETS_AUDIT") or "").strip().lower()


def mode() -> str:
    """Return the active mode: ``"off"``, ``"mutations"``, or ``"all"``."""
    v = _raw_mode()
    if v in _ALL_VALUES:
        return "all"
    if v in _MUTATIONS_VALUES:
        return "mutations"
    return "off"


def is_enabled() -> bool:
    """True iff auditing is on in any mode."""
    return mode() != "off"


def _should_log(action: str) -> bool:
    m = mode()
    if m == "off":
        return False
    if action == "get":
        return m == "all"
    # set / delete (and any future mutation) log in every enabled mode
    return True


def _sink() -> str:
    sv = _store_get("secrets_audit_sink")
    if sv is not None:
        return str(sv).strip().lower()
    return (os.environ.get("BD_SECRETS_AUDIT_SINK") or "file").strip().lower()


def _file_path() -> Path:
    sv = _store_get("secrets_audit_file")
    if sv is not None:
        return Path(str(sv))
    return Path(os.environ.get("BD_SECRETS_AUDIT_FILE") or _DEFAULT_FILE)


def _max_bytes() -> int:
    sv = _store_get("secrets_audit_max_bytes")
    if sv is not None:
        try:
            return int(sv)
        except (TypeError, ValueError):
            pass
    try:
        return int(os.environ.get("BD_SECRETS_AUDIT_MAX_BYTES") or _DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES


def _rotate_if_needed(path: Path, max_bytes: int) -> None:
    """Single-generation size rotation. Fail-open: a rotation problem
    must not stop the write that follows."""
    if max_bytes <= 0:
        return
    try:
        if path.exists() and path.stat().st_size >= max_bytes:
            bak = path.with_name(path.name + ".1")
            try:
                if bak.exists():
                    bak.unlink()
            except OSError:
                pass
            path.replace(bak)
    except OSError:
        pass


def _write_file(record: dict) -> None:
    path = _file_path()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _write_lock:
        _rotate_if_needed(path, _max_bytes())
        parent = path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


def _write_log(record: dict) -> None:
    import logging
    logging.getLogger("bulk_downloader.secrets_audit").info(
        "secrets_audit %s",
        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
    )


def audit(action: str, key, *, ok=None, backend_name: str | None = None) -> None:
    """Record one credential-store operation. Redacted and fail-open.

    ``action`` is ``"get"`` | ``"set"`` | ``"delete"``. ``key`` is the
    credential key NAME — never the secret value. ``ok`` is the outcome
    (True/False) or None when unknown. Never raises.
    """
    try:
        if not _should_log(action):
            return
        record = {
            "t": round(time.time(), 3),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": action,
            "key": str(key),
            "backend": backend_name,
        }
        if ok is not None:
            record["ok"] = bool(ok)
        if _sink() == "log":
            _write_log(record)
        else:
            _write_file(record)
    except Exception:
        # Fail-open: auditing must never break a credential operation.
        try:
            sys.stderr.write("  secrets_audit: write failed (ignored)\n")
        except Exception:
            pass
