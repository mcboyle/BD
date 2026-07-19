"""api_tokens API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/api_tokens views moved onto a Flask Blueprint.
Endpoint labels gain a "api_tokens." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

api_tokens_bp = Blueprint("api_tokens", __name__)


@api_tokens_bp.route("/api/api_tokens", methods=["POST"])
def api_api_tokens_create():
    body = request.get_json(silent=True) or {}
    scope = (body.get("scope") or "").strip()
    from . import api_tokens as _apitok
    if scope not in _apitok.SCOPES:
        return jsonify({"ok": False,
                        "error": "scope must be one of: read, "
                                 "enqueue, admin"}), 400
    res = _apitok.create_token(
        scope=scope,
        label=(body.get("label") or "")[:200],
        ttl_hours=body.get("ttl_hours"),
    )
    return jsonify(res), (200 if res.get("ok") else 400)


@api_tokens_bp.route("/api/api_tokens")
def api_api_tokens_list():
    from . import api_tokens as _apitok
    return jsonify({"ok": True, "tokens": _apitok.list_tokens()})


@api_tokens_bp.route("/api/api_tokens/<token_id>", methods=["DELETE"])
def api_api_tokens_revoke(token_id):
    from . import api_tokens as _apitok
    return jsonify({"ok": _apitok.revoke_token(token_id)})

def register_routes(app) -> int:
    app.register_blueprint(api_tokens_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("api_tokens."))

