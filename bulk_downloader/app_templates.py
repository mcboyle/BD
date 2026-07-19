"""templates API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/templates views moved onto a Flask Blueprint.
Endpoint labels gain a "templates." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

templates_bp = Blueprint("templates", __name__)


@templates_bp.route("/api/templates")
def api_templates_list():
    """Return the template library, optionally filtered to those matching
    a given URL/hostname (passed as ?url=...). Used by the new-site
    modal's template picker."""
    from . import templates as _tpls
    url = request.args.get("url", "")
    all_tpls = _tpls.list_templates()
    suggested = set(_tpls.suggest_for_url(url)) if url else set()
    for t in all_tpls:
        t["suggested"] = t["id"] in suggested
    return jsonify({"ok": True, "templates": all_tpls})

def register_routes(app) -> int:
    app.register_blueprint(templates_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("templates."))

