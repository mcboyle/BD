"""Route-contract tests for Cut 624 / C2: the /api/semantic blueprint.

Pins the surface (3 routes registered, correct methods) and the read path. The
engine logic is covered by test_embeddings / test_vector_index / test_semantic_search;
these keep the blueprint honest.

Sandbox-runner conventions: zero-arg, no monkeypatch. Uses an isolated Flask app
with only the semantic blueprint so no CSRF spine / full-app boot is required.
"""
from __future__ import annotations


def _iso_app():
    from flask import Flask
    from bulk_downloader import app_semantic_search as M
    app = Flask(__name__)
    n = M.register_routes(app)
    return app, n


def test_register_routes_reports_three():
    app, n = _iso_app()
    assert n == 3


def test_the_three_routes_are_registered_with_expected_methods():
    app, _ = _iso_app()
    rules = {r.rule: sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
             for r in app.url_map.iter_rules() if r.endpoint.startswith("semantic.")}
    assert rules.get("/api/semantic/status") == ["GET"]
    assert rules.get("/api/semantic/search") == ["POST"]
    assert rules.get("/api/semantic/reindex") == ["POST"]


def test_status_route_returns_ok_shape():
    import os, tempfile
    app, _ = _iso_app()
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp(prefix="bdc2rte_"))
    try:
        r = app.test_client().get("/api/semantic/status")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "indexed" in body and "enabled" in body and "dims" in body
    finally:
        os.chdir(cwd)


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
