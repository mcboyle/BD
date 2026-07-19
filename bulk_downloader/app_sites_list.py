"""sites_list API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/sites_list view moved onto a Flask Blueprint. Endpoint
label gains a "sites_list." prefix; the (rule, methods, bare-name) routing surface
is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached via a _app_s_cfg() accessor
(getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

sites_list_bp = Blueprint("sites_list", __name__)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@sites_list_bp.route("/api/sites_list", methods=["GET"])
def api_sites_list():
    """Lightweight list of all configured sites, used by the browser
    extension to populate its 'send to specific site' submenu and
    the routing preview. Returns just id/name/hostname; no secrets.

    Distinct from /api/status which is heavy (full job dicts) and
    /api/sites/<sid> which is single-site detailed config — this is
    the index a non-trusted-but-paired client needs to render its
    site picker."""
    s_cfg = _app_s_cfg()
    from urllib.parse import urlparse
    out = []
    for sid, cfg in s_cfg.items():
        host = ""
        for fld in ("login_url", "success_url"):
            v = (cfg.get(fld) or "").lower()
            if v:
                try:
                    h = (urlparse(v).hostname or "").lower()
                    if h:
                        host = h
                        break
                except Exception:
                    continue
        out.append({
            "site_id": sid,
            "name": cfg.get("name") or sid,
            "hostname": host,
            "patterns": (cfg.get("url_patterns") or "").strip(),
        })
    # Sort by name so the extension's submenu is stable
    out.sort(key=lambda s: (s.get("name") or "").lower())
    return jsonify({"ok": True, "sites": out})


def register_routes(app) -> int:
    app.register_blueprint(sites_list_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("sites_list."))
