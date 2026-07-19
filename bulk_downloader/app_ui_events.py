"""ui_events API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/ui_events views moved onto a Flask Blueprint.
Endpoint labels gain a "ui_events." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_app_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import time
from flask import Blueprint, jsonify, request

ui_events_bp = Blueprint("ui_events", __name__)

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")


@ui_events_bp.route("/api/ui_events", methods=["POST"])
def api_ui_events():
    """Ingest a batch of UI events from the frontend logger. Events
    arrive every ~2 seconds (or sooner on visibilitychange) so we
    don't hammer the server. Server-side level gating drops events
    above the configured tier — defense against a misbehaving or
    malicious client shipping extreme-mode events to a basic user.

    Body:
      {events: [{ts, category, event, data}, ...]}
    Returns:
      {ok: true, accepted: N, dropped: M}

    Authentication: standard. Goes through _check_token + _check_csrf
    like every other state-changing endpoint. The frontend logger reads
    the X-CSRF-Token from the existing <meta name="csrf-token"> tag.
    """
    _app_cfg = _app__app_cfg()
    from . import ui_events as _uie
    data = request.get_json(silent=True) or {}
    events = data.get("events") or []
    if not isinstance(events, list):
        return jsonify({"ok": False, "error": "events must be an array"}), 400
    if len(events) > 1000:
        # Hard cap: even EXTREME mode shouldn't ship more than this in
        # a single batch. Reject rather than truncate so the client
        # learns it's misbehaving.
        return jsonify({"ok": False, "error": "batch too large (max 1000 events)"}), 413
    tier = _app_cfg.get("ui_logging_level") or "basic"
    accepted, dropped = _uie.ingest(events, tier)
    return jsonify({"ok": True, "accepted": accepted, "dropped": dropped})


@ui_events_bp.route("/api/ui_events/download")
def api_ui_events_download():
    """Stream the current ui_events.log file to the user as a download.
    Used by the "Download log" button in the Settings UI. Only the
    current day's log; rotated daily files stay on disk and can be
    fetched manually from $INSTALL_DIR/ui_events.log.YYYY-MM-DD."""
    from . import ui_events as _uie
    path = _uie.get_log_path()
    if not path.exists():
        return jsonify({"ok": False, "error": "no log file yet — UI events haven't been recorded"}), 404
    from flask import send_file
    # send_file with as_attachment forces a download dialog rather than
    # rendering the file inline in the browser.
    return send_file(str(path.resolve()),
                     as_attachment=True,
                     download_name=f"ui_events_{time.strftime('%Y-%m-%d')}.log",
                     mimetype="text/plain")

def register_routes(app) -> int:
    app.register_blueprint(ui_events_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("ui_events."))

