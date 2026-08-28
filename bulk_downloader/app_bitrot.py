"""bitrot API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/bitrot views moved onto a Flask Blueprint.
Endpoint labels gain a "bitrot." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bitrot_bp = Blueprint("bitrot", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@bitrot_bp.route("/api/bitrot/scan", methods=["POST"])
def api_bitrot_scan():
    """Trigger a bit-rot scan. Returns the summary {checked, intact,
    missing, modified, truncated, errors}. Caller-throttled — this
    can be expensive on large libraries."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import bitrot as _br
        result = _br.run_scan(
            scan_fraction=float(body.get("scan_fraction", 0.05)),
            min_age_days=int(body.get("min_age_days", 7)),
            max_files=int(body.get("max_files", 100)),
        )
        return jsonify(result), (200 if result.get("available", True) else 503)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@bitrot_bp.route("/api/bitrot/issues")
def api_bitrot_issues():
    try:
        from . import bitrot as _br
        kind = request.args.get("kind") or None
        repaired = request.args.get("repaired")
        rep = None if repaired is None else (repaired in ("1", "true", "yes"))
        return jsonify({"issues": _br.list_issues(kind=kind, repaired=rep)})
    except Exception as e:
        return jsonify({
            "ok": False,
            "available": False,
            "inventory_status": "unknown",
            "issues": None,
            "error": str(e)[:200],
        }), 503

@bitrot_bp.route("/api/bitrot/stats")
def api_bitrot_stats():
    try:
        from . import bitrot as _br
        result = _br.stats()
        return jsonify(result), (200 if result.get("available", True) else 503)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(bitrot_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bitrot."))
