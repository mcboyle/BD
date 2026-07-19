"""weather API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/weather views moved onto a Flask Blueprint.
Endpoint labels gain a "weather." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

weather_bp = Blueprint("weather", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@weather_bp.route("/api/weather")
def api_weather_all():
    """Current weather (green/yellow/red) for every configured site.
    UI renders this as a status grid + 'is site X down?' badge."""
    s_cfg = _app_s_cfg()
    try:
        from . import site_weather as _sw
        return jsonify({"sites": _sw.all_statuses(s_cfg=s_cfg)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@weather_bp.route("/api/weather/<sid>")
def api_weather_site(sid):
    """Current weather + recent probe history for one site."""
    try:
        from . import site_weather as _sw
        return jsonify(_sw.site_status(sid))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@weather_bp.route("/api/weather/probe/<sid>", methods=["POST"])
def api_weather_probe_now(sid):
    """Force an immediate probe of one site. Useful for the operator
    to verify a fix without waiting for the next scheduled probe."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    try:
        from . import site_weather as _sw
        return jsonify(_sw.probe_one(sid, cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(weather_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("weather."))

