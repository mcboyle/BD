"""storage_rebalance API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/storage_rebalance views moved onto a Flask Blueprint.
Endpoint labels gain a "storage_rebalance." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

storage_rebalance_bp = Blueprint("storage_rebalance", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@storage_rebalance_bp.route("/api/storage_rebalance/inventory", methods=["POST"])
def api_storage_inventory():
    """Per-disk usage inventory. Body: {paths: [...]}."""
    body = request.json or {}
    paths = body.get("paths") or []
    if not paths:
        return jsonify({"error": "paths required"}), 400
    try:
        from . import storage_rebalance as _sr
        return jsonify({"inventory": _sr.inventory(paths)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@storage_rebalance_bp.route("/api/storage_rebalance/plan", methods=["POST"])
def api_storage_plan():
    """Generate a rebalance plan. Body: {paths, strategy, max_moves}."""
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
            max_moves=int(body.get("max_moves", 100) or 100),
        ))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@storage_rebalance_bp.route("/api/storage_rebalance/execute", methods=["POST"])
def api_storage_execute():
    """Execute a rebalance plan. Body: {plan, dry_run}.
    Defaults to dry_run=True; set False to actually move files."""
    _check_csrf()
    body = request.json or {}
    plan = body.get("plan") or {}
    if not plan or not plan.get("moves"):
        return jsonify({"ok": False, "error": "plan required"}), 400
    try:
        from . import storage_rebalance as _sr
        return jsonify(_sr.execute_plan(
            plan, dry_run=bool(body.get("dry_run", True))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(storage_rebalance_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("storage_rebalance."))

