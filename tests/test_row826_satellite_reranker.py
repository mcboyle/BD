"""Contract tests for the optional local semantic-search reranker."""
from __future__ import annotations

import json


BD_GATE_SCOPE = "module"


class _Reply:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_search_reranks_the_top_fifty_candidates_from_the_local_endpoint(monkeypatch):
    from bulk_downloader import semantic_search as search

    raw_hits = [
        {"id": f"template:{n}", "score": float(100 - n),
         "meta": {"kind": "template", "id": str(n), "summary": f"document {n}"}}
        for n in range(55)
    ]
    calls = []

    def fake_vector_search(_query_vector, *, k, base_dir):
        assert base_dir is None
        assert k == 50
        return raw_hits[:k]

    def fake_urlopen(request, *, timeout):
        calls.append((request, timeout))
        return _Reply({"results": [{"index": n, "score": float(n)} for n in range(50)]})

    with monkeypatch.context() as patch:
        patch.setenv("RERANKER_ENDPOINT", "http://127.0.0.1:8082")
        patch.setattr(search._vi, "search", fake_vector_search)
        patch.setattr(search, "urlopen", fake_urlopen)
        result = search.search("semantic query", k=10)

    assert len(calls) == 1, "configured local reranker must receive the top fifty candidates"
    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:8082/rerank"
    assert timeout <= 0.15
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {"query": "semantic query", "documents": [f"document {n}" for n in range(50)]}
    assert [row["id"] for row in result["results"]] == [str(n) for n in range(49, 39, -1)]


def test_search_falls_back_to_vector_order_when_the_local_reranker_times_out(monkeypatch):
    from bulk_downloader import semantic_search as search

    raw_hits = [
        {"id": f"template:{n}", "score": float(10 - n),
         "meta": {"kind": "template", "id": str(n), "summary": f"document {n}"}}
        for n in range(10)
    ]
    calls = []

    def fake_urlopen(_request, *, timeout):
        calls.append(timeout)
        raise TimeoutError("reranker timed out")

    with monkeypatch.context() as patch:
        patch.setenv("RERANKER_ENDPOINT", "http://127.0.0.1:8082")
        patch.setattr(search._vi, "search", lambda _v, *, k, base_dir: raw_hits[:k])
        patch.setattr(search, "urlopen", fake_urlopen)
        result = search.search("semantic query", k=3)

    assert calls == [0.15]
    assert [row["id"] for row in result["results"]] == ["0", "1", "2"]


def test_search_never_contacts_a_non_loopback_reranker_endpoint(monkeypatch):
    from bulk_downloader import semantic_search as search

    raw_hits = [{"id": "template:raw", "score": 1.0,
                 "meta": {"kind": "template", "id": "raw", "summary": "raw document"}}]

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("non-loopback reranker endpoint must not be contacted")

    with monkeypatch.context() as patch:
        patch.setenv("RERANKER_ENDPOINT", "http://198.51.100.9:8082")
        patch.setattr(search._vi, "search", lambda _v, *, k, base_dir: raw_hits[:k])
        patch.setattr(search, "urlopen", unexpected_network)
        result = search.search("semantic query", k=1)

    assert [row["id"] for row in result["results"]] == ["raw"]
