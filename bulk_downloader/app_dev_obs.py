"""app_dev.obs -- 11 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_app,
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/routes")
def api_dev_routes():
    """Every registered Flask route — path, methods, endpoint."""
    app = _app_app()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.route_map(app))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/logtail")
def api_dev_logtail():
    """Last ?n lines (default 200, max 5000) of the app log."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.log_tail(request.args.get("n", 200)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/invariants")
def api_dev_invariants():
    """Runtime audit of checkable DANGER_MAP invariants."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.invariant_audit())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/log_level", methods=["GET", "POST"])
def api_dev_log_level():
    """GET — current log level. POST {level} — set it (reversible)."""
    guard = _dev_mode_guard()
    if guard: return guard
    from . import dev_suite as _ds
    if request.method == "GET":
        return jsonify(_ds.get_log_level())
    _check_csrf()
    level = (request.json or {}).get("level", "")
    result = _ds.set_log_level(level)
    return jsonify(result), (200 if result.get("ok") else 400)


@dev_bp.route("/api/dev/sse_status")
def api_dev_sse_status():
    """Live SSE broker state — connected client count and ages."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.sse_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/log_search")
def api_dev_log_search():
    """Search the app log by level / logger / text. Query params:
    level, logger, contains, limit."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.log_search(
            level=request.args.get("level"),
            logger=request.args.get("logger"),
            contains=request.args.get("contains"),
            limit=request.args.get("limit", 200)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/route_timing")
def api_dev_route_timing():
    """OBS-2 — per-route latency percentiles (count/p50/p95/max) over the recent
    request buffer, for the status-page timing panel. Read-only."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.route_timing())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/slow_endpoints")
def api_dev_slow_endpoints():
    """Requests over a latency threshold. Query param: threshold_ms."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.slow_endpoints(
            request.args.get("threshold_ms", 1000)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/error_rate")
def api_dev_error_rate():
    """4xx/5xx counts per route."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.error_rate())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/exceptions")
def api_dev_exceptions():
    """Recent unhandled request exceptions. Query param: limit."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.exception_log(request.args.get("limit", 50)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/event_tap")
def api_dev_event_tap():
    """U22/D-65 — recent SSE events captured from the broker's publish
    path (read-only ring buffer). Optional ?limit=<n>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        limit = request.args.get("limit", "50")
        return jsonify(_ds.event_tap(limit=limit))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/event_tap_ui")
def api_dev_event_tap_ui():
    """U22/D-105 — the standalone SSE-event-tap dev page. Self-contained
    HTML; does not load or modify the shared static/app.js."""
    guard = _dev_mode_guard()
    if guard: return guard
    from . import dev_suite as _ds
    return _ds.event_tap_ui_html()
