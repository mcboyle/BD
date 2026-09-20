"""RED-first tests for Row 878: Reciprocal Rank Fusion hybrid search.

``bulk_downloader.hybrid_search.rrf_search`` merges a keyword engine (e.g. BM25
via ``db.db_search_fts``) and a vector engine (e.g. ``semantic_search.search``)
into one ranking using Reciprocal Rank Fusion: score(id) = sum over engines that
returned it of 1 / (k + rank), rank 1-based. Both engines are passed in as
callables so the fusion math is testable without a live DB or embedding index.

Three load-bearing behaviors (Row 878 acceptance):
  1. RRF scoring combines keyword + vector ranks into one ranking.
  2. An exact alphanumeric-code match is prioritized over a loose vector-only hit.
  3. Fail-soft: an engine that raises is treated as offline, not fatal.
"""
from __future__ import annotations

import pytest

from bulk_downloader.hybrid_search import rrf_search

BD_GATE_SCOPE = "module"


def _kw_ranked(rows):
    """A stub keyword engine returning `rows` (list of dicts) in rank order."""
    def _fn(query, limit):
        return list(rows)[:limit]
    return _fn


def _vec_ranked(rows):
    """A stub vector engine returning `rows` (list of dicts) in rank order."""
    def _fn(query, limit):
        return list(rows)[:limit]
    return _fn


def _raising(_exc=RuntimeError("engine offline")):
    def _fn(query, limit):
        raise _exc
    return _fn


# ── 1. RRF scoring combines both engines ────────────────────────────────

def test_rrf_combines_keyword_and_vector_ranks():
    keyword = _kw_ranked([
        {"id": "doc-a", "title": "A"},
        {"id": "doc-b", "title": "B"},
        {"id": "doc-c", "title": "C"},
    ])
    vector = _vec_ranked([
        {"id": "doc-c", "title": "C"},
        {"id": "doc-a", "title": "A"},
        {"id": "doc-d", "title": "D"},
    ])
    out = rrf_search("q", keyword, vector, k=60, limit=10)
    assert out["ok"] is True
    ids = [r["id"] for r in out["results"]]
    # doc-a: kw rank1 + vec rank2 = 1/61 + 1/62 (highest combined)
    # doc-c: kw rank3 + vec rank1 = 1/63 + 1/61
    # doc-b: kw rank2 only = 1/62
    # doc-d: vec rank3 only = 1/63
    expect_a = 1 / 61 + 1 / 62
    expect_c = 1 / 63 + 1 / 61
    expect_b = 1 / 62
    expect_d = 1 / 63
    assert expect_a > expect_c > expect_b > expect_d
    assert ids == ["doc-a", "doc-c", "doc-b", "doc-d"]
    scores = {r["id"]: r["score"] for r in out["results"]}
    assert scores["doc-a"] == pytest.approx(expect_a)
    assert scores["doc-c"] == pytest.approx(expect_c)
    # both engines contributed to doc-a
    sources = {r["id"]: set(r["sources"]) for r in out["results"]}
    assert sources["doc-a"] == {"keyword", "vector"}
    assert sources["doc-b"] == {"keyword"}
    assert sources["doc-d"] == {"vector"}


# ── 2. Exact alphanumeric match outranks a loose vector-only hit ────────

def test_exact_alphanumeric_match_beats_loose_vector_similarity():
    # keyword engine finds the exact code at rank 1; vector engine doesn't see it
    # at all but ranks two "conceptually similar" docs highly.
    keyword = _kw_ranked([
        {"id": "ABC-1234", "title": "exact code match"},
    ])
    vector = _vec_ranked([
        {"id": "loose-1", "title": "vaguely related"},
        {"id": "loose-2", "title": "also vaguely related"},
    ])
    out = rrf_search("ABC-1234", keyword, vector, k=60, limit=10)
    ids = [r["id"] for r in out["results"]]
    assert ids[0] == "ABC-1234"
    assert out["results"][0]["score"] > out["results"][1]["score"]


def test_exact_match_boost_is_case_and_separator_insensitive():
    keyword = _kw_ranked([{"id": "sku_9981", "title": "exact"}])
    vector = _vec_ranked([{"id": "sku_9981", "title": "also found by vector"},
                           {"id": "other-doc", "title": "unrelated"}])
    out = rrf_search("SKU-9981 for the order", keyword, vector, k=60, limit=10)
    assert out["results"][0]["id"] == "sku_9981"


# ── 3. Fail-soft: one engine offline never breaks the search ────────────

def test_keyword_engine_offline_falls_back_to_vector_only():
    keyword = _raising()
    vector = _vec_ranked([{"id": "doc-a", "title": "A"}, {"id": "doc-b", "title": "B"}])
    out = rrf_search("q", keyword, vector, k=60, limit=10)
    assert out["ok"] is True
    assert [r["id"] for r in out["results"]] == ["doc-a", "doc-b"]
    assert out["engines"] == {"keyword": False, "vector": True}


def test_vector_engine_offline_falls_back_to_keyword_only():
    keyword = _kw_ranked([{"id": "doc-x", "title": "X"}])
    vector = _raising()
    out = rrf_search("q", keyword, vector, k=60, limit=10)
    assert out["ok"] is True
    assert [r["id"] for r in out["results"]] == ["doc-x"]
    assert out["engines"] == {"keyword": True, "vector": False}


def test_both_engines_offline_returns_empty_not_raise():
    out = rrf_search("q", _raising(), _raising(), k=60, limit=10)
    assert out["ok"] is True
    assert out["results"] == []
    assert out["engines"] == {"keyword": False, "vector": False}


def test_non_list_engine_return_is_treated_as_offline():
    def _bad(query, limit):
        return None
    vector = _vec_ranked([{"id": "doc-a", "title": "A"}])
    out = rrf_search("q", _bad, vector, k=60, limit=10)
    assert out["ok"] is True
    assert out["engines"]["keyword"] is False
    assert [r["id"] for r in out["results"]] == ["doc-a"]


def test_limit_truncates_fused_results():
    keyword = _kw_ranked([{"id": f"kw-{i}"} for i in range(5)])
    vector = _vec_ranked([{"id": f"vec-{i}"} for i in range(5)])
    out = rrf_search("q", keyword, vector, k=60, limit=3)
    assert len(out["results"]) == 3


# ---- fixer (O928) controls for the correctness REFUTE E1-E5 --------------

def test_e1_code_inside_a_phrase_is_an_exact_match():
    from bulk_downloader.hybrid_search import rrf_search, _is_exact_match
    assert _is_exact_match("SKU-9981 for the order", "sku_9981")
    assert _is_exact_match("order sku-9981", "SKU_9981")
    assert not _is_exact_match("sku-99810 for the order", "sku_9981")
    assert not _is_exact_match("9981", "sku_9981")
    kw = lambda q, n: [{"id": "loose-1"}, {"id": "sku_9981"}]
    vec = lambda q, n: [{"id": "loose-1"}, {"id": "loose-2"}]
    res = rrf_search("SKU-9981 for the order", kw, vec)
    assert res["results"][0]["id"] == "sku_9981"


def test_e2_duplicate_rows_within_one_engine_vote_once():
    from bulk_downloader.hybrid_search import rrf_search
    kw = lambda q, n: [{"id": "a"}, {"id": "b"}, {"id": "a"}]
    vec = lambda q, n: []
    res = rrf_search("x", kw, vec, k=60)
    scores = {r["id"]: r["score"] for r in res["results"]}
    assert abs(scores["a"] - 1 / 61) < 1e-12
    assert abs(scores["b"] - 1 / 62) < 1e-12
    assert res["engines"] == {"keyword": True, "vector": True}


def test_e3_namespaced_identities_are_not_collapsed():
    from bulk_downloader.hybrid_search import rrf_search
    vec = lambda q, n: [{"kind": "capture", "id": "shared", "summary": "c"},
                        {"kind": "template", "id": "shared", "summary": "t"}]
    kw = lambda q, n: [{"id": "other"}]
    res = rrf_search("shared", kw, vec)
    kinds = sorted((r.get("kind") or "", r["id"]) for r in res["results"])
    assert kinds == [("", "other"), ("capture", "shared"), ("template", "shared")]
    assert len(res["results"]) == 3


def test_e4_malformed_engine_list_is_offline():
    from bulk_downloader.hybrid_search import rrf_search
    kw = lambda q, n: [None, {"title": "no id"}]
    vec = lambda q, n: [{"id": "v1"}]
    res = rrf_search("x", kw, vec)
    assert res["engines"] == {"keyword": False, "vector": True}
    assert [r["id"] for r in res["results"]] == ["v1"]
    assert res["results"][0]["sources"] == ["vector"]


# ---- fixer (O928) controls: correctness REFUTE items 1-2 (runtime engine adapters) ----

def test_keyword_engine_adapts_db_search_fts_signature_and_tags_kind(monkeypatch):
    """Item 1: db_search_fts takes a keyword-only limit and returns bare
    history rows; the adapter calls it correctly and tags every row."""
    from bulk_downloader import db, hybrid_search as hs
    calls = []

    def fake_fts(query, *, site_id=None, status=None, limit=100):
        calls.append((query, limit))
        return [{"id": 7, "name": "SKU-9981.mp4"}, {"id": 8, "name": "other"}, None, {"name": "no id"}]

    monkeypatch.setattr(db, "db_search_fts", fake_fts)
    rows = hs.keyword_engine("sku-9981", 5)
    assert calls == [("sku-9981", 5)]
    assert rows == [{"id": 7, "name": "SKU-9981.mp4", "kind": "history"},
                    {"id": 8, "name": "other", "kind": "history"}]
    rows_ok, online = hs._safe_call(hs.keyword_engine, "sku-9981", 5)
    assert online is True and len(rows_ok) == 2


def test_vector_engine_unwraps_the_semantic_search_envelope(monkeypatch):
    """Item 1: semantic_search.search returns {ok, results}; the adapter
    unwraps it, and an {ok: False} envelope is an OFFLINE engine."""
    from bulk_downloader import semantic_search, hybrid_search as hs
    monkeypatch.setattr(semantic_search, "search", lambda q, k=10, base_dir=None: {
        "ok": True, "query": q, "results": [{"kind": "capture", "id": "a/b", "score": 0.9}]})
    assert hs.vector_engine("q", 3) == [{"kind": "capture", "id": "a/b", "score": 0.9}]
    monkeypatch.setattr(semantic_search, "search", lambda q, k=10, base_dir=None: {"ok": False, "error": "x"})
    rows, online = hs._safe_call(hs.vector_engine, "q", 3)
    assert rows == [] and online is False


def test_hybrid_search_fuses_the_real_engines_on_a_uniform_identity(monkeypatch):
    """Item 2: an item both engines return under the same kind/id fuses into
    ONE entry with two sources; items of different kinds stay apart."""
    from bulk_downloader import db, semantic_search, hybrid_search as hs
    monkeypatch.setattr(db, "db_search_fts", lambda q, *, site_id=None, status=None, limit=100: [
        {"id": "clip-1", "kind": "capture"}, {"id": 42}])
    monkeypatch.setattr(semantic_search, "search", lambda q, k=10, base_dir=None: {
        "ok": True, "results": [{"kind": "capture", "id": "clip-1", "score": 0.8},
                                {"kind": "template", "id": 42, "score": 0.5}]})
    res = hs.hybrid_search("clip", limit=10)
    assert res["engines"] == {"keyword": True, "vector": True}
    by_key = {hs._identity(r): r for r in res["results"]}
    assert set(by_key) == {"capture/clip-1", "history/42", "template/42"}
    assert by_key["capture/clip-1"]["sources"] == ["keyword", "vector"]
    assert by_key["history/42"]["sources"] == ["keyword"] and by_key["template/42"]["sources"] == ["vector"]
    assert res["results"][0]["id"] == "clip-1"        # fused entry ranks first
