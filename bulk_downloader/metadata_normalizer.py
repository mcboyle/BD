"""row907: canonical record attribution and metadata normalization.

# What this is

Upstream sources spell the same publisher/series/author inconsistently
("O'Reilly Media" vs "OReilly" vs "O'REILLY MEDIA INC"), which fragments
storage directory structures and makes search miss records that are really
the same entity. This module maps a raw attribution string to one canonical,
filesystem-safe path component:

  1. Exact match (case/punctuation/whitespace-folded) against a caller-
     supplied alias table -- the reliable path, used whenever the variant is
     already known.
  2. Bounded Levenshtein-distance fuzzy match against the same table, for
     small typos/OCR noise in an otherwise-known name.
  3. Otherwise, a plain filesystem-safe normalization of the input itself --
     never invented, never merged with an unrelated name.

# What this is NOT

Not a fuzzy-matching search index: the fuzzy fallback only ever proposes an
alias table's OWN canonical values, at a small bounded edit distance, never a
match against other raw inputs seen elsewhere. Two genuinely different names
with no alias table entry stay genuinely different.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

_WHITESPACE_RE = re.compile(r"\s+")
_FS_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_DOTS_SPACES_RE = re.compile(r"[.\s]+$")
_LEADING_THE_RE = re.compile(r"^(the)\s+", re.IGNORECASE)
_PUNCT_STRIP_RE = re.compile(r"[.,;:!'\"()\[\]{}]")

_MAX_COMPONENT_LEN = 200
_DEFAULT_FUZZY_MAX_DISTANCE = 2


def _fold(name: str) -> str:
    """Normalize `name` for comparison / lookup only: NFKC, casefold,
    collapse whitespace, drop a leading "The", strip common punctuation.
    Never returned to a caller -- only used as a dict key / distance input."""
    n = unicodedata.normalize("NFKC", name).strip()
    # Punctuation stripped BEFORE the leading-"The" check: "The, Beatles!"
    # must fold the same as "The Beatles" -- if "The" were checked first,
    # a comma directly after it ("The,") would block the \s+ match and this
    # variant would silently miss its alias.
    n = _PUNCT_STRIP_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    n = _LEADING_THE_RE.sub("", n)
    return n.strip().casefold()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def to_filesystem_component(name: str) -> str:
    """A single canonical, filesystem-safe path component for `name`: NFKC
    normalized, characters illegal on Windows/POSIX stripped, whitespace
    collapsed, trailing dots/spaces removed (illegal as a Windows path
    component tail), length-capped. Empty/all-illegal input becomes
    "unknown" rather than an empty path component."""
    n = unicodedata.normalize("NFKC", name).strip()
    n = _FS_UNSAFE_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    n = _TRAILING_DOTS_SPACES_RE.sub("", n)
    if not n:
        n = "unknown"
    return n[:_MAX_COMPONENT_LEN]


@dataclass(frozen=True)
class NormalizeResult:
    canonical: str
    matched_alias: bool
    fuzzy: bool
    distance: int = 0


class AliasTable:
    """A canonical-name alias table: O(1) exact lookup (entries are folded
    once at construction, not per query) plus a bounded-edit-distance fuzzy
    fallback for small typos against the same table's known variants."""

    def __init__(
        self,
        aliases: Mapping[str, str],
        *,
        fuzzy_max_distance: int = _DEFAULT_FUZZY_MAX_DISTANCE,
    ):
        self._exact: dict[str, str] = {}
        for variant, canonical in aliases.items():
            self._exact[_fold(variant)] = canonical
        self._fuzzy_max_distance = fuzzy_max_distance

    def lookup(self, name: str) -> NormalizeResult:
        folded = _fold(name)

        canonical = self._exact.get(folded)
        if canonical is not None:
            return NormalizeResult(
                to_filesystem_component(canonical), matched_alias=True, fuzzy=False
            )

        if folded and self._fuzzy_max_distance > 0:
            best_canonical: str | None = None
            best_distance = self._fuzzy_max_distance + 1
            for key, cand_canonical in self._exact.items():
                d = _levenshtein(folded, key)
                if d < best_distance:
                    best_distance = d
                    best_canonical = cand_canonical
            if best_canonical is not None and best_distance <= self._fuzzy_max_distance:
                return NormalizeResult(
                    to_filesystem_component(best_canonical),
                    matched_alias=True,
                    fuzzy=True,
                    distance=best_distance,
                )

        return NormalizeResult(to_filesystem_component(name), matched_alias=False, fuzzy=False)


def normalize(name: str, *, aliases: AliasTable | None = None) -> str:
    """Return the canonical filesystem-path component for `name`.

    Deterministic: the same (name, aliases) always produces the same output.
    With no alias table, this is just `to_filesystem_component(name)`.
    """
    if aliases is None:
        return to_filesystem_component(name)
    return aliases.lookup(name).canonical


@dataclass(frozen=True)
class MetadataNormalizer:
    """Bundles separate alias tables per attribution field -- a raw string
    that means one publisher may coincidentally match a different series or
    author's alias, so each field's lookup stays scoped to its own table."""

    publisher_aliases: AliasTable
    series_aliases: AliasTable
    author_aliases: AliasTable

    def normalize_publisher(self, name: str) -> str:
        return self.publisher_aliases.lookup(name).canonical

    def normalize_series(self, name: str) -> str:
        return self.series_aliases.lookup(name).canonical

    def normalize_author(self, name: str) -> str:
        return self.author_aliases.lookup(name).canonical


def build_normalizer(
    *,
    publisher_aliases: Mapping[str, str] | None = None,
    series_aliases: Mapping[str, str] | None = None,
    author_aliases: Mapping[str, str] | None = None,
    fuzzy_max_distance: int = _DEFAULT_FUZZY_MAX_DISTANCE,
) -> MetadataNormalizer:
    return MetadataNormalizer(
        publisher_aliases=AliasTable(publisher_aliases or {}, fuzzy_max_distance=fuzzy_max_distance),
        series_aliases=AliasTable(series_aliases or {}, fuzzy_max_distance=fuzzy_max_distance),
        author_aliases=AliasTable(author_aliases or {}, fuzzy_max_distance=fuzzy_max_distance),
    )
