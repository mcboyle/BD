"""account_pool API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/account_pool views moved onto a Flask Blueprint.
Endpoint labels gain a "account_pool." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

account_pool_bp = Blueprint("account_pool", __name__)


@account_pool_bp.route("/api/account_pool/status_all")
def api_account_pool_status_all():
    """Global view across every site's pool. Used by the dashboard
    when the user wants to see why downloads are slow."""
    try:
        from bulk_downloader import account_pool as _ap
        return jsonify({"pools": _ap.get_all_pools_status()})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(account_pool_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("account_pool."))

