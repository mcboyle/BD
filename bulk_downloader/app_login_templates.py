"""login_templates API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/login_templates views moved onto a Flask Blueprint.
Endpoint labels gain a "login_templates." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

login_templates_bp = Blueprint("login_templates", __name__)


@login_templates_bp.route("/api/login_templates")
def api_login_templates_list():
    """List the available LOGIN templates for the config-form picker.
    Login templates are separate from download/player templates: a
    site selects one of each independently."""
    from . import login_templates_data as _lt
    return jsonify({"ok": True,
                    "login_templates": _lt.list_login_templates()})

def register_routes(app) -> int:
    app.register_blueprint(login_templates_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("login_templates."))

