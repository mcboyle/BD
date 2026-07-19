"""circuit API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/circuit views moved onto a Flask Blueprint.
Endpoint labels gain a "circuit." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

circuit_bp = Blueprint("circuit", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@circuit_bp.route("/api/circuit/report")
def api_circuit_report():
    try:
        from . import circuit_breaker as _cb
        return jsonify({"hosts": _cb.report()})
    except Exception as e:
        return jsonify({"hosts": {}, "error": str(e)[:200]}), 500

@circuit_bp.route("/api/circuit/reset", methods=["POST"])
def api_circuit_reset():
    _check_csrf()
    body = request.json or {}
    try:
        from . import circuit_breaker as _cb
        _cb.reset(host=body.get("host") or None)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(circuit_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("circuit."))

