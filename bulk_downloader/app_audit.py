"""audit API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/audit views moved onto a Flask Blueprint.
Endpoint labels gain a "audit." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/api/audit/recent")
def api_audit_recent():
    from . import audit as _audit
    limit = request.args.get("limit", "100")
    try:
        limit_int = max(1, min(int(limit), 500))
    except ValueError:
        limit_int = 100
    rows = _audit.audit_recent(limit_int)
    return jsonify({"ok": True, "rows": rows, "count": len(rows)})


@audit_bp.route("/api/audit/for_target")
def api_audit_for_target():
    """Filter audit events by target prefix. Pass `prefix=sites_config:abc123`
    to see everything that ever happened to a specific site."""
    from . import audit as _audit
    prefix = request.args.get("prefix", "").strip()
    if not prefix:
        return jsonify({"ok": False, "error": "prefix required"}), 400
    limit = request.args.get("limit", "50")
    try:
        limit_int = max(1, min(int(limit), 500))
    except ValueError:
        limit_int = 50
    rows = _audit.audit_for_target(prefix, limit_int)
    return jsonify({"ok": True, "rows": rows, "count": len(rows)})

def register_routes(app) -> int:
    app.register_blueprint(audit_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("audit."))

