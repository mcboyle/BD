"""ytdlp_status API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/ytdlp_status views moved onto a Flask Blueprint.
Endpoint labels gain a "ytdlp_status." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

ytdlp_status_bp = Blueprint("ytdlp_status", __name__)


def _app_s_cfg():
    """The live shared s_cfg from app_state (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


# Which per-site config flag selects which extractor backend. A site can use
# several (e.g. aylo with a yt-dlp fallback); a site with none uses the default
# Playwright engine.
_EXTRACTOR_FLAGS = [
    ("aylo", "use_aylo_extractor"),
    ("ytdlp", "use_ytdlp_fallback"),
    ("gallerydl", "use_gallerydl_fallback"),
    ("search", "use_search_extractor"),
]


def extractor_site_map(s_cfg: dict) -> dict:
    """Map each extractor backend to the configured sites that depend on it -- the
    blast radius when that extractor breaks or goes stale. A site appears under
    every backend it enables; a site enabling none lands under 'playwright' (the
    default engine). Pure + read-only."""
    out: dict = {name: [] for name, _flag in _EXTRACTOR_FLAGS}
    out["playwright"] = []
    for sid, cfg in (s_cfg or {}).items():
        cfg = cfg or {}
        entry = {"site_id": sid, "name": cfg.get("name", sid)}
        used = False
        for name, flag in _EXTRACTOR_FLAGS:
            if cfg.get(flag):
                out[name].append(entry)
                used = True
        if not used:
            out["playwright"].append(entry)
    return out


@ytdlp_status_bp.route("/api/ytdlp_status")
def api_ytdlp_status():
    """Return {installed, version, age_days, stale} for the installed
    yt-dlp. Cached for 1 hour internally — cheap to poll."""
    try:
        from . import ytdlp_updater
        return jsonify(ytdlp_updater.status_dict())
    except Exception as e:
        return jsonify({"installed": False, "version": None,
                        "age_days": None, "stale": False,
                        "error": str(e)[:200]})


@ytdlp_status_bp.route("/api/ytdlp/sites_affected")
def api_ytdlp_sites_affected():
    """Read-only blast-radius map: which configured sites depend on each extractor
    backend, so a broken/stale extractor's affected sites are visible at a glance.
    Folds in the current yt-dlp staleness signal for the ytdlp bucket."""
    try:
        s_cfg = _app_s_cfg()
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
    mapping = extractor_site_map(s_cfg)
    ytdlp_stale = False
    try:
        from . import ytdlp_updater
        ytdlp_stale = bool(ytdlp_updater.status_dict().get("stale"))
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "by_extractor": {k: {"count": len(v), "sites": v}
                         for k, v in mapping.items()},
        "ytdlp_stale": ytdlp_stale,
        "total_sites": len(s_cfg or {}),
    })

def register_routes(app) -> int:
    app.register_blueprint(ytdlp_status_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("ytdlp_status."))

