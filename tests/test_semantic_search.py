"""RED-first tests for Cut 624 / C2: semantic search over captures + templates.

The RAG/search layer: index the Phase-1 capture metadata and the template corpus,
then answer an NL query with the right prior item. ``reindex`` accepts injected
lists so the engine is hermetically testable without wrestling the live DB path;
at runtime the same call pulls from ``db.db_captures_all`` + user templates.

The load-bearing assertion is "returns the right prior capture/template".

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, base_dir params, no
monkeypatch.
"""
from __future__ import annotations

import tempfile


def _base():
    return tempfile.mkdtemp(prefix="bdsem_")


def _captures():
    return [
        {"rel_path": "a.wacz", "name": "reptyle-jwplayer-login", "host": "reptyle.com",
         "dir": "", "kind": "wacz", "redacted": True, "captured_at": 3.0},
        {"rel_path": "b.wacz", "name": "wowgirls-hls-manifest-ladder", "host": "wowgirls.com",
         "dir": "", "kind": "wacz", "redacted": True, "captured_at": 2.0},
        {"rel_path": "c.json", "name": "bros-dash-segments", "host": "bros.com",
         "dir": "", "kind": "json", "redacted": True, "captured_at": 1.0},
    ]


def _templates():
    return [
        {"id": "t1", "name": "JWPlayer signed token", "description": "jwplayer signing scheme",
         "patterns": ["reptyle.com/*"], "learned": {"download": {"trigger": "button.dl"}}},
        {"id": "t2", "name": "HLS master playlist", "description": "hls manifest rendition ladder",
         "patterns": ["wowgirls.com/*"], "learned": {}},
    ]


def test_reindex_reports_indexed_count():
    from bulk_downloader import semantic_search as S
    base = _base()
    r = S.reindex(captures=_captures(), templates=_templates(), base_dir=base)
    assert r["ok"] is True
    assert r["indexed"] == 5  # 3 captures + 2 templates


def test_search_returns_the_right_prior_capture():
    from bulk_downloader import semantic_search as S
    base = _base()
    S.reindex(captures=_captures(), templates=_templates(), base_dir=base)
    hits = S.search("jwplayer login capture on reptyle", k=3, base_dir=base)["results"]
    assert hits, "expected at least one hit"
    # the reptyle-jwplayer-login capture should be the top capture-kind hit
    cap_hits = [h for h in hits if h["kind"] == "capture"]
    assert cap_hits and cap_hits[0]["id"] == "a.wacz"


def test_search_returns_the_right_template():
    from bulk_downloader import semantic_search as S
    base = _base()
    S.reindex(captures=_captures(), templates=_templates(), base_dir=base)
    hits = S.search("hls manifest rendition ladder", k=5, base_dir=base)["results"]
    tmpl_hits = [h for h in hits if h["kind"] == "template"]
    assert tmpl_hits and tmpl_hits[0]["id"] == "t2"


def test_results_carry_kind_id_score_summary():
    from bulk_downloader import semantic_search as S
    base = _base()
    S.reindex(captures=_captures(), templates=_templates(), base_dir=base)
    hits = S.search("jwplayer", k=1, base_dir=base)["results"]
    assert hits
    h = hits[0]
    assert h["kind"] in ("capture", "template")
    assert "id" in h and "score" in h and "summary" in h
    assert isinstance(h["score"], float)


def test_empty_corpus_search_returns_empty():
    from bulk_downloader import semantic_search as S
    base = _base()
    S.reindex(captures=[], templates=[], base_dir=base)
    assert S.search("anything", base_dir=base)["results"] == []


def test_status_reports_indexed_and_enabled():
    from bulk_downloader import semantic_search as S
    base = _base()
    S.reindex(captures=_captures(), templates=_templates(), base_dir=base)
    st = S.status(base_dir=base)
    assert st["indexed"] == 5
    assert "enabled" in st and "dims" in st


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
