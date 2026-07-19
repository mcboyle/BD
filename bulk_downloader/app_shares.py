"""shares API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/shares views moved onto a Flask Blueprint.
Endpoint labels gain a "shares." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

shares_bp = Blueprint("shares", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@shares_bp.route("/api/shares", methods=["POST"])
def api_shares_create():
    _check_csrf()
    body = request.json or {}
    try:
        from . import shares as _sh
        return jsonify(_sh.create_token(
            scopes=body.get("scopes", []) or [],
            label=body.get("label", ""),
            ttl_hours=(int(body["ttl_hours"]) if body.get("ttl_hours")
                       else None),
            ip_whitelist=body.get("ip_whitelist", ""),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@shares_bp.route("/api/shares")
def api_shares_list():
    try:
        from . import shares as _sh
        return jsonify({"tokens": _sh.list_tokens(),
                        "known_scopes": _sh.KNOWN_SCOPES})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@shares_bp.route("/api/shares/<token_id>", methods=["DELETE"])
def api_shares_revoke(token_id):
    _check_csrf()
    try:
        from . import shares as _sh
        return jsonify({"ok": _sh.revoke_token(token_id)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@shares_bp.route("/api/shares/access_log")
def api_shares_access():
    try:
        from . import shares as _sh
        return jsonify({"log": _sh.recent_access(
            limit=int(request.args.get("limit", 100)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(shares_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("shares."))

