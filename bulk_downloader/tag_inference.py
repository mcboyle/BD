"""Tag inference from filenames/URLs + performer-name canonicalization
(Phase 94, Block K).

Two related capabilities the search/recommendation layers depend on:

1. infer_tags(text) — extract candidate tags from a filename, URL, or
   message string. Pattern-based, not ML. Returns a list of normalized
   tag strings ordered by confidence.

2. canonicalize_performer(name) — collapse spelling variants of the
   same performer name to a canonical form (e.g. "Alex Coal",
   "Alexandra Coal", "alex.coal" → "alex coal"). The output is a
   stable identity key; the display name is preserved separately.

Both are pure functions — no DB writes, no state. The caller (search
backend, recommendation engine) decides what to do with the output.

A small known-aliases dict is shipped here for the highest-volume
cases; the full performer-merge UI (Phase 94 UI work) lets operators
add more aliases at runtime via /api/aliases/add.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional


# ─── tag inference ────────────────────────────────────────────────────

# Resolution + codec tokens — extracted commonly from filenames.
_RES_TAGS = {
    "2160p": "4k", "2160": "4k", "uhd": "4k", "4k": "4k",
    "1440p": "qhd", "1440": "qhd",
    "1080p": "1080p", "1080": "1080p", "fhd": "1080p",
    "720p": "720p", "720": "720p", "hd": "720p",
    "480p": "480p", "480": "480p", "sd": "sd",
    "8k": "8k",
}
_CODEC_TAGS = {"h264", "h265", "hevc", "avc", "x264", "x265", "av1",
               "vp9"}

# Domain/source tokens we want to surface as tags
_SOURCE_TAGS = {"web", "webrip", "webdl", "web-dl", "bluray",
                "remux", "dvdrip", "hdrip", "hdtv"}

# Content tokens that show up regularly in adult/video filenames
# and are worth tagging. Conservative list — false positives are
# annoying so we skip ambiguous words.
_CONTENT_TAGS = {
    "anal", "bbc", "bdsm", "bondage", "creampie", "deepthroat",
    "double", "facial", "gangbang", "interracial", "lesbian",
    "milf", "orgy", "outdoor", "pov", "redhead", "rough",
    "solo", "threesome", "toys", "vintage", "vr", "180", "360",
}

# Filename noise we strip before tokenizing
_NOISE_RE = re.compile(
    r"(?:scene[\s\-_]?\d+|part[\s\-_]?\d+|episode[\s\-_]?\d+|"
    r"video[\s\-_]?\d+|\(\d+\)|\[\d+\])",
    re.IGNORECASE,
)

# Tokenizer — splits on whitespace, ., -, _, [, ], (, ), +, &
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _normalize(s: str) -> str:
    """Lower, NFKD, strip diacritics, collapse whitespace. Stable
    key for case-insensitive matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def infer_tags(text: str, *, max_tags: int = 12) -> list[str]:
    """Extract candidate tags from `text` (filename / URL / message).
    Returns a list of normalized tag strings, ordered by confidence
    (more-specific tags first). Deduplicated and capped at `max_tags`."""
    if not text:
        return []
    body = _NOISE_RE.sub(" ", _normalize(text))
    tokens = _TOKEN_RE.findall(body)
    out: list[str] = []
    seen: set = set()

    def _add(tag: str):
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)

    # Pass 1: resolution + codec — highest confidence
    for tok in tokens:
        if tok in _RES_TAGS:
            _add(_RES_TAGS[tok])
        if tok in _CODEC_TAGS:
            _add(tok)
        if tok in _SOURCE_TAGS:
            _add(tok)
    # Pass 2: known content tags
    for tok in tokens:
        if tok in _CONTENT_TAGS:
            _add(tok)
    # Pass 3: compound matches ("interracial-anal", "double-anal" etc)
    joined = "-".join(tokens)
    for compound in ("double-anal", "double-penetration", "all-girl",
                     "first-time"):
        if compound in joined:
            _add(compound)
    return out[:max_tags]


# ─── performer canonicalization ───────────────────────────────────────

# Hand-curated alias map. Maps every variant to the canonical key.
# Canonical key is "first-last", lowercased, no diacritics.
# Operators can extend this at runtime via add_alias() (in-memory,
# persisted by the caller if they want it durable).
_PERFORMER_ALIASES: dict[str, str] = {
    # canonical -> set of aliases (we invert below for fast lookup)
}

# Inverted lookup: alias_normalized -> canonical
_alias_index: dict[str, str] = {}


def _split_name(name: str) -> tuple[str, str]:
    """Split into (first, last) heuristically. Two-token names map
    cleanly; longer names take the first and last token, ignoring
    middles. Single-token names return (token, '')."""
    tokens = _TOKEN_RE.findall(_normalize(name))
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return (tokens[0], "")
    return (tokens[0], tokens[-1])


def canonicalize_performer(name: str) -> Optional[str]:
    """Return the canonical identity key for a performer name, or
    None if `name` is empty/non-string.

    Algorithm:
      1. If name matches a registered alias → return its canonical
      2. Otherwise, normalize: lowercase, strip diacritics, strip
         punctuation, collapse whitespace → "first last"
      3. Result is the canonical key itself
    """
    if not name or not isinstance(name, str):
        return None
    norm = _normalize(name)
    # Match by full normalized string against alias index
    if norm in _alias_index:
        return _alias_index[norm]
    # Tokenized canonical form
    first, last = _split_name(name)
    if not first:
        return None
    canonical = f"{first} {last}".strip()
    return canonical


def add_alias(canonical: str, alias: str) -> bool:
    """Register that `alias` refers to the same performer as
    `canonical`. Returns True on success. Use to teach BD that
    'Alex Coal' and 'Alexandra Coal' are the same person."""
    if not canonical or not alias:
        return False
    can = canonicalize_performer(canonical)
    if not can:
        return False
    _alias_index[_normalize(alias)] = can
    _PERFORMER_ALIASES.setdefault(can, set()).add(alias)
    return True


def aliases_for(canonical: str) -> list[str]:
    """All known aliases (including the canonical itself) for a
    performer key."""
    can = canonicalize_performer(canonical)
    if not can:
        return []
    return sorted(_PERFORMER_ALIASES.get(can, set()) | {canonical})


def merge_performer_list(names: Iterable[str]) -> list[str]:
    """Apply canonicalization to a list of performer names, dropping
    duplicates and empties. Preserves first-seen order of canonical
    keys."""
    out: list[str] = []
    seen: set = set()
    for n in names or []:
        c = canonicalize_performer(n)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def infer_performers(text: str, *, known: Optional[Iterable[str]] = None) -> list[str]:
    """Extract likely performer names from a filename/URL by checking
    each two-token substring against the known performer set.

    `known` is the operator's list of performers to look for (from
    previous downloads, the alias registry, or a manual list). Without
    it, this function returns []; pure-text performer extraction from
    filenames is too noisy without a candidate set.

    Returns canonical keys in first-match order, deduplicated.
    """
    if not text or not known:
        return []
    norm = _normalize(text)
    out = []
    seen = set()
    for k in known:
        ck = canonicalize_performer(k)
        if not ck:
            continue
        if ck in seen:
            continue
        # Check if all tokens of the canonical key appear in the text
        if all(tok in norm for tok in ck.split()):
            seen.add(ck)
            out.append(ck)
    return out
