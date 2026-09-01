"""v3.43.72: perceptual video dedup.

# What this module is

Catches duplicate content across BD's library when filenames and file
sizes differ. The same scene can ship across two Aylo brands at
different filenames; the same scene Matt has at 1080p H.264 might
also be available at 4K HEVC for replacement; networks re-publish
existing content with their own watermark/intro.

Traditional filename-or-size dedup (which the existing Stash
integration does) misses all three. Perceptual hashing — computing
a fingerprint from the video's actual frames — catches them.

# Approach

A perceptual video hash (pHash) is a fixed-size hex string computed
from N evenly-spaced frames of the video. Two videos with similar
visual content produce similar hashes; "similar" is measured by
Hamming distance (bit-difference count) between the hashes.

Typical thresholds:
  - 0 bits different    — identical re-encode at same resolution
  - 1-3 bits different  — same scene, different encoding params
  - 4-8 bits different  — same scene, different watermarks/intros
  - 9+ bits             — likely different content

We use the `videohash` Python library which wraps ffmpeg + PIL +
imagehash to produce a 64-bit pHash. The library handles frame
extraction, downscaling, normalization, and the actual perceptual
hashing.

# Module layout

  - `is_available()`    — True if videohash + ffmpeg are both ready
  - `compute_hash(path)` — slow (10-30s per file). Returns HashResult.
  - `HashRegistry`      — SQLite-backed registry. `add()`, `find_duplicates()`,
                          `scan_folder()` (one-shot, with progress callback).
  - `hamming_distance(a, b)` — count of differing bits between two hex
                          strings of equal length.

# SQLite schema

    CREATE TABLE IF NOT EXISTS video_hashes (
        path TEXT PRIMARY KEY,         -- canonical absolute path
        hash_hex TEXT NOT NULL,        -- 64-bit videohash as 16 hex chars
        duration_sec REAL,             -- best-effort, may be 0
        file_size_bytes INTEGER,       -- for tie-break in policies
        computed_at REAL NOT NULL,     -- wall time of hashing
        ffprobe_codec TEXT,            -- e.g. "h264", "hevc"
        notes TEXT                     -- e.g. "scan_v3.43.72", "queue_done"
    )
    CREATE INDEX IF NOT EXISTS idx_video_hashes_hash ON video_hashes(hash_hex)

# Policy

When `find_duplicates` returns >1 file for a hash group, the caller
applies a policy:

  - `keep_all`     — flag but keep all files (default; safe)
  - `keep_largest` — keep the one with the largest file size
  - `keep_first`   — keep the earliest computed_at

We never auto-delete from this module. Deletion is opt-in via the
review UI; the module surfaces candidates only.

# Fail-open

Every operation is wrapped in try/except. Missing videohash →
`is_available()` returns False, all functions become no-ops. Missing
ffmpeg → `compute_hash` returns HashResult(ok=False). Corrupt file →
same. The dispatcher in runner.py calls these in a way that NEVER
blocks a download path on hash success or failure.

# What about the 2,875-URL queue?

`scan_folder(root_dir, progress_cb)` walks a folder recursively,
hashes each new file (skipping ones already in the registry), reports
progress. Designed to run on a background thread; can be cancelled
via a cancel_check callback.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger(__name__)


# ─── Availability ──────────────────────────────────────────────────


def is_videohash_available() -> bool:
    """True if the `videohash` Python library imports cleanly."""
    try:
        import videohash  # noqa: F401
        return True
    except ImportError:
        return False


def is_ffmpeg_available() -> bool:
    """True if `ffmpeg` is on PATH. videohash invokes ffmpeg
    internally, so without it the library will fail."""
    from . import ffmpeg_bin          # MOD-4: one resolver, honours the pin
    return ffmpeg_bin.available()


def is_available() -> bool:
    """Both prerequisites for actually computing hashes."""
    return is_videohash_available() and is_ffmpeg_available()


# ─── Result types ──────────────────────────────────────────────────


@dataclass
class HashResult:
    """Result of compute_hash()."""
    ok: bool
    path: str = ""
    hash_hex: str = ""        # 16 hex chars for 64-bit pHash
    duration_sec: Optional[float] = 0.0
    file_size_bytes: int = 0
    codec: Optional[str] = ""
    elapsed_s: float = 0.0
    error: str = ""           # short reason if not ok
    metadata_error: str = ""  # ffprobe failure when the hash itself succeeded


@dataclass
class DuplicateGroup:
    """One group of files whose hashes are within `distance` Hamming
    bits of the seed file. The seed is at index 0; the rest are
    duplicates of the seed."""
    files: list = field(default_factory=list)  # list of (path, hash_hex, size, computed_at)
    distance: int = 0       # Hamming distance threshold used


# ─── Hamming distance ──────────────────────────────────────────────


def hamming_distance(a: str, b: str) -> int:
    """Bit-difference count between two hex hashes of equal length.

    Returns the Hamming distance (number of differing bits). If lengths
    don't match or input is bad, returns -1 (caller must handle).
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return -1
    if len(a) != len(b) or not a:
        return -1
    try:
        ai = int(a, 16)
        bi = int(b, 16)
    except ValueError:
        return -1
    return bin(ai ^ bi).count("1")


# ─── Hash computation ──────────────────────────────────────────────


def _canon_hash_hex(raw) -> str:
    """videohash returns various types depending on version. Normalize
    to a lowercase 16-char hex string."""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    # Newer versions prefix with "0x" or include trailing whitespace
    if s.startswith("0x"):
        s = s[2:]
    # Strip non-hex chars (videohash sometimes returns a string with
    # quotes or other formatting)
    s = re.sub(r"[^0-9a-f]", "", s)
    if len(s) > 16:
        s = s[:16]
    elif len(s) < 16:
        # Pad with leading zeros to canonical length
        s = s.zfill(16)
    return s


def _ffprobe_meta(path: str) -> dict:
    """Best-effort duration + codec lookup via the configured ffprobe.

    A successful-but-empty measurement is ``0.0``/``""``.  A probe that did
    not run or failed is ``None``/``None`` with a named error, so callers never
    store an unavailable measurement as a measured-looking zero.
    """
    from . import ffmpeg_bin
    ffprobe = ffmpeg_bin.ffprobe()
    if not ffprobe:
        return {"duration_sec": None, "codec": None,
                "error": "ffprobe_unavailable"}
    try:
        import subprocess
        result = subprocess.run(
            [ffprobe, "-v", "error",
             "-show_entries", "stream=codec_name:format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             path],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[:120]
            suffix = f":{detail}" if detail else ""
            return {"duration_sec": None, "codec": None,
                    "error": f"ffprobe_rc_{result.returncode}{suffix}"}
        out = {"duration_sec": 0.0, "codec": "", "error": ""}
        # ffprobe output is two lines: codec_name, duration
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if len(lines) >= 1:
            out["codec"] = lines[0]
        if len(lines) >= 2:
            try:
                out["duration_sec"] = float(lines[1])
            except ValueError:
                return {"duration_sec": None, "codec": None,
                        "error": "ffprobe_invalid_duration"}
        return out
    except subprocess.TimeoutExpired:
        return {"duration_sec": None, "codec": None,
                "error": "ffprobe_timeout"}
    except (OSError, UnicodeDecodeError) as e:
        log.debug("dedup: ffprobe failed for %s: %s", path[-50:], e)
        return {"duration_sec": None, "codec": None,
                "error": (f"ffprobe_exec_failed:{type(e).__name__}:"
                          f"{str(e)[:120]}")}


def compute_hash(path: str, *,
                 timeout_s: float = 60.0) -> HashResult:
    """Compute a perceptual video hash for `path`.

    Blocks for 10-60 seconds depending on file size + storage speed.
    Caller should run on a background thread.

    Returns HashResult(ok=False) on any failure — never raises.
    """
    if not path or not isinstance(path, str):
        return HashResult(ok=False, error="empty_path")
    if not os.path.exists(path):
        return HashResult(ok=False, path=path, error="file_not_found")
    if not is_videohash_available():
        return HashResult(ok=False, path=path, error="videohash_not_installed")
    if not is_ffmpeg_available():
        return HashResult(ok=False, path=path, error="ffmpeg_not_on_path")
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return HashResult(ok=False, path=path, error=f"stat_failed:{e}")
    if size < 1024:
        return HashResult(ok=False, path=path,
                          error="file_too_small_for_hashing")
    start = time.monotonic()
    try:
        import videohash
        # videohash.VideoHash(path=...) does all the work in __init__
        vh = videohash.VideoHash(path=path)
        raw_hash = getattr(vh, "hash_hex", None) or getattr(vh, "hash", None)
        # Some versions provide .hash as a hex string, others as an int.
        hash_hex = _canon_hash_hex(raw_hash)
        if not hash_hex or hash_hex == "0" * 16:
            return HashResult(ok=False, path=path,
                              error="empty_or_invalid_hash",
                              elapsed_s=time.monotonic() - start)
    except Exception as e:
        # Cover every failure mode videohash might raise: ffmpeg
        # parse errors, PIL decoding errors, OSError on the temp
        # directory, etc.
        return HashResult(
            ok=False, path=path,
            error=f"{type(e).__name__}:{str(e)[:120]}",
            elapsed_s=time.monotonic() - start,
        )
    # Pull duration + codec via ffprobe — cheap relative to hashing
    meta = _ffprobe_meta(path)
    return HashResult(
        ok=True, path=path, hash_hex=hash_hex,
        duration_sec=meta["duration_sec"],
        file_size_bytes=size,
        codec=meta["codec"],
        metadata_error=meta["error"],
        elapsed_s=time.monotonic() - start,
    )


# ─── Registry ──────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_hashes (
    path TEXT PRIMARY KEY,
    hash_hex TEXT NOT NULL,
    duration_sec REAL,
    file_size_bytes INTEGER,
    computed_at REAL NOT NULL,
    ffprobe_codec TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_hashes_hash ON video_hashes(hash_hex);
"""


class HashRegistry:
    """SQLite-backed registry of computed hashes. Thread-safe.

    Usage:
        reg = HashRegistry("video_hashes.db")
        reg.add(hash_result, notes="queue_done")
        reg.find_duplicates(hash_hex, distance=4)
        reg.scan_folder(root, progress_cb=lambda done, total: ...)
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        """Return a new connection. Each call site gets its own to
        avoid thread-affinity issues with sqlite3."""
        c = sqlite3.connect(self.db_path, timeout=10.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _init_schema(self) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()

    def add(self, result: HashResult, notes: str = "") -> bool:
        """Insert or replace a hash result. Returns True on success."""
        if not result.ok or not result.hash_hex:
            return False
        try:
            with self._lock:
                c = self._conn()
                try:
                    c.execute(
                        """INSERT OR REPLACE INTO video_hashes
                           (path, hash_hex, duration_sec, file_size_bytes,
                            computed_at, ffprobe_codec, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (result.path, result.hash_hex,
                         result.duration_sec, result.file_size_bytes,
                         time.time(), result.codec, notes or ""),
                    )
                    c.commit()
                    return True
                finally:
                    c.close()
        except Exception as e:
            log.debug("dedup: add() failed: %s", e)
            return False

    def lookup(self, path: str) -> Optional[dict]:
        """Get the stored record for a path, if any."""
        try:
            with self._lock:
                c = self._conn()
                try:
                    row = c.execute(
                        "SELECT path, hash_hex, duration_sec, "
                        "file_size_bytes, computed_at, ffprobe_codec, notes "
                        "FROM video_hashes WHERE path = ?",
                        (path,),
                    ).fetchone()
                finally:
                    c.close()
            if row is None:
                return None
            return {
                "path": row[0], "hash_hex": row[1],
                "duration_sec": row[2], "file_size_bytes": row[3],
                "computed_at": row[4], "ffprobe_codec": row[5],
                "notes": row[6],
            }
        except Exception as e:
            log.debug("dedup: lookup() failed: %s", e)
            return None

    def known_paths(self) -> set:
        """Return the set of paths already in the registry."""
        try:
            with self._lock:
                c = self._conn()
                try:
                    rows = c.execute(
                        "SELECT path FROM video_hashes").fetchall()
                finally:
                    c.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def find_duplicates(
        self, hash_hex: str, distance: int = 4,
        exclude_path: str = "",
    ) -> list:
        """Find files whose hash is within `distance` Hamming bits of
        `hash_hex`. Returns a list of dicts (one per match) sorted by
        ascending distance, then descending file_size_bytes (so the
        "best" match for a keep-largest policy comes first).

        `exclude_path` if set is filtered out of the results (used when
        we're looking up the dups of a JUST-hashed file and don't want
        to report it as a dup of itself).
        """
        if not hash_hex or len(hash_hex) != 16:
            return []
        try:
            with self._lock:
                c = self._conn()
                try:
                    rows = c.execute(
                        "SELECT path, hash_hex, duration_sec, "
                        "file_size_bytes, computed_at, ffprobe_codec "
                        "FROM video_hashes"
                    ).fetchall()
                finally:
                    c.close()
        except Exception as e:
            log.debug("dedup: find_duplicates() failed: %s", e)
            return []
        out: list = []
        for r in rows:
            other_path, other_hash = r[0], r[1]
            if exclude_path and other_path == exclude_path:
                continue
            d = hamming_distance(hash_hex, other_hash)
            if d < 0 or d > distance:
                continue
            out.append({
                "path": other_path,
                "hash_hex": other_hash,
                "distance": d,
                "duration_sec": r[2],
                "file_size_bytes": r[3] or 0,
                "computed_at": r[4],
                "ffprobe_codec": r[5],
            })
        out.sort(key=lambda x: (x["distance"], -x["file_size_bytes"]))
        return out

    def stats(self) -> dict:
        """Return counts for the dashboard widget."""
        try:
            with self._lock:
                c = self._conn()
                try:
                    total = c.execute(
                        "SELECT COUNT(*) FROM video_hashes").fetchone()[0]
                    last_ts = c.execute(
                        "SELECT MAX(computed_at) FROM video_hashes"
                    ).fetchone()[0] or 0.0
                finally:
                    c.close()
            return {"total": total, "last_computed_at": last_ts}
        except Exception:
            return {"total": 0, "last_computed_at": 0.0}

    def remove(self, path: str) -> bool:
        """Remove a single path from the registry (e.g. when file is
        deleted on disk)."""
        try:
            with self._lock:
                c = self._conn()
                try:
                    c.execute(
                        "DELETE FROM video_hashes WHERE path = ?",
                        (path,),
                    )
                    c.commit()
                    return True
                finally:
                    c.close()
        except Exception:
            return False

    def scan_folder(
        self,
        root_dir: str,
        *,
        extensions: tuple = (".mp4", ".mkv", ".webm", ".mov", ".m4v"),
        recursive: bool = True,
        progress_cb: Optional[Callable] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        skip_known: bool = True,
    ) -> dict:
        """Walk `root_dir`, hash every video file, record results.

        `progress_cb(done, total, current_path)`: called after each file.
        `cancel_check()`: returns True to abort the scan early.

        Returns a summary dict: {scanned, hashed, skipped, failed, elapsed_s}.

        Run on a background thread — this is a long-running operation.
        """
        start = time.monotonic()
        summary = {
            "scanned": 0, "hashed": 0, "skipped": 0,
            "failed": 0, "elapsed_s": 0.0, "cancelled": False,
        }
        if not os.path.isdir(root_dir):
            summary["error"] = "not_a_directory"
            return summary

        # Collect candidate files
        candidates: list = []
        for dirpath, _, filenames in os.walk(root_dir):
            for fn in filenames:
                if not any(fn.lower().endswith(e) for e in extensions):
                    continue
                candidates.append(os.path.join(dirpath, fn))
            if not recursive:
                break

        total = len(candidates)
        known = self.known_paths() if skip_known else set()
        for i, path in enumerate(candidates):
            if cancel_check and cancel_check():
                summary["cancelled"] = True
                break
            summary["scanned"] += 1
            if path in known:
                summary["skipped"] += 1
                if progress_cb:
                    try: progress_cb(i + 1, total, path)
                    except Exception: pass
                continue
            res = compute_hash(path)
            if res.ok:
                if self.add(res, notes="scan_folder"):
                    summary["hashed"] += 1
                else:
                    summary["failed"] += 1
            else:
                summary["failed"] += 1
            if progress_cb:
                try: progress_cb(i + 1, total, path)
                except Exception: pass

        summary["elapsed_s"] = time.monotonic() - start
        return summary


# ─── Module-level helpers ──────────────────────────────────────────


_default_registry: Optional[HashRegistry] = None
_default_registry_lock = threading.Lock()


def get_default_registry(db_path: str = "video_hashes.db") -> HashRegistry:
    """Get the module-level singleton registry. Path is fixed at the
    first call; subsequent calls return the same instance regardless
    of the path arg."""
    global _default_registry
    if _default_registry is None:
        with _default_registry_lock:
            if _default_registry is None:
                _default_registry = HashRegistry(db_path)
    return _default_registry


def apply_policy(group: list, policy: str = "keep_all") -> dict:
    """Apply a dedup policy to a duplicate group.

    `group` is a list of duplicate dicts (as returned by
    find_duplicates) plus the seed file (caller must include it).

    Returns {'keep': [...], 'remove_candidates': [...]} — never
    auto-removes, just classifies.

    Policies:
      - keep_all      — keep everything, classify nothing as removable
      - keep_largest  — keep the largest file_size_bytes
      - keep_first    — keep the earliest computed_at
    """
    if not group:
        return {"keep": [], "remove_candidates": []}
    if policy == "keep_all" or len(group) <= 1:
        return {"keep": list(group), "remove_candidates": []}
    if policy == "keep_largest":
        winner = max(group, key=lambda x: x.get("file_size_bytes", 0))
    elif policy == "keep_first":
        winner = min(group, key=lambda x: x.get("computed_at", float("inf")))
    else:
        # Unknown policy → fail-safe (keep_all)
        return {"keep": list(group), "remove_candidates": []}
    keep = [winner]
    remove = [x for x in group if x is not winner]
    return {"keep": keep, "remove_candidates": remove}


__all__ = [
    "is_videohash_available",
    "is_ffmpeg_available",
    "is_available",
    "HashResult",
    "DuplicateGroup",
    "compute_hash",
    "hamming_distance",
    "HashRegistry",
    "get_default_registry",
    "apply_policy",
]
