"""app_cockpit_home.py — additive cockpit landing page + nav map (#1 / P4).

A dashboard LANDING page that groups related features and links the additive
read-only pages, WITHOUT editing the existing cockpit sidebar. Per the plan, the
actual sidebar consolidation is the final-integration pass (operator-applied — see
docs/NAV_CONSOLIDATION.md); this page is purely additive and reversible.

Read-only, no action affordances. Auth piggybacks on app.py's global hooks. Not
wired into app.py (the one-line `register_routes(app)` is the integration step).

NEEDS OPERATOR CLICK-THROUGH VALIDATION.

Wiring (deferred):
    from .app_cockpit_home import register_routes as _reg_home
    _reg_home(app)
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify

cockpit_home_bp = Blueprint("cockpit_home", __name__)
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Information architecture: related features grouped to cut sidebar clutter.
# `kind`: "page" (linkable route), "existing" (existing cockpit entry),
# "cli" (a tool, listed for discoverability, not a link), "artifact" (a file).
NAV = [
    {"group": "Templates", "items": [
        {"label": "Template Manager", "path": "/cockpit/template-manager",
         "kind": "page", "note": "reviewed/drafts/candidates, scoring, drift — new, needs click-through"},
        {"label": "Template Manager API", "path": "/api/template_manager",
         "kind": "existing", "note": "existing list + promote/disable (operator-gated)"},
        {"label": "template_analytics (CLI)", "path": "tools/template_analytics.py",
         "kind": "cli", "note": "tree-wide stats + drift summary"},
    ]},
    {"group": "Monitoring", "items": [
        {"label": "queue_intelligence (CLI)", "path": "tools/queue_intelligence.py",
         "kind": "cli", "note": "queue diagnostics / failure categories / stuck URLs"},
    ]},
    {"group": "Captures", "items": [
        {"label": "capture_analytics (CLI)", "path": "tools/capture_analytics.py",
         "kind": "cli", "note": "artifact inventory + draft/candidate yield"},
    ]},
    {"group": "Reports", "items": [
        {"label": "Report Center", "path": "/cockpit/reports",
         "kind": "page", "note": "index of generated reports — new, needs click-through"},
        {"label": "Report sections API", "path": "/api/report_center/sections",
         "kind": "page", "note": "report center section metadata JSON"},
        {"label": "Data layer API", "path": "/api/data/template_health",
         "kind": "page", "note": "analytics providers: template/capture/queue/release/kb"},
    ]},
    {"group": "Settings", "items": [
        {"label": "Settings Center", "path": "/cockpit/settings",
         "kind": "page", "note": "read-only config overview + gated gui-safe editor"},
        {"label": "Secrets (presence only)", "path": "/cockpit/settings/secrets",
         "kind": "page", "note": "secret presence/classification — never values"},
    ]},
    {"group": "Consoles", "items": [
        {"label": "Framework dashboard", "path": "/framework/",
         "kind": "page", "note": "read-only framework reports"},
        {"label": "Fleet view", "path": "/fleet/",
         "kind": "page", "note": "read-only fleet status"},
        {"label": "Main UI (legacy)", "path": "/",
         "kind": "existing", "note": "the primary downloader UI"},
        {"label": "Main UI (D3 SPA)", "path": "/m2/",
         "kind": "existing", "note": "the React SPA (requires built frontend/dist)"},
    ]},
    {"group": "API & schema", "items": [
        {"label": "OpenAPI export", "path": "/api/openapi.json", "kind": "page",
         "note": "OpenAPI 3.1 generated from the live route map"},
        {"label": "Endpoint catalog", "path": "ENDPOINT_CATALOG.md", "kind": "artifact",
         "note": "human-readable route index"},
        {"label": "Generated schemas", "path": "docs (document_schemas.py)",
         "kind": "cli", "note": "DB + template schema reference"},
    ]},
    {"group": "Dev / release", "items": [
        {"label": "verify_release (CLI)", "path": "tools/verify_release.py", "kind": "cli",
         "note": "version+docs+templates+manifest+tests gate"},
        {"label": "Main cockpit", "path": "/cockpit", "kind": "existing",
         "note": "existing operator console"},
    ]},
]


@cockpit_home_bp.route("/api/cockpit/nav", methods=["GET"])
def api_cockpit_nav():
    return jsonify({"ok": True, "nav": NAV})


# NOTE: the /cockpit/home server-rendered landing page was retired in v3.66.344
# (Phase-4 retired cut) -- the consolidated cockpit console + the SPA replace it.
# /api/cockpit/nav (above) remains the nav source of truth. Its HTML renderers
# (_item_html / _group_html) were removed in the row-113 remainder cut.


def register_routes(app) -> int:
    app.register_blueprint(cockpit_home_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cockpit_home."))
