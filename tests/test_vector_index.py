"""RED-first tests for Cut 624 / C2: brute-force cosine vector index.

A single-file (JSON) vector store with top-k cosine search. Brute force is the
shipping default -- correct at any single-operator corpus size and dependency-
free. ``sqlite-vec`` is DETECTED and gated as a future speed path, never required.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, base_dir params, no
monkeypatch. Read paths never raise on an empty store.
"""
from __future__ import annotations

import math
import tempfile


def _base():
    return tempfile.mkdtemp(prefix="bdvec_")


def _unit(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def test_upsert_then_search_returns_nearest_first():
    from bulk_downloader import vector_index as VI
    base = _base()
    VI.upsert_many([
        ("a", _unit([1.0, 0.0, 0.0]), {"t": "x-axis"}),
        ("b", _unit([0.0, 1.0, 0.0]), {"t": "y-axis"}),
        ("c", _unit([0.9, 0.1, 0.0]), {"t": "near-x"}),
    ], base_dir=base)
    hits = VI.search(_unit([1.0, 0.0, 0.0]), k=2, base_dir=base)
    assert [h["id"] for h in hits] == ["a", "c"]
    assert hits[0]["meta"]["t"] == "x-axis"
    assert hits[0]["score"] >= hits[1]["score"]


def test_search_respects_k():
    from bulk_downloader import vector_index as VI
    base = _base()
    VI.upsert_many([(str(i), _unit([float(i), 1.0]), {}) for i in range(5)], base_dir=base)
    assert len(VI.search(_unit([1.0, 1.0]), k=3, base_dir=base)) == 3


def test_remove_and_size_and_clear():
    from bulk_downloader import vector_index as VI
    base = _base()
    VI.upsert_many([("a", _unit([1.0, 0.0]), {}), ("b", _unit([0.0, 1.0]), {})], base_dir=base)
    assert VI.size(base_dir=base) == 2
    assert VI.remove("a", base_dir=base) is True
    assert VI.size(base_dir=base) == 1
    assert VI.remove("nope", base_dir=base) is False
    VI.clear(base_dir=base)
    assert VI.size(base_dir=base) == 0


def test_persists_across_reload():
    from bulk_downloader import vector_index as VI
    base = _base()
    VI.upsert("k", _unit([1.0, 2.0, 3.0]), {"note": "kept"}, base_dir=base)
    # a fresh read from the same base_dir (no in-memory carry) sees it
    hits = VI.search(_unit([1.0, 2.0, 3.0]), k=1, base_dir=base)
    assert hits and hits[0]["id"] == "k" and hits[0]["meta"]["note"] == "kept"


def test_empty_index_search_returns_empty():
    from bulk_downloader import vector_index as VI
    base = _base()
    assert VI.search(_unit([1.0, 0.0]), k=5, base_dir=base) == []
    assert VI.size(base_dir=base) == 0


def test_sqlite_vec_available_returns_bool():
    from bulk_downloader import vector_index as VI
    assert isinstance(VI.sqlite_vec_available(), bool)


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
