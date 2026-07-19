"""accounts API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/accounts views moved onto a Flask Blueprint.
Endpoint labels gain a "accounts." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

accounts_bp = Blueprint("accounts", __name__)


@accounts_bp.route("/api/accounts/health")
def api_accounts_health():
    try:
        from . import account_health as _ah
        return jsonify({
            "rows": _ah.report_all(),
            "summary": _ah.summary(),
        })
    except Exception as e:
        return jsonify({"rows": [], "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(accounts_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("accounts."))

