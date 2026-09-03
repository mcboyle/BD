"""RAM-disk staging for downloads (Phase 98, Block L).

The dev box has ~700 GB of unused RAM that could be used as a staging
buffer for in-flight downloads. The win is real: SSDs at 500 MB/s sustained
write are still 10-20× slower than RAM, and parallel downloads to a single
SSD compound the slowdown via write amplification + queue depth contention.

Strategy:
  1. On Windows, use a configured RAM-disk drive letter (user creates it
     with ImDisk / OSFMount / Primo / etc — BD doesn't try to provision)
  2. Workers write the .part file to RAM-disk
  3. On success, atomically move to the final destination disk
  4. On failure (incl. crash), the .part is lost — same as a corrupt
     .part on disk, no worse

Constraints:
  • RAM-disk is finite (typical 16-64 GB allocation). Refuse to stage
    files larger than configured `ramdisk_max_file_gb`
  • Concurrent files can't exceed `ramdisk_capacity_gb` total — when
    full, fall back to direct-to-disk (this module returns None, caller
    uses normal path)
  • Capacity accounting is process-local, but path ownership is a durable
    filesystem claim so a second process cannot reuse a live staging file

Operator config (BD site config):
  • ramdisk_path: '/mnt/ramdisk' or 'R:\\' (empty = disabled)
  • ramdisk_capacity_gb: 32         (advisory; we don't enforce OS-level)
  • ramdisk_max_file_gb: 8          (per-file ceiling)

Fail-open: every call must work even when ramdisk is misconfigured.
Returning None means "use the normal disk path."
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple


# In-memory capacity table: {staging_path: (bytes_reserved, ts)}. Protected by
# _lock and deliberately advisory. Exclusive path ownership is the adjacent
# staging_claim sidecar, which survives a second process or process restart.
_reservations: dict = {}
_lock = threading.Lock()


def is_enabled(config: dict) -> bool:
    """True when a non-empty `ramdisk_path` is configured AND the
    path exists and is writable. Caches nothing — checked on each
    call so the operator can toggle without restart."""
    path = (config or {}).get("ramdisk_path", "")
    if not path or not isinstance(path, str):
        return False
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return False
    return os.access(str(p), os.W_OK)


def capacity_gb(config: dict) -> float:
    """Configured advisory capacity. Falls back to 16 GB if missing."""
    try:
        return float((config or {}).get("ramdisk_capacity_gb", 16.0))
    except (TypeError, ValueError):
        return 16.0


def max_file_gb(config: dict) -> float:
    """Per-file ceiling. Files larger than this skip RAM-disk staging."""
    try:
        return float((config or {}).get("ramdisk_max_file_gb", 8.0))
    except (TypeError, ValueError):
        return 8.0


def _reserved_total_bytes() -> int:
    """Sum of currently-reserved bytes across all in-flight files."""
    with _lock:
        return sum(v[0] for v in _reservations.values())


def _ramdisk_free_bytes(path: str) -> Optional[int]:
    """Actual free bytes on the RAM-disk device. None on error."""
    try:
        return shutil.disk_usage(path).free
    except (OSError, ValueError):
        return None


def reserve_staging_path(
    final_path: str,
    expected_size: int,
    config: dict,
    identity: str | None = None,
    claim_reserver=None,
) -> Optional[str]:
    """Reserve a staging path on RAM-disk for an in-flight download.

    Returns a durably claimed staging path string on success, or None if the
    RAM-disk isn't enabled / file is too big / not enough room. Unmeasurable
    ownership raises ``staging_claim.StagingUnavailable`` rather than returning
    an unclaimed path. Caller releases both this module's capacity slot and the
    filesystem claim after promote()/cleanup().

    `expected_size` is the size we plan to download in bytes. If 0
    or negative (unknown), we reserve `max_file_gb` worth and let
    the caller fail down the line if it overruns. That's defensive
    but acceptable — the OS will refuse the write before damage.
    """
    if not is_enabled(config):
        return None
    cap_bytes = int(capacity_gb(config) * (1024**3))
    max_bytes = int(max_file_gb(config) * (1024**3))
    if expected_size > max_bytes:
        return None  # too big for RAM-disk; use normal disk
    reserve_bytes = expected_size if expected_size > 0 else max_bytes
    # Check both reservation budget AND actual OS-level free space.
    # Either being short means we fail-open to direct-to-disk.
    ramdisk = config["ramdisk_path"]
    with _lock:
        current_reserved = sum(v[0] for v in _reservations.values())
        if current_reserved + reserve_bytes > cap_bytes:
            return None
    os_free = _ramdisk_free_bytes(ramdisk)
    if os_free is not None and os_free < reserve_bytes:
        return None
    final = Path(final_path)
    # Use a flat namespace at <ramdisk>/staging/<basename>.part so
    # we don't recreate the source dir hierarchy in RAM (waste).
    # Collisions resolved with a numeric suffix.
    stage_dir = Path(ramdisk) / "staging"
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"[ramdisk] mkdir failed: {e}\n")
        return None
    # The RAM-disk namespace is shared by every site and download directory,
    # and process-local bookkeeping cannot reserve it across workers or a
    # restart. Publish the same durable filesystem claim used by the ordinary
    # transport path. ``reserve`` keeps the historical flat basename while
    # atomically diverting a different owner to a suffixed candidate.
    # The transport owns staging-claim policy and injects its atomic reserve
    # operation. Without that authority this helper must fail back to disk;
    # returning an unclaimed RAM path would make ownership UNKNOWN.
    if not identity or claim_reserver is None:
        return None
    _candidate_final, candidate = claim_reserver(
        stage_dir / final.name, identity)
    with _lock:
        _reservations[str(candidate)] = (reserve_bytes, time.time())
    return str(candidate)


def promote(staging_path: str, final_path: str) -> Tuple[bool, str]:
    """Atomically move the completed download from RAM-disk to its
    final destination. Always releases the reservation, even on
    failure (caller can't leak a slot).

    Returns (ok, message). On failure, leaves the staging file in
    place so the operator can inspect it.
    """
    try:
        stage = Path(staging_path)
        final = Path(final_path)
        if not stage.exists():
            release(staging_path)
            return (False, f"staging path missing: {stage}")
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return (False, f"mkdir failed: {e}")
        # shutil.move handles cross-device automatically (RAM-disk is
        # almost always a different device than the destination SSD).
        # It does copy + delete under the hood; the *destination* write
        # is the slow part, RAM-disk read is fast.
        try:
            shutil.move(str(stage), str(final))
        except (OSError, shutil.Error) as e:
            return (False, f"move failed: {e}")
        release(staging_path)
        return (True, str(final))
    except Exception as e:
        release(staging_path)
        return (False, f"promote raised: {e}")


def release(staging_path: str):
    """Free a reservation slot. Safe to call multiple times — idempotent.
    Does NOT delete the staging file (cleanup() does that). Splitting
    release from cleanup lets the operator inspect a failed staging
    file before BD wipes it."""
    with _lock:
        _reservations.pop(staging_path, None)


def cleanup(staging_path: str) -> bool:
    """Delete an abandoned staging file (failed download, crash recovery).
    Returns True on success. Always releases the reservation."""
    release(staging_path)
    try:
        p = Path(staging_path)
        if p.exists():
            p.unlink()
        return True
    except OSError as e:
        sys.stderr.write(f"[ramdisk] cleanup failed for {staging_path}: {e}\n")
        return False


def status_dict(config: dict) -> dict:
    """Surface in /api/status. Tells the operator whether RAM-disk
    staging is on and how full it currently is."""
    if not is_enabled(config):
        return {"enabled": False, "configured_path": (config or {}).get("ramdisk_path", "")}
    reserved = _reserved_total_bytes()
    cap = int(capacity_gb(config) * (1024**3))
    free_os = _ramdisk_free_bytes(config["ramdisk_path"])
    return {
        "enabled": True,
        "path": config["ramdisk_path"],
        "capacity_gb": capacity_gb(config),
        "reserved_gb": round(reserved / (1024**3), 2),
        "reserved_pct": round(100 * reserved / cap, 1) if cap else 0,
        "free_os_gb": round(free_os / (1024**3), 2) if free_os is not None else None,
        "in_flight": len(_reservations),
        "max_file_gb": max_file_gb(config),
    }


def stale_cleanup(max_age_seconds: int = 86400) -> int:
    """Sweep reservations older than `max_age_seconds`. A reservation
    older than a day means a worker died without releasing — orphaned
    entry. Returns number of slots reclaimed. Call from a periodic
    janitor task or on BD restart."""
    now = time.time()
    stale = []
    with _lock:
        for path, (_size, ts) in _reservations.items():
            if (now - ts) > max_age_seconds:
                stale.append(path)
    for path in stale:
        cleanup(path)
    return len(stale)
