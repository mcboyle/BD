"""rights API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/rights views moved verbatim onto a Flask
Blueprint. Endpoint labels gain a "rights." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant
diffs empty). App-level helpers are reached lazily at call time.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

rights_bp = Blueprint("rights", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@rights_bp.route("/api/rights/blocklist")
def api_rights_blocklist():
    """List all blocklist entries."""
    try:
        from . import content_rights as _cr
        return jsonify({"blocks": _cr.list_blocks()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@rights_bp.route("/api/rights/block_url", methods=["POST"])
def api_rights_block_url():
    """Block a URL pattern. Body: {pattern, reason?, added_by?}."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import content_rights as _cr
        return jsonify(_cr.block_url(
            body.get("pattern", ""),
            reason=body.get("reason", ""),
            added_by=body.get("added_by", "operator")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@rights_bp.route("/api/rights/block_hash", methods=["POST"])
def api_rights_block_hash():
    """Block a perceptual hash."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import content_rights as _cr
        return jsonify(_cr.block_hash(
            body.get("hash_hex", ""),
            reason=body.get("reason", ""),
            added_by=body.get("added_by", "operator")))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@rights_bp.route("/api/rights/remove/<int:bid>", methods=["POST"])
def api_rights_remove(bid):
    _check_csrf()
    try:
        from . import content_rights as _cr
        return jsonify(_cr.remove_block(bid))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@rights_bp.route("/api/rights/audit")
def api_rights_audit():
    try:
        from . import content_rights as _cr
        return jsonify({"entries": _cr.audit_log(
            limit=int(request.args.get("limit", 200) or 200),
            kind=request.args.get("kind") or None)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(rights_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("rights."))

