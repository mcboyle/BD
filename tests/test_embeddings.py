"""RED-first tests for Cut 624 / C2: dependency-free text embeddings.

A feature-hashing embedder (word tokens + char n-grams -> fixed-dim, L2-normalized
vector) so semantic retrieval needs no model download and no native extension --
fully deterministic and sandbox-testable. Shared vocabulary -> higher cosine.

Sandbox-runner conventions: zero-arg, no monkeypatch, no external deps.
"""
from __future__ import annotations

import math


def test_embed_returns_fixed_dim_float_vector():
    from bulk_downloader import embeddings as E
    v = E.embed("hello world", dims=128)
    assert isinstance(v, list) and len(v) == 128
    assert all(isinstance(x, float) for x in v)


def test_embed_is_l2_normalized():
    from bulk_downloader import embeddings as E
    v = E.embed("the quick brown fox", dims=256)
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_embed_is_deterministic():
    from bulk_downloader import embeddings as E
    a = E.embed("reptyle jwplayer capture", dims=256)
    b = E.embed("reptyle jwplayer capture", dims=256)
    assert a == b


def test_similar_text_scores_higher_than_dissimilar():
    from bulk_downloader import embeddings as E
    q = E.embed("login form username password", dims=256)
    near = E.embed("username and password login page", dims=256)
    far = E.embed("hls video manifest segment ladder", dims=256)
    assert E.cosine(q, near) > E.cosine(q, far)


def test_empty_text_is_zero_vector_and_cosine_is_safe():
    from bulk_downloader import embeddings as E
    z = E.embed("", dims=64)
    assert all(x == 0.0 for x in z)
    # cosine against a zero vector must not divide by zero
    other = E.embed("anything", dims=64)
    assert E.cosine(z, other) == 0.0
    assert E.cosine(z, z) == 0.0


def test_cosine_self_is_one_and_bounded():
    from bulk_downloader import embeddings as E
    v = E.embed("some descriptive capture text", dims=256)
    assert abs(E.cosine(v, v) - 1.0) < 1e-6
    q = E.embed("different text here", dims=256)
    c = E.cosine(v, q)
    assert -1.0001 <= c <= 1.0001


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for fn in fns:
        try:
            fn(); p += 1; print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            f += 1; print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p} passed / {f} failed")
    raise SystemExit(1 if f else 0)
