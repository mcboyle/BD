"""bulk_downloader.interop_registry -- INTEROP-GOV-1 keystone: the provenance +
risk-ack registry every interop track (chromium extensions / JDownloader plugins /
yt-dlp + gallery-dl plugins) loads through.

Governance posture (interop roadmap, decided @601): an OPEN loader with per-item
risk acknowledgment + provenance, **not** an allowlist. BD ships nothing and keeps
no catalog -- it provides the socket and the consent/provenance surface, and the
operator owns the legal/ToS call. Concretely this module:

  * RECORDS provenance for an interop item -- ``source`` (where it came from),
    ``sha256`` (content hash / pin), optional ``commit``.
  * REQUIRES an explicit ``risk_acknowledged`` flag AND an ``enabled`` flag before
    ``is_permitted`` is True. Off by default: a freshly-registered item is neither
    acknowledged nor enabled, and an unregistered item is never permitted.
  * PINS the item: re-registering with a DIFFERENT ``sha256`` resets the ack, so an
    item cannot silently change on disk under an existing acknowledgment.

Disk-backed JSON, stateless (every call reads/writes the file -- nothing cached, so
concurrent workers and a fresh process both see the current state). Every
load-modify-save runs under a thread lock plus an advisory lock on a sibling
``.lock`` file, so concurrent writers do not drop each other's updates. Path:
``<BD_HOME>/interop_registry.json``. Pure stdlib; no network, no Flask. Mirrors
plugins.py's ``allow_full_access`` / ``risk_acknowledged`` model, generalized across
interop kinds.

On-disk shape::

    {"<kind>": {"<item_id>": {"source": str, "sha256": str, "commit": str|null,
                              "risk_acknowledged": bool, "enabled": bool,
                              "registered_ts": int, "updated_ts": int}}}
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

# The interop kinds this keystone governs. New tracks register under a new kind;
# the gate logic (is_permitted) is identical for all.
KINDS = ("chromium_extension", "jd_plugin", "ytdlp_plugin", "gallerydl_plugin")


def _registry_path() -> Path:
    # Resolved from BD_HOME (already a classified deploy/path env var) rather than a
    # dedicated override, so no new BD_-prefixed env var enters the config-surface
    # inventory. Tests isolate by pointing BD_HOME at a tempdir.
    home = os.environ.get("BD_HOME") or "."
    return Path(home).resolve() / "interop_registry.json"


def _load() -> Dict[str, Dict[str, Any]]:
    """Read the whole registry; missing/unreadable/non-dict -> {}. Never raises."""
    p = _registry_path()
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_lock = threading.Lock()


def _lock_file(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return
    # msvcrt locks a byte range from the current position; keep one byte in the
    # lock file so every Windows process contends on the same range.
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_file(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _transaction():
    """Serialize one load-modify-save. ``_lock`` covers threads in this process;
    the sibling ``.lock`` file covers other processes (the registry itself is
    replaced on every save, so its inode cannot carry the lock). If the lock file
    cannot be opened the write runs under ``_lock`` alone -- the same directory
    problem will normally make ``_save`` fail and report it."""
    p = _registry_path()
    with _lock:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fh = open(p.with_name(p.name + ".lock"), "a+b")
        except OSError:
            fh = None
        if fh is None:
            yield
            return
        with fh:
            _lock_file(fh)
            try:
                yield
            finally:
                _unlock_file(fh)


def _save(reg: Dict[str, Dict[str, Any]]) -> bool:
    """Atomically write the registry (unique temp + os.replace). Returns False on
    OSError, leaving no temp file behind."""
    p = _registry_path()
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(reg, indent=2, sort_keys=True))
        os.replace(tmp, p)
        return True
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def register(kind: str, item_id: str, *, source: str = "",
             sha256: str = "", commit: str = "") -> Dict[str, Any]:
    """Record (or update) provenance for ``item_id``. A NEW item is off by default
    (risk_acknowledged=False, enabled=False). Re-registering an EXISTING item
    updates source/commit and, if the ``sha256`` differs from the stored one,
    RESETS risk_acknowledged (the pin property -- a changed item must be re-acked).
    Returns the stored record; raises OSError if it could not be persisted."""
    with _transaction():
        reg = _load()
        bucket = reg.setdefault(kind, {})
        now = int(time.time())
        prev = bucket.get(item_id)
        if prev is None:
            rec = {"source": source, "sha256": sha256, "commit": commit or None,
                   "risk_acknowledged": False, "enabled": False,
                   "registered_ts": now, "updated_ts": now}
        else:
            rec = dict(prev)
            rec["source"] = source or rec.get("source", "")
            rec["commit"] = (commit or None) if commit else rec.get("commit")
            # Pin: a provenance change (new content hash) drops the acknowledgment.
            if sha256 and sha256 != rec.get("sha256", ""):
                rec["risk_acknowledged"] = False
            if sha256:
                rec["sha256"] = sha256
            rec["updated_ts"] = now
        bucket[item_id] = rec
        if not _save(reg):
            raise OSError(f"could not persist interop registry {_registry_path()}")
    return rec


def acknowledge(kind: str, item_id: str) -> bool:
    """Set risk_acknowledged=True for a REGISTERED item. Returns False (no-op) if
    the item was never registered -- ack can't conjure a permitted phantom -- or
    if the change could not be persisted."""
    with _transaction():
        reg = _load()
        rec = reg.get(kind, {}).get(item_id)
        if rec is None:
            return False
        rec["risk_acknowledged"] = True
        rec["updated_ts"] = int(time.time())
        return _save(reg)


def set_enabled(kind: str, item_id: str, enabled: bool) -> bool:
    """Set the enabled flag for a REGISTERED item. Returns False if unregistered
    or if the change could not be persisted."""
    with _transaction():
        reg = _load()
        rec = reg.get(kind, {}).get(item_id)
        if rec is None:
            return False
        rec["enabled"] = bool(enabled)
        rec["updated_ts"] = int(time.time())
        return _save(reg)


def is_permitted(kind: str, item_id: str, live_sha256: Optional[str] = None) -> bool:
    """True iff the item is registered AND risk_acknowledged AND enabled -- the
    single gate every interop track consults before loading an item.

    When ``live_sha256`` is provided, ALSO require it to equal the registered
    provenance hash: an item that changed on disk since it was acknowledged is
    blocked until it is re-registered (which resets the ack) and re-acknowledged.
    This makes the pin airtight at load time, not just at register time."""
    rec = _load().get(kind, {}).get(item_id)
    if not (rec and rec.get("risk_acknowledged") and rec.get("enabled")):
        return False
    if live_sha256 is not None and rec.get("sha256", "") != live_sha256:
        return False
    return True


def get(kind: str, item_id: str) -> Optional[Dict[str, Any]]:
    rec = _load().get(kind, {}).get(item_id)
    return dict(rec) if rec is not None else None


def list_all() -> List[Tuple[str, str, Dict[str, Any]]]:
    """``[(kind, item_id, record), ...]`` across all kinds, sorted."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    reg = _load()
    for kind in sorted(reg):
        bucket = reg[kind]
        if isinstance(bucket, dict):
            for item_id in sorted(bucket):
                out.append((kind, item_id, dict(bucket[item_id])))
    return out


def dir_sha256(path: str | os.PathLike) -> str:
    """Stable, content-sensitive SHA-256 of a directory tree: hash of each file's
    relative path + bytes, in sorted order. Used to pin an unpacked extension so a
    silent on-disk change drops its acknowledgment. A missing dir hashes empty."""
    h = hashlib.sha256()
    base = Path(path)
    if base.is_dir():
        for f in sorted(base.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(base)).encode("utf-8"))
                h.update(b"\0")
                try:
                    h.update(f.read_bytes())
                except OSError:
                    h.update(b"<unreadable>")
                h.update(b"\0")
    return h.hexdigest()
