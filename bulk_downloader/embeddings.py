"""bulk_downloader.embeddings -- Cut 624 / C2: dependency-free text embeddings.

Turns text into a dense, fixed-dimension vector so semantic retrieval
(``vector_index`` + ``semantic_search``) needs NO model download and NO native
extension. The default embedder is a **feature-hashing** vectorizer over word
tokens plus per-word character 3-grams, L2-normalized -- deterministic, stdlib
only, fully sandbox-testable. Shared vocabulary (whole words or subword n-grams)
raises the cosine, which is exactly what a "find the prior capture that talks
about X" search needs.

This is honest lexical/subword-semantic retrieval, not a neural embedding. A
neural backend (Ollama's ``/api/embeddings``, reusing the existing
``ai_provider`` Ollama plumbing) is a deliberate follow, availability-gated like
the litestream / gallery-dl paths -- the hashing embedder is the shipping default
so the feature works on any box.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

DEFAULT_DIMS = 256
# Unicode letters/digits (``\w`` minus ``_``): on ASCII this is exactly the old
# ``[a-z0-9]+`` after lowercasing, so ASCII vectors are unchanged, while
# Cyrillic / CJK / Greek / accented words now embed instead of vanishing.
_WORD_RE = re.compile(r"[^\W_]+")
# Bumped whenever ``_tokens`` changes what a stored vector means. An index built
# under another version must be rebuilt, never scored against (1 = ``[a-z0-9]+``).
TOKENIZER_VERSION = 2
_NGRAM = 3  # per-word character n-gram size


def _tokens(text: str) -> list[str]:
    """Casefolded word tokens plus character n-grams of each word (padded), so
    'jwplayer' and 'jwplayers' share most of their subword features. Text is NFKC
    normalized first, so precomposed and decomposed 'café' are one token, with
    no accent stripping. Deterministic and dependency-free."""
    out: list[str] = []
    norm = unicodedata.normalize("NFKC", text or "").casefold()
    for w in _WORD_RE.findall(norm):
        out.append(w)
        if len(w) >= _NGRAM:
            padded = f"^{w}$"
            for i in range(len(padded) - _NGRAM + 1):
                out.append("#" + padded[i:i + _NGRAM])
        else:
            out.append("#^" + w + "$")
    return out


def _bucket_and_sign(token: str, dims: int) -> tuple[int, float]:
    """Stable (salted-hash-free) hashing: bucket index in [0, dims) plus a +/-1
    sign from an independent hash bit, so collisions partly cancel instead of
    always adding (the standard signed feature-hashing trick)."""
    h = hashlib.md5(token.encode("utf-8"), usedforsecurity=False).digest()
    idx = int.from_bytes(h[:4], "big") % dims
    sign = 1.0 if (h[4] & 1) else -1.0
    return idx, sign


def embed(text: str, dims: int = DEFAULT_DIMS) -> list[float]:
    """Feature-hash ``text`` into a length-``dims`` L2-normalized vector. Empty or
    tokenless text returns the zero vector (norm 0), which ``cosine`` treats as
    orthogonal to everything (no division by zero). A ``dims`` that is not a
    positive int falls back to ``DEFAULT_DIMS`` (the rule ``semantic_search``
    applies to its config), so a bad width never raises mid-embed."""
    if isinstance(dims, bool) or not isinstance(dims, int) or dims <= 0:
        dims = DEFAULT_DIMS
    vec = [0.0] * dims
    for tok in _tokens(text):
        idx, sign = _bucket_and_sign(tok, dims)
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec  # zero vector
    return [x / norm for x in vec]


def embed_batch(texts, dims: int = DEFAULT_DIMS) -> list[list[float]]:
    return [embed(t, dims) for t in (texts or [])]


def cosine(a, b) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0.0 if either is a
    zero vector or lengths differ (fail-safe -- never raises)."""
    try:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0
