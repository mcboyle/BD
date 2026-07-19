"""rebalance API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/rebalance views moved onto a Flask Blueprint.
Endpoint labels gain a "rebalance." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

rebalance_bp = Blueprint("rebalance", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@rebalance_bp.route("/api/rebalance/inventory")
def api_rebalance_inventory():
    s_cfg = _app_s_cfg()
    paths = request.args.getlist("path") or list({
        (cfg or {}).get("download_dir", "")
        for cfg in (s_cfg or {}).values()
        if (cfg or {}).get("download_dir")
    })
    try:
        from . import storage_rebalance as _sr
        return jsonify({"inventory": _sr.inventory(paths)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@rebalance_bp.route("/api/rebalance/plan", methods=["POST"])
def api_rebalance_plan():
    _check_csrf()
    body = request.json or {}
    paths = body.get("paths") or []
    if not paths:
        return jsonify({"error": "paths required"}), 400
    try:
        from . import storage_rebalance as _sr
        return jsonify(_sr.plan_rebalance(
            paths,
            strategy=body.get("strategy", "even_fill"),
            max_moves=int(body.get("max_moves", 100)),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@rebalance_bp.route("/api/rebalance/execute", methods=["POST"])
def api_rebalance_execute():
    _check_csrf()
    body = request.json or {}
    if not body.get("plan"):
        return jsonify({"ok": False, "error": "plan required"}), 400
    try:
        from . import storage_rebalance as _sr
        return jsonify(_sr.execute_plan(
            body["plan"],
            dry_run=bool(body.get("dry_run", True)),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(rebalance_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("rebalance."))

