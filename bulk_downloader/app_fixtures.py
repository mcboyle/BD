"""fixtures API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/fixtures views moved onto a Flask Blueprint.
Endpoint labels gain a "fixtures." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

fixtures_bp = Blueprint("fixtures", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@fixtures_bp.route("/api/fixtures/list")
def api_fixtures_list():
    try:
        from . import synthetic_tests as _syn
        return jsonify({"fixtures": _syn.list_fixtures()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@fixtures_bp.route("/api/fixtures/run_all", methods=["POST"])
def api_fixtures_run():
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import synthetic_tests as _syn
        return jsonify(_syn.run_all(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(fixtures_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("fixtures."))

