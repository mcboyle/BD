"""shares API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/shares views moved onto a Flask Blueprint.
Endpoint labels gain a "shares." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

shares_bp = Blueprint("shares", __name__)
ACCESS_LOG_MAX = 1000   # /api/shares/access_log rows per call; a negative SQLite LIMIT is every row

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@shares_bp.route("/api/shares", methods=["POST"])
def api_shares_create():
    refused = _check_csrf()   # returns a 403 response to refuse; it does not abort
    if refused is not None:
        return refused
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400
    if not all(isinstance(body.get(k, ""), str) for k in ("label", "ip_whitelist")):
        return jsonify({"ok": False, "error": "label and ip_whitelist must be strings"}), 400
    ttl_hours = None
    if body.get("ttl_hours") is not None:
        try:
            if isinstance(body["ttl_hours"], bool):
                raise TypeError("invalid ttl_hours")
            ttl_hours = float(body["ttl_hours"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ttl_hours must be positive"}), 400
        if (not math.isfinite(ttl_hours)
                or ttl_hours <= 0):
            return jsonify({"ok": False, "error": "ttl_hours must be positive"}), 400
    try:
        from . import shares as _sh
        scope_err = _sh.scope_error(body.get("scopes"))
        if scope_err:
            return jsonify({"ok": False, "error": scope_err}), 400
        return jsonify(_sh.create_token(
            scopes=body.get("scopes"),
            label=body.get("label", ""),
            ttl_hours=ttl_hours,
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
    refused = _check_csrf()   # returns a 403 response to refuse; it does not abort
    if refused is not None:
        return refused
    try:
        from . import shares as _sh
        return jsonify({"ok": _sh.revoke_token(token_id)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@shares_bp.route("/api/shares/access_log")
def api_shares_access():
    raw = request.args.get("limit", "100")
    # ASCII digits only and bounded: int() also takes "1_0", " 5" and non-ASCII digits
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 9) or int(raw) < 1:
        return jsonify({"error": "limit must be a positive integer"}), 400
    try:
        from . import shares as _sh
        return jsonify({"log": _sh.recent_access(
            limit=min(int(raw), ACCESS_LOG_MAX))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(shares_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("shares."))

