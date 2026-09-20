"""bulk_downloader/audio_fingerprint.py - Acoustic audio fingerprinting for cross-resolution dedup.

Generates 32-bit Chromaprint perceptual acoustic hashes invariant to video re-encoding,
resolution scaling (720p vs 1080p), and visual watermarks.
Provides non-blocking failure recovery when fpcalc is missing and in-memory caching
for sub-10ms duplicate comparison latency.
"""
from __future__ import annotations

import json
import math
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from array import array
from dataclasses import dataclass, field

from bulk_downloader import ffmpeg_bin

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioFingerprint:
    """Represents a Chromaprint 32-bit acoustic fingerprint vector."""
    path: str
    duration: float
    fingerprint: tuple[int, ...]
    computed_at: float
    raw_string: str | None = None
    algorithm: str = "chromaprint"
    file_identity: tuple[int, int, int] | None = None   # (size, mtime_ns, inode) the vector was computed from

    packed: int = field(init=False, repr=False, compare=False, default=0)

    def __post_init__(self) -> None:
        # Guarantee all vector elements are normalized 32-bit unsigned integers --
        # for a tuple input too (a tuple holding -1 is not normalized by being a tuple).
        normalized = tuple(int(x) & 0xFFFFFFFF for x in self.fingerprint)
        if normalized != self.fingerprint:
            object.__setattr__(self, "fingerprint", normalized)
        # one big int, element i at bits [32i, 32i+32): a comparison at any
        # offset is one shift + one XOR + one popcount instead of a Python
        # loop over the vector (acceptance 3 at 60-minute scale)
        object.__setattr__(self, "packed", _pack(self.fingerprint))


@dataclass(frozen=True)
class MatchResult:
    """Outcome of comparing two acoustic fingerprint vectors."""
    is_match: bool
    similarity: float
    hamming_distance: int
    evaluated_elements: int
    offset: int
    latency_ms: float


@dataclass(frozen=True)
class DuplicateMatch:
    """Candidate duplicate file identified by acoustic fingerprint."""
    path: str
    similarity: float
    duration_diff: float


def _file_identity(path: str) -> tuple[int, int, int] | None:
    """What a cached vector is bound to: the file's size, mtime and inode. A
    replaced file at the same path has a different identity, so the cache is
    never trusted across a rewrite."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def get_fpcalc_path() -> str:
    """Path or binary name for Chromaprint's fpcalc executable."""
    return os.environ.get("FPCALC_PATH", "fpcalc")


def is_fpcalc_available() -> bool:
    """True if fpcalc binary is found on PATH or via FPCALC_PATH."""
    binary = get_fpcalc_path()
    return shutil.which(binary) is not None


def is_ffmpeg_available() -> bool:
    """True if the pinned ffmpeg resolves (ffmpeg_bin owns WHICH ffmpeg the
    app uses -- MOD-4; nothing here probes PATH on its own). fpcalc decodes
    audio itself, so this is an informational probe, not a prerequisite."""
    return ffmpeg_bin.ffmpeg() is not None


def _norm_path(path: str | os.PathLike) -> str:
    """Normalize file path to absolute path."""
    return os.path.abspath(str(path))


def is_available() -> bool:
    """Convenience alias for whether acoustic fingerprinting is operable."""
    return is_fpcalc_available()


def hamming_distance_32(a: int, b: int) -> int:
    """Return bit difference count between two 32-bit integers."""
    return ((a & 0xFFFFFFFF) ^ (b & 0xFFFFFFFF)).bit_count()


def _pack(vector: Sequence[int]) -> int:
    """Element i of `vector` at bits [32i, 32i+32) of one int."""
    if not vector:
        return 0
    if _ARRAY_U32_LE:
        return int.from_bytes(array("I", (int(x) & 0xFFFFFFFF for x in vector)).tobytes(), "little")
    packed = 0
    for i, x in enumerate(vector):
        packed |= (int(x) & 0xFFFFFFFF) << (32 * i)
    return packed


_ARRAY_U32_LE = array("I").itemsize == 4 and sys.byteorder == "little"


def compare_fingerprints(
    fp1: AudioFingerprint | Sequence[int],
    fp2: AudioFingerprint | Sequence[int],
    threshold: float = 0.85,
    max_offset: int = 5,
    min_overlap_fraction: float = 0.5,
) -> MatchResult:
    """Compare two 32-bit Chromaprint fingerprint vectors across small temporal offsets.

    Returns a MatchResult indicating match status, similarity (0.0 - 1.0),
    Hamming distance, and evaluation latency in milliseconds.
    """
    t_start = time.perf_counter()

    if isinstance(fp1, AudioFingerprint):
        len1, p1 = len(fp1.fingerprint), fp1.packed
    else:
        len1, p1 = len(fp1), _pack(fp1)
    if isinstance(fp2, AudioFingerprint):
        len2, p2 = len(fp2.fingerprint), fp2.packed
    else:
        len2, p2 = len(fp2), _pack(fp2)

    if len1 == 0 or len2 == 0:
        elapsed = (time.perf_counter() - t_start) * 1000.0
        return MatchResult(
            is_match=False,
            similarity=0.0,
            hamming_distance=0,
            evaluated_elements=0,
            offset=0,
            latency_ms=elapsed,
        )

    # Search across temporal alignment offsets [-max_offset, +max_offset]. An
    # alignment only counts when it overlaps MEANINGFULLY: at least
    # min_overlap_fraction of the shorter vector (and never fewer than 2
    # elements) -- a single shared element must not make two complementary
    # vectors an exact duplicate.
    min_overlap = max(2, int(math.ceil(min(len1, len2) * min_overlap_fraction)))
    min_overlap = min(min_overlap, len1, len2)
    best: tuple[float, int, int, int] | None = None   # (similarity, distance, evaluated, offset)

    offsets = range(-max_offset, max_offset + 1) if max_offset > 0 else (0,)

    for offset in offsets:
        if offset >= 0:
            start1 = offset
            start2 = 0
        else:
            start1 = 0
            start2 = -offset

        overlap = min(len1 - start1, len2 - start2)
        if overlap < min_overlap:
            continue

        total_bits = overlap * 32
        window = (1 << total_bits) - 1
        total_distance = (((p1 >> (32 * start1)) ^ (p2 >> (32 * start2))) & window).bit_count()

        sim = 1.0 - (total_distance / total_bits)
        # The first EVALUATED alignment seeds the best; later ones must beat it.
        if best is None or sim > best[0]:
            best = (sim, total_distance, overlap, offset)

    elapsed = (time.perf_counter() - t_start) * 1000.0
    if best is None:
        return MatchResult(
            is_match=False, similarity=0.0, hamming_distance=0, evaluated_elements=0,
            offset=0, latency_ms=round(elapsed, 4),
        )
    best_similarity, best_distance, best_evaluated, best_offset = best
    return MatchResult(
        is_match=(best_similarity >= threshold),
        similarity=round(best_similarity, 4),
        hamming_distance=best_distance,
        evaluated_elements=best_evaluated,
        offset=best_offset,
        latency_ms=round(elapsed, 4),
    )


class AudioFingerprintCache:
    """Thread-safe in-memory cache for audio fingerprints and comparison lookups."""

    def __init__(self, max_entries: int = 10000) -> None:
        if max_entries < 1:
            # an empty bound would make put() evict from an empty dict
            raise ValueError(f"max_entries must be >= 1, got {max_entries!r}")
        self._lock = threading.Lock()
        self._cache: dict[str, AudioFingerprint] = {}
        self._max_entries = max_entries

    def get(self, path: str) -> AudioFingerprint | None:
        """Retrieve cached fingerprint by path."""
        norm_path = _norm_path(path)
        with self._lock:
            return self._cache.get(norm_path)

    def put(self, fp: AudioFingerprint) -> None:
        """Store fingerprint in cache."""
        norm_path = _norm_path(fp.path)
        with self._lock:
            if norm_path in self._cache:
                # overwrite in place: no new entry, nothing to evict
                self._cache[norm_path] = fp
                return
            if len(self._cache) >= self._max_entries:
                # Evict oldest entry
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[norm_path] = fp

    def remove(self, path: str) -> None:
        """Remove entry from cache."""
        norm_path = _norm_path(path)
        with self._lock:
            self._cache.pop(norm_path, None)

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Number of cached entries."""
        with self._lock:
            return len(self._cache)

    def compare(
        self, path1: str, path2: str, threshold: float = 0.85
    ) -> MatchResult | None:
        """Compare two cached fingerprints; returns None if either is not cached."""
        fp1 = self.get(path1)
        fp2 = self.get(path2)
        if fp1 is None or fp2 is None:
            return None
        return compare_fingerprints(fp1, fp2, threshold=threshold)


# Module-level default cache
_DEFAULT_CACHE = AudioFingerprintCache()


def compute_audio_fingerprint(
    path: str | os.PathLike,
    timeout: float = 30.0,
    use_cache: bool = True,
) -> AudioFingerprint | None:
    """Compute 32-bit Chromaprint audio fingerprint vector from a media file.

    Fail-open: returns None on missing fpcalc, unreadable file, or command error.
    """
    path_str = str(path)
    if not os.path.isfile(path_str):
        log.debug("Acoustic fingerprint: file does not exist %s", path_str)
        return None

    identity = _file_identity(path_str)
    if use_cache:
        cached = _DEFAULT_CACHE.get(path_str)
        if cached is not None and cached.file_identity == identity:
            return cached
        # a different file now lives at this path (replaced/rewritten): recompute

    if not is_fpcalc_available():
        log.debug("Acoustic fingerprint skipped: fpcalc not found")
        return None

    cmd = [get_fpcalc_path(), "-raw", "-json", path_str]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.warning("fpcalc invocation failed on %s: %s", path_str, err)
        return None

    if proc.returncode != 0:
        log.warning(
            "fpcalc exited rc=%d on %s: %s",
            proc.returncode,
            path_str,
            proc.stderr.strip() if proc.stderr else "unknown error",
        )
        return None

    try:
        data = json.loads(proc.stdout)
        if not isinstance(data, dict):
            raise ValueError(f"fpcalc output is not a JSON object: {type(data).__name__}")
        duration = float(data.get("duration", 0.0))
        raw_fp = data.get("fingerprint", [])
        if not isinstance(raw_fp, (list, tuple)):
            raise ValueError("fpcalc 'fingerprint' is not a list")
        vector = tuple(int(x) & 0xFFFFFFFF for x in raw_fp)
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as err:
        log.warning("Failed parsing fpcalc output for %s: %s", path_str, err)
        return None

    fp = AudioFingerprint(
        path=_norm_path(path_str),
        duration=duration,
        fingerprint=vector,
        computed_at=time.time(),
        raw_string=proc.stdout.strip(),
        file_identity=identity,
    )

    if use_cache:
        _DEFAULT_CACHE.put(fp)

    return fp


class AcousticDedupMatcher:
    """Registry for indexing and finding cross-resolution duplicate media files."""

    def __init__(self, cache: AudioFingerprintCache | None = None) -> None:
        self._cache = cache or AudioFingerprintCache()
        self._index: dict[str, AudioFingerprint] = {}
        self._lock = threading.Lock()

    def index_fingerprint(self, fp: AudioFingerprint) -> None:
        """Register a known audio fingerprint in the matcher index."""
        with self._lock:
            # keyed like the cache (normalised path): a relative and an
            # absolute spelling of one file are one record, never each
            # other's duplicate
            self._index[_norm_path(fp.path)] = fp
            self._cache.put(fp)

    def index_file(self, path: str) -> AudioFingerprint | None:
        """Compute and register fingerprint for a media file."""
        fp = compute_audio_fingerprint(path)
        if fp is not None:
            self.index_fingerprint(fp)
        return fp

    def find_duplicates(
        self,
        query: AudioFingerprint | str,
        threshold: float = 0.85,
    ) -> list[DuplicateMatch]:
        """Find duplicate media entries matching query fingerprint."""
        if isinstance(query, str):
            # same rule as compute_audio_fingerprint: a cached vector is only
            # trusted while the file at that path is the one it was computed
            # from (size, mtime_ns, inode); a rewritten file is recomputed.
            cached = self._cache.get(query)
            if cached is not None and cached.file_identity == _file_identity(query):
                fp = cached
            else:
                fp = compute_audio_fingerprint(query)
            if fp is None:
                return []
        else:
            fp = query

        matches: list[DuplicateMatch] = []
        with self._lock:
            candidates = list(self._index.values())

        for cand in candidates:
            if _norm_path(cand.path) == _norm_path(fp.path):
                continue
            res = compare_fingerprints(fp, cand, threshold=threshold)
            if res.is_match:
                matches.append(
                    DuplicateMatch(
                        path=cand.path,
                        similarity=res.similarity,
                        duration_diff=round(abs(fp.duration - cand.duration), 2),
                    )
                )

        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches

    def clear(self) -> None:
        """Clear indexed fingerprints."""
        with self._lock:
            self._index.clear()
            self._cache.clear()
