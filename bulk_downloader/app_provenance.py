"""provenance API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/provenance views moved onto a Flask Blueprint.
Endpoint labels gain a "provenance." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

provenance_bp = Blueprint("provenance", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@provenance_bp.route("/api/provenance/query")
def api_provenance_query():
    """Query the ledger by url/filename/sha256/site/time range."""
    try:
        from . import provenance as _p
        kwargs = {}
        for k in ("url", "filename", "sha256", "site_id"):
            v = request.args.get(k)
            if v:
                kwargs[k] = v
        for k in ("ts_from", "ts_to"):
            v = request.args.get(k)
            if v:
                try:
                    kwargs[k] = float(v)
                except (TypeError, ValueError):
                    pass
        try:
            kwargs["limit"] = min(500, int(request.args.get("limit", 100)))
        except (TypeError, ValueError):
            kwargs["limit"] = 100
        return jsonify({"rows": _p.query(**kwargs)})
    except Exception as e:
        return jsonify({"rows": [], "error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/verify", methods=["POST"])
def api_provenance_verify():
    """Run a full chain verification. Slow on large ledgers; intended
    to be invoked manually or from a nightly task, not on every poll."""
    _check_csrf()
    try:
        from . import provenance as _p
        return jsonify(_p.verify_chain())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@provenance_bp.route("/api/provenance/stats")
def api_provenance_stats():
    try:
        from . import provenance as _p
        return jsonify(_p.stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(provenance_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("provenance."))

