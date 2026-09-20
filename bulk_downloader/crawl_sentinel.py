"""Pure frontier guard for recursive crawls.

The sentinel keeps only enough local state to reject duplicate queue entries.
Each URL is otherwise judged from its own path, so a rejected branch cannot
poison unrelated navigation branches.
"""
from __future__ import annotations

import math
import posixpath
from collections import Counter
from urllib.parse import urlsplit, urlunsplit


class CrawlSentinel:
    """Bound crawl frontier growth by depth, path cycles, and duplicates."""

    def __init__(self, *, max_depth: int = 8, max_segment_repeats: int = 2,
                 min_path_entropy: float = 0.6, entropy_min_depth: int = 4):
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        if max_segment_repeats < 1:
            raise ValueError("max_segment_repeats must be positive")
        if not 0 <= min_path_entropy <= 1:
            raise ValueError("min_path_entropy is a ratio in [0, 1]")
        self.max_depth = max_depth
        self.max_segment_repeats = max_segment_repeats
        # path entropy: Shannon entropy of the segment distribution as a
        # ratio of the maximum for that depth (1.0 = every segment distinct).
        # A deep path built from few distinct segments (a/b/c/a/c/b/a ...)
        # is a generated cycle even when no single segment exceeds
        # max_segment_repeats and no adjacent block repeats; judged only from
        # entropy_min_depth segments on (short paths are legitimately low).
        self.min_path_entropy = min_path_entropy
        self.entropy_min_depth = entropy_min_depth
        self._seen: set[str] = set()

    def admit(self, url: str) -> bool:
        """Record and admit a safe URL, returning ``False`` for a pruned one."""
        canonical, parts = _canonical_path(url)
        if not canonical or canonical in self._seen:
            return False
        if len(parts) > self.max_depth:
            return False
        if any(parts.count(part) > self.max_segment_repeats for part in parts):
            return False
        if _has_repeated_branch(parts):
            return False
        if len(parts) >= self.entropy_min_depth and path_entropy_ratio(parts) < self.min_path_entropy:
            return False
        self._seen.add(canonical)
        return True

    def prune_queue(self, urls: list[str]) -> list[str]:
        """Return queue entries admitted by this sentinel, in input order."""
        return [url for url in urls if self.admit(url)]


def _canonical_path(url: str) -> tuple[str, tuple[str, ...]]:
    parsed = urlsplit(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "", ()
    # dot-segments and empty segments are resolved BEFORE dedup and depth:
    # /library/scene/../scene/42 and /library//scene/./42 are /library/scene/42
    path = "/" + posixpath.normpath("/" + parsed.path).lstrip("/") if parsed.path else "/"
    if parsed.path.endswith("/") and path != "/":
        path += "/"
    parts = tuple(part for part in path.split("/") if part)
    canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))
    return canonical, parts


def path_entropy(parts: tuple[str, ...]) -> float:
    """Shannon entropy, in bits, of the path's segment distribution: 0.0 for
    one repeated segment, log2(n) for n distinct segments."""
    if not parts:
        return 0.0
    total = len(parts)
    return -sum((c / total) * math.log2(c / total) for c in Counter(parts).values())


def path_entropy_ratio(parts: tuple[str, ...]) -> float:
    """``path_entropy`` over its maximum for this depth (log2(len)); 1.0 when
    every segment is distinct, 0.0 when one segment repeats; 1.0 for a
    path of fewer than two segments (nothing to be repetitive about)."""
    if len(parts) < 2:
        return 1.0
    return path_entropy(parts) / math.log2(len(parts))


def _has_repeated_branch(parts: tuple[str, ...]) -> bool:
    """Detect adjacent repeated path sequences such as ``a/b/a/b``."""
    for start in range(len(parts)):
        for width in range(1, (len(parts) - start) // 2 + 1):
            if parts[start:start + width] == parts[start + width:start + 2 * width]:
                return True
    return False
