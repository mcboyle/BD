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
import math
import re
import unicodedata
import sqlite3
import subprocess
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


@dataclass
class HeaderHashResult:
    """Result of compute_header_hash(): aHash of the first keyframes decoded
    from a stream prefix. ``ok`` is False when no frame decoded (prefix too
    short, moov atom at EOF, not a video) -- callers must fail OPEN on that."""
    ok: bool
    hash_hex: str = ""        # 16 hex chars, majority-combined 64-bit aHash
    frames: int = 0
    bytes_sampled: int = 0
    elapsed_s: float = 0.0
    error: str = ""


@dataclass
class RejectionRecord:
    """Why a transfer was refused at the header stage."""
    final_path: str
    duplicate_of: str
    distance: int
    hash_hex: str
    frames: int
    bytes_sampled: int
    source_url: str = ""

    def as_dict(self) -> dict:
        return {
            "final_path": self.final_path, "duplicate_of": self.duplicate_of,
            "distance": self.distance, "hash": self.hash_hex, "frames": self.frames,
            "bytes_sampled": self.bytes_sampled, "url": self.source_url,
        }


class HeaderDuplicateRejected(Exception):
    """Raised by the transport when the header-stage index matched. Not an
    _HTTPDownloadFailed: the Playwright fallback must not re-download it."""

    def __init__(self, record: RejectionRecord):
        super().__init__(
            f"header-hash duplicate of {record.duplicate_of} "
            f"(distance {record.distance}, {record.frames} keyframes, "
            f"{record.bytes_sampled} bytes sampled)")
        self.record = record


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


# ─── Header-stage sampling (row 857) ───────────────────────────────

HEADER_SAMPLE_BYTES = 256 * 1024   # stream prefix handed to ffmpeg
HEADER_KEYFRAMES = 3
HEADER_DISTANCE = 4                # Hamming bits; same scale as find_duplicates

_HEADER_FRAME_BYTES = 64           # 8x8 gray, one byte per pixel


def _frame_ahash(gray64: bytes) -> str:
    avg = sum(gray64) / len(gray64)
    bits = "".join("1" if p >= avg else "0" for p in gray64)
    return f"{int(bits, 2):016x}"


def _combine_frame_hashes(hashes: list) -> str:
    """Per-bit majority over the frame hashes. A tie (even frame count)
    resolves to 1 so the result is symmetric in the frames: no frame is
    privileged and none is discarded."""
    n = len(hashes)
    if n == 1:
        return hashes[0]
    vals = [int(h, 16) for h in hashes]
    out = 0
    for i in range(64):
        ones = sum((v >> i) & 1 for v in vals)
        if ones * 2 >= n:
            out |= 1 << i
    return f"{out:016x}"


def compute_header_hash(prefix: bytes, *, max_frames: int = HEADER_KEYFRAMES,
                        timeout_s: float = 20.0) -> HeaderHashResult:
    """aHash the first ``max_frames`` keyframes of a stream prefix.

    ffmpeg reads the prefix on stdin and writes 8x8 grayscale keyframes to
    stdout (no intermediate files). Never raises."""
    t0 = time.time()
    n = len(prefix or b"")
    if n == 0:
        return HeaderHashResult(ok=False, error="empty prefix")
    from . import ffmpeg_bin
    exe = ffmpeg_bin.ffmpeg()
    if not exe:
        return HeaderHashResult(ok=False, bytes_sampled=n, error="ffmpeg unavailable")
    cmd = [exe, "-v", "error", "-i", "pipe:0",
           "-vf", "select=eq(pict_type\\,I),scale=8:8", "-vsync", "vfr",
           "-frames:v", str(int(max_frames)),
           "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=prefix, capture_output=True, timeout=timeout_s)
        raw = proc.stdout or b""
    except Exception as e:
        return HeaderHashResult(ok=False, bytes_sampled=n,
                                elapsed_s=time.time() - t0, error=f"ffmpeg: {e}")
    frames = [raw[i:i + _HEADER_FRAME_BYTES]
              for i in range(0, len(raw) - len(raw) % _HEADER_FRAME_BYTES, _HEADER_FRAME_BYTES)]
    if not frames:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return HeaderHashResult(ok=False, bytes_sampled=n, elapsed_s=time.time() - t0,
                                error=("no keyframe decoded from prefix"
                                       + (f": {err[-1][:120]}" if err else "")))
    combined = _combine_frame_hashes([_frame_ahash(f) for f in frames])
    return HeaderHashResult(ok=True, hash_hex=combined, frames=len(frames),
                            bytes_sampled=n, elapsed_s=time.time() - t0)


def preflight_header_reject(prefix: bytes, *, registry: "HashRegistry",
                            final_path: str, source_url: str = "",
                            distance: int = HEADER_DISTANCE) -> Optional[RejectionRecord]:
    """Decide, from the stream prefix alone, whether ``final_path`` would be
    a perceptual duplicate of something already indexed.

    Returns a RejectionRecord (and logs it) on a match. Returns None -- and
    registers ``final_path`` under the header hash -- when the content is
    new. Also returns None when no keyframe decodes: unknown is never a
    rejection. Never raises."""
    final_path = str(final_path)
    try:
        res = compute_header_hash(prefix)
        if not res.ok:
            log.debug("dedup_header_skip path=%s reason=%s", final_path, res.error)
            return None
        hits = registry.find_header_duplicates(res.hash_hex, distance=distance,
                                               exclude_path=final_path)
        if hits:
            rec = RejectionRecord(final_path=final_path, duplicate_of=hits[0]["path"],
                                  distance=hits[0]["distance"], hash_hex=res.hash_hex,
                                  frames=res.frames, bytes_sampled=res.bytes_sampled,
                                  source_url=source_url)
            log.info("dedup_header_reject path=%s duplicate_of=%s distance=%d hash=%s "
                     "frames=%d bytes_sampled=%d url=%s",
                     rec.final_path, rec.duplicate_of, rec.distance, rec.hash_hex,
                     rec.frames, rec.bytes_sampled, rec.source_url)
            return rec
        registry.add_header(final_path, res.hash_hex, frames=res.frames,
                            source_url=source_url)
        return None
    except Exception as e:
        log.debug("dedup: preflight_header_reject() failed for %s: %s", final_path, e)
        return None


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
CREATE TABLE IF NOT EXISTS header_hashes (
    path TEXT PRIMARY KEY,
    hash_hex TEXT NOT NULL,
    frame_count INTEGER NOT NULL,
    computed_at REAL NOT NULL,
    source_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_header_hashes_hash ON header_hashes(hash_hex);
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

    # ── header_hashes (row 857): keyed by the canonical destination path ──

    def add_header(self, path: str, hash_hex: str, *, frames: int,
                   source_url: str = "") -> bool:
        hash_hex = _canon_hash_hex(hash_hex)
        if not hash_hex or len(hash_hex) != 16:
            return False
        try:
            with self._lock:
                c = self._conn()
                try:
                    c.execute(
                        "INSERT OR REPLACE INTO header_hashes "
                        "(path, hash_hex, frame_count, computed_at, source_url) "
                        "VALUES (?,?,?,?,?)",
                        (str(path), hash_hex, int(frames), time.time(), source_url or ""))
                    c.commit()
                finally:
                    c.close()
            return True
        except Exception as e:
            log.debug("dedup: add_header() failed for %s: %s", path, e)
            return False

    def header_rows(self) -> list:
        try:
            with self._lock:
                c = self._conn()
                try:
                    rows = c.execute(
                        "SELECT path, hash_hex, frame_count, computed_at, source_url "
                        "FROM header_hashes").fetchall()
                finally:
                    c.close()
        except Exception as e:
            log.debug("dedup: header_rows() failed: %s", e)
            return []
        return [{"path": r[0], "hash_hex": r[1], "frame_count": r[2],
                 "computed_at": r[3], "source_url": r[4]} for r in rows]

    def header_stats(self) -> dict:
        return {"count": len(self.header_rows())}

    def find_header_duplicates(self, hash_hex: str, distance: int = HEADER_DISTANCE,
                               exclude_path: str = "") -> list:
        """Indexed destinations within ``distance`` Hamming bits, nearest
        first. Rows whose destination no longer exists on disk are skipped:
        a transfer that never materialised is not something to be a
        duplicate of."""
        if not hash_hex or len(hash_hex) != 16:
            return []
        out: list = []
        for r in self.header_rows():
            if exclude_path and r["path"] == exclude_path:
                continue
            d = hamming_distance(hash_hex, r["hash_hex"])
            if d < 0 or d > distance:
                continue
            if not os.path.exists(r["path"]):
                continue
            r["distance"] = d
            out.append(r)
        out.sort(key=lambda x: (x["distance"], x["path"]))
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


# ─── Row 909: textual similarity indexing (title dedup) ───────────

_TITLE_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
# Labeled episode identity: "season 1", "s01", "episode 2", "ep 2", "e02",
# "part 3", "pt 3", "chapter 4", "vol 5", "episode IV" (roman). Unlabeled
# numbers ("2024", "the great adventure 2") keep an empty label.
_TITLE_MARKER_RE = re.compile(
    r"(?:\b(?P<label>season|series|episode|ep|part|pt|chapter|ch|volume|vol)"
    r"\s*)?\b(?P<num>\d+|[ivxlcdm]+)\b", re.UNICODE)
_TITLE_SXXEYY_RE = re.compile(r"\bs(\d+)\s*e(\d+)\b")
# compact notations: "s02" / "s2" -> season, "e05" / "ep05" -> episode,
# "2x05" -> season 2 episode 5 (a bare "\b\d+\b" never sees them: the
# letter prefix denies the word boundary)
_TITLE_COMPACT_RES = (
    (re.compile(r"\b(\d+)x(\d+)\b"), r"season \1 episode \2"),
    (re.compile(r"\bs(\d+)\b"), r"season \1"),
    (re.compile(r"\be(?:p)?(\d+)\b"), r"episode \1"),
)
_MARKER_LABELS = {
    "season": "season", "series": "season",
    "episode": "episode", "ep": "episode",
    "part": "part", "pt": "part", "chapter": "chapter", "ch": "chapter",
    "volume": "volume", "vol": "volume",
}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
# a WELL-FORMED roman numeral (subtractive forms only where the notation
# allows them): "iv", "xii", "mix" match; "civil", "mm i", "ivx" do not
_ROMAN_WELL_FORMED_RE = re.compile(
    r"^m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$")
# no labeled season/episode/part/chapter/volume is numbered beyond this in
# roman numerals; anything larger is a word made of roman letters
_ROMAN_MAX_MARKER = 200


def _normalize_title(title: str) -> str:
    """NFKC-fold, casefold and collapse punctuation/whitespace to single
    spaces. Unicode letters and digits (CJK, Cyrillic, Arabic, ...) are
    retained: two unrelated Chinese titles must not both collapse to ""."""
    if not isinstance(title, str):
        return ""
    folded = unicodedata.normalize("NFKC", title).casefold()
    return _TITLE_WORD_RE.sub(" ", folded).strip()


def _title_trigrams(text: str) -> frozenset:
    """Padded character 3-grams of `text`; blank text yields no trigrams
    and never matches anything."""
    if not text:
        return frozenset()
    padded = f"  {text}  "
    return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))


def _roman_to_int(text: str) -> int:
    """Value of a well-formed roman numeral, 0 when `text` is not one (a
    natural word spelled in roman letters -- "mix", "civil", "dim" -- is
    never an episode number)."""
    if not text or not _ROMAN_WELL_FORMED_RE.match(text):
        return 0
    total = 0
    prev = 0
    for ch in reversed(text):
        val = _ROMAN[ch]
        total = total - val if val < prev else total + val
        prev = max(prev, val)
    return total


def _episode_markers(text: str) -> tuple:
    """Episode identity of a normalized title: a sorted tuple of
    (label, number) pairs. Labeled markers keep their label ("season 1
    episode 2" != "episode 1 season 2"); roman numerals count only when
    labeled ("episode iv" -> ("episode", 4); a bare "i"/"v" word is not a
    number); bare numbers carry an empty label so "01" and "1" compare
    equal and a sequel's added number still distinguishes it."""
    markers = []
    text = _TITLE_SXXEYY_RE.sub(r"season \1 episode \2", text)
    for compact_re, spelled in _TITLE_COMPACT_RES:
        text = compact_re.sub(spelled, text)
    for m in _TITLE_MARKER_RE.finditer(text):
        label = _MARKER_LABELS.get(m.group("label") or "", "")
        num = m.group("num")
        if num.isdigit():
            value = int(num)
        elif label:
            value = _roman_to_int(num)
            if not 0 < value <= _ROMAN_MAX_MARKER:
                continue
        else:
            continue
        markers.append((label, value))
    return tuple(sorted(markers))


@dataclass(frozen=True)
class SimilarityMatch:
    catalog_id: object
    title: str
    score: float


class TitleSimilarityIndex:
    """In-memory trigram index over catalog titles (Row 909).

    `add(catalog_id, title)` once per catalog record; `find_duplicates(title)`
    returns catalog matches at or above `threshold` (Jaccard similarity over
    character trigrams: |intersection| / |union|), excluding any candidate
    whose episode identity (labeled season/episode/part numbers) differs
    from the query -- those are different episodes, not duplicates.

    Candidate generation is threshold-aware (prefix filtering): for Jaccard
    >= t a match must share at least ceil(t*|Q|) of the query's |Q| trigrams,
    so it must appear in the postings of at least one of the query's
    |Q| - ceil(t*|Q|) + 1 RAREST trigrams; only those postings are read.
    Scoring uses per-title trigram bitmasks over the index vocabulary, so a
    candidate's intersection size is one AND + popcount, and a candidate is
    dropped by the exact bound |A&B| >= t*(|A|+|B|)/(1+t) before any
    division. Neither step depends on episode markers: a catalog of 2000
    titles sharing a long common prefix (all markers equal) still scores
    in well under a millisecond on the author's host.
    """

    def __init__(self) -> None:
        self._catalog: dict = {}        # catalog_id -> (title, mask, markers)
        self._vocab: dict = {}          # trigram -> bit position
        self._rows: list = []           # row -> catalog_id
        self._masks: list = []          # row -> trigram bitmask
        self._sizes: list = []          # row -> trigram count
        self._postings: dict = {}       # trigram -> [row, ...]
        self._row_of: dict = {}         # catalog_id -> live row (None if blank)

    def _retire(self, catalog_id) -> None:
        """Detach the row a catalog_id currently owns (re-add / update): the
        row stays in the parallel lists and postings but can never score --
        otherwise the old title's bitmask would be reported under the new
        title (fixer, correctness item 1)."""
        row = self._row_of.pop(catalog_id, None)
        if row is not None:
            title, _mask, _markers = self._catalog[catalog_id]
            for gram in _title_trigrams(_normalize_title(title)):
                rows = self._postings.get(gram)
                if rows:
                    rows.remove(row)
                    if not rows:
                        del self._postings[gram]
            self._rows[row] = None
            self._masks[row] = 0
            self._sizes[row] = 0

    def add(self, catalog_id, title: str) -> None:
        if catalog_id in self._catalog:
            self._retire(catalog_id)
        norm = _normalize_title(title)
        grams = _title_trigrams(norm)
        if not grams:
            # blank after normalization: recorded, never a match candidate
            self._catalog[catalog_id] = (title, 0, ())
            self._row_of[catalog_id] = None
            return
        row = len(self._rows)
        self._row_of[catalog_id] = row
        mask = 0
        for gram in grams:
            bit = self._vocab.setdefault(gram, len(self._vocab))
            mask |= 1 << bit
            self._postings.setdefault(gram, []).append(row)
        self._catalog[catalog_id] = (title, mask, _episode_markers(norm))
        self._rows.append(catalog_id)
        self._masks.append(mask)
        self._sizes.append(len(grams))

    def _candidates(self, q_grams: frozenset, threshold: float) -> set:
        q_len = len(q_grams)
        min_shared = max(1, math.ceil(threshold * q_len - 1e-9))
        by_rarity = sorted(q_grams, key=lambda g: len(self._postings.get(g, ())))
        rows: set = set()
        for gram in by_rarity[:q_len - min_shared + 1]:
            rows.update(self._postings.get(gram, ()))
        ids = self._rows
        return {row for row in rows if ids[row] is not None}

    def find_duplicates(self, title: str, threshold: float = 0.9) -> list:
        norm = _normalize_title(title)
        q_grams = _title_trigrams(norm)
        if not q_grams:
            return []
        threshold = min(max(float(threshold), 0.0), 1.0)
        q_markers = _episode_markers(norm)
        q_len = len(q_grams)
        q_mask = 0
        for gram in q_grams:
            bit = self._vocab.get(gram)
            if bit is not None:
                q_mask |= 1 << bit
        rows = self._candidates(q_grams, threshold)
        masks, sizes, catalog, ids = self._masks, self._sizes, self._catalog, self._rows
        bit_count = int.bit_count
        shared = [bit_count(q_mask & masks[row]) for row in rows]
        matches = []
        for row, inter in zip(rows, shared):
            size = sizes[row]
            if inter * (1.0 + threshold) < threshold * (q_len + size):
                continue
            cid = ids[row]
            title_, _mask, markers = catalog[cid]
            if markers != q_markers:
                continue
            score = inter / (q_len + size - inter)
            if score >= threshold:
                matches.append(SimilarityMatch(cid, title_, score))
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    def candidate_count(self, title: str, threshold: float = 0.9) -> int:
        """Number of catalog records the prefix filter admits for `title`
        (diagnostic; lets tests prove pruning without timing)."""
        q_grams = _title_trigrams(_normalize_title(title))
        if not q_grams:
            return 0
        return len(self._candidates(q_grams, min(max(float(threshold), 0.0), 1.0)))

    def __len__(self) -> int:
        return len(self._catalog)


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
    "SimilarityMatch",
    "TitleSimilarityIndex",
    "TextualSimilarityIndex",
    "DuplicateRecordReconciler",
    "ReconciliationStrategy",
    "reconcile_text_records",
    "apply_text_reconciliation",
]


# ── Row 1047: Textual Similarity Indexing & Duplicate Record Reconciliation ──
from .text_similarity import (
    DuplicateRecordReconciler,
    ReconciliationPlan,
    ReconciliationStrategy,
    TextualRecord,
    TextualSimilarityIndex,
)


def reconcile_text_records(
    records: list[dict],
    text_key: str = "title",
    id_key: str = "id",
    strategy: ReconciliationStrategy = ReconciliationStrategy.KEEP_FIRST,
    threshold: float = 0.8,
) -> list[ReconciliationPlan]:
    """Reconcile duplicate records based on textual similarity."""
    reconciler = DuplicateRecordReconciler()
    return reconciler.reconcile_records(
        records,
        text_key=text_key,
        id_key=id_key,
        strategy=strategy,
        threshold=threshold,
    )


def apply_text_reconciliation(
    records: list[dict],
    plans: list[ReconciliationPlan],
    id_key: str = "id",
) -> list[dict]:
    """Return records with reconcile_text_records plans applied (duplicates dropped)."""
    return DuplicateRecordReconciler().apply_reconciliation(records, plans, id_key=id_key)
