"""changelog API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/changelog views moved onto a Flask Blueprint.
Endpoint labels gain a "changelog." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

changelog_bp = Blueprint("changelog", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@changelog_bp.route("/api/changelog")
def api_changelog_all():
    """Per-site changelog: success-rate deltas, new failure patterns,
    cookie health. Sorted with most-broken sites first. Used by the
    UI to flag 'what changed about my sites this week'."""
    s_cfg = _app_s_cfg()
    try:
        from . import site_changelog as _scl
        return jsonify(_scl.for_all_sites(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@changelog_bp.route("/api/changelog/<sid>")
def api_changelog_site(sid):
    """Detailed changelog for one site."""
    s_cfg = _app_s_cfg()
    try:
        from . import site_changelog as _scl
        return jsonify(_scl.for_site(sid, s_cfg_entry=(s_cfg or {}).get(sid)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(changelog_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("changelog."))

