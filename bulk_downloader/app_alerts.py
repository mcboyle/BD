"""alerts API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/alerts views moved onto a Flask Blueprint.
Endpoint labels gain a "alerts." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

alerts_bp = Blueprint("alerts", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@alerts_bp.route("/api/alerts/active")
def api_alerts_active():
    """Currently firing alerts (last N hours, default 24).
    UI's bell-icon badge reads this for the unread count."""
    try:
        hours = int(request.args.get("hours", 24) or 24)
        from . import alerts_engine as _ae
        return jsonify({"alerts": _ae.active_alerts(lookback_hours=hours)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@alerts_bp.route("/api/alerts/rules")
def api_alerts_rules():
    """List all configured rules (built-in + user-added). Used by
    the Settings → Alerts pane."""
    s_cfg = _app_s_cfg()
    try:
        from . import alerts_engine as _ae
        return jsonify({"rules": _ae.list_rules(s_cfg=s_cfg)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@alerts_bp.route("/api/alerts/evaluate", methods=["POST"])
def api_alerts_evaluate():
    """Force an immediate evaluation pass. Cron also runs this every
    60s via bg_scheduler — this endpoint is for testing rule edits
    without waiting a minute."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import alerts_engine as _ae
        return jsonify(_ae.evaluate(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@alerts_bp.route("/api/alerts/rules", methods=["POST"])
def api_alerts_rule_save():
    """Cut 8: create/update an alert rule (incl. the job-lifecycle rule
    type, metric=bd_job_failures_1h). CSRF-gated; validated against the
    evaluable-metric set; idempotent upsert by rule id."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import alerts_engine as _ae
        rid = _ae.save_rule(body)
        if rid is None:
            return jsonify({"ok": False,
                            "error": "invalid rule (need id, a known metric, "
                                     "op in >=/<=/>/</==, numeric threshold)"}), 400
        return jsonify({"ok": True, "id": rid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@alerts_bp.route("/api/alerts/rules/<rule_id>/remove", methods=["POST"])
def api_alerts_rule_remove(rule_id):
    """Delete a custom alert rule."""
    _check_csrf()
    try:
        from . import alerts_engine as _ae
        return jsonify({"ok": bool(_ae.delete_rule(rule_id))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(alerts_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("alerts."))

