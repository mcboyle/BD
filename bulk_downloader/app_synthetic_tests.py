"""synthetic_tests API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/synthetic_tests views moved onto a Flask Blueprint.
Endpoint labels gain a "synthetic_tests." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

synthetic_tests_bp = Blueprint("synthetic_tests", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@synthetic_tests_bp.route("/api/synthetic_tests/list")
def api_synth_list():
    """List all configured fixtures."""
    try:
        from . import synthetic_tests as _st
        return jsonify({"fixtures": _st.list_fixtures()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@synthetic_tests_bp.route("/api/synthetic_tests/run_all", methods=["POST"])
def api_synth_run_all():
    """Run every fixture; report per-site pass/fail. Safe — no
    network calls, only HAR replay."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import synthetic_tests as _st
        return jsonify(_st.run_all(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(synthetic_tests_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("synthetic_tests."))

