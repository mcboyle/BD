"""shortcuts API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/shortcuts views moved onto a Flask Blueprint.
Endpoint labels gain a "shortcuts." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

shortcuts_bp = Blueprint("shortcuts", __name__)


@shortcuts_bp.route("/api/shortcuts")
def api_shortcuts_list():
    """Catalog of all keyboard shortcuts. UI renders this as the
    ? help modal. Context filter narrows to a specific area
    (e.g. ?context=queue returns only queue-scoped shortcuts)."""
    try:
        context = request.args.get("context", "")
        from . import shortcuts as _sc
        return jsonify({
            "shortcuts": (_sc.merged_with_overrides() if not context
                          else _sc.list_shortcuts(context=context)),
            "contexts": _sc.contexts(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(shortcuts_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("shortcuts."))

