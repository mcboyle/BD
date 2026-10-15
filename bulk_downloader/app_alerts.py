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

_MAX_LOOKBACK_HOURS = 24 * 366

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
    except (TypeError, ValueError):
        hours = 0
    if not 1 <= hours <= _MAX_LOOKBACK_HOURS:
        return jsonify({"error": "hours must be a whole number from 1 to "
                                 f"{_MAX_LOOKBACK_HOURS}"}), 400
    try:
        from . import alerts_engine as _ae
        try:
            return jsonify({"alerts": _ae.active_alerts(lookback_hours=hours)})
        except _ae.AlertEventStoreUnavailable as e:
            # UNKNOWN, not "nothing firing": the badge must not read 0 off a
            # store that could not be read.
            return jsonify({"error": str(e)[:200],
                            "store_unavailable": True}), 503
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@alerts_bp.route("/api/alerts/rules")
def api_alerts_rules():
    """List all configured rules (built-in + user-added). Used by
    the Settings → Alerts pane."""
    s_cfg = _app_s_cfg()
    try:
        from . import alerts_engine as _ae
        rules, store_error = _ae.rules_report(s_cfg=s_cfg)
        body = {"rules": rules}
        if store_error:
            # The built-in rules are still listed; saved rules are UNKNOWN.
            body["store_error"] = store_error
        return jsonify(body)
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
            from .egress_withhold import app_message  # SEC-2: served verbatim, by origin
            return jsonify({"ok": False,
                            "error": app_message("alert-rule-invalid",
                                                 "invalid rule (need an id without '/', a known metric, "
                                                 "op in >=/<=/>/</==, finite numeric threshold, "
                                                 "whole non-negative duration_minutes / "
                                                 "cooldown_minutes)")}), 400
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

@alerts_bp.route("/api/notifications/inbox")
def api_notifications_inbox():
    """F005: the /notifications inbox -- fired alerts with read state.
    ?status=all|unread|read, ?severity=all|info|warn|fail, ?limit=N (<=1000).
    An unknown filter is a 400, an unreadable store a 500 -- never an empty
    list that reads as "no notifications"."""
    try:
        limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    from . import alerts_engine as _ae
    try:
        return jsonify(_ae.notification_inbox(
            status=request.args.get("status", "all"),
            severity=request.args.get("severity", "all"),
            limit=limit))
    except ValueError as e:
        return jsonify({"error": str(e)[:200]}), 400
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@alerts_bp.route("/api/notifications/inbox/<int:event_id>/read", methods=["POST"])
def api_notifications_mark_read(event_id):
    """F005: mark one notification read ({"read": true}) or unread
    ({"read": false}). CSRF-gated; 404 when no fired alert has that id."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    read = body.get("read")
    if not isinstance(read, bool):
        return jsonify({"ok": False, "error": "body needs read: true|false"}), 400
    from . import alerts_engine as _ae
    try:
        if not _ae.set_notification_read(event_id, read):
            return jsonify({"ok": False,
                            "error": f"no notification {event_id}"}), 404
        return jsonify({"ok": True, "id": event_id, "read": read})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(alerts_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("alerts."))

