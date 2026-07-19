"""discovery API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/discovery views moved onto a Flask Blueprint.
Endpoint labels gain a "discovery." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

discovery_bp = Blueprint("discovery", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@discovery_bp.route("/api/discovery/run", methods=["POST"])
def api_discovery_run():
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    sid = body.get("site_id")
    # Local enqueue: append URL to the site's pending queue
    def enqueue(site_id, urls):
        if site_id not in s_cfg or site_id not in runners:
            return 0
        n = 0
        for url in urls[:1000]:  # safety cap
            try:
                runners[site_id].load_urls([url])  # type: ignore  # SiteRunner enqueue is load_urls, not add_url (v3.66.245)
                n += 1
            except Exception:
                pass
        return n
    try:
        from . import discovery as _disc
        if sid:
            cfg = (s_cfg or {}).get(sid, {})
            dcfg = (cfg or {}).get("discovery") or {}
            return jsonify(_disc.discover_one(sid, dcfg, enqueue_fn=enqueue))
        return jsonify({"runs": _disc.discover_all(
            s_cfg, enqueue_fn=enqueue)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/history")
def api_discovery_history():
    try:
        from . import discovery as _disc
        return jsonify({"runs": _disc.recent_runs(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/disco/run", methods=["POST"])
def api_discovery_disco_run():
    """Operator manual trigger for A-DISCO (cut 4b): a run-now. It FORCES a pass
    past the ``auto_disco`` DAILY toggle -- an attended, explicit operator action,
    matching the app's other run-now controls (e.g. drift_repair). The MASTER
    off-switch still dominates (default off_switch_fn = automation_controller's kill
    path -> the per-site pass is inert when engaged), per-site ``disco.enabled``
    still gates which sites run, and the bounded budget + AR4 enqueue cap still
    apply. It does NOT flip the daily toggle; it only runs one pass now."""
    _check_csrf()
    try:
        from . import disco_runner as _dr
        return jsonify(_dr.scheduled_disco(
            s_cfg=_app_s_cfg(), runners=_app_runners(),
            enabled_fn=lambda: True))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@discovery_bp.route("/api/discovery/disco/runs")
def api_discovery_disco_runs():
    """The persisted A-DISCO run history (disco_runner.recent_runs), so the operator
    can see what a manual trigger (or the daily task) did."""
    try:
        from . import disco_runner as _dr
        return jsonify({"runs": _dr.recent_runs(
            limit=int(request.args.get("limit", 50)))})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(discovery_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("discovery."))

