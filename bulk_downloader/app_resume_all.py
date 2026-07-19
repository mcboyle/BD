"""resume_all API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/resume_all views moved onto a Flask Blueprint.
Endpoint labels gain a "resume_all." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint

resume_all_bp = Blueprint("resume_all", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _do_action_all(*_a, **_k):
    """Delegate to app._do_action_all at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_do_action_all")(*_a, **_k)


@resume_all_bp.route("/api/resume_all", methods=["POST"])
def api_resume_all(): _check_csrf(); return _do_action_all("resume")

def register_routes(app) -> int:
    app.register_blueprint(resume_all_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("resume_all."))

