"""events_all API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/events_all views moved onto a Flask Blueprint.
Endpoint labels gain a "events_all." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_meta) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import json
from flask import Blueprint, jsonify, request

events_all_bp = Blueprint("events_all", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_meta():
    """The live shared s_meta from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_meta")


@events_all_bp.route("/api/events_all")
def api_events_all():
    """Phase 21.4: cross-site event feed. JSON cursor `?after={...}` maps
    site_id → last seen seq. Returns merged events sorted by ts."""
    runners = _app_runners()
    s_meta = _app_s_meta()
    limit = min(int(request.args.get("limit", 200) or 200), 500)
    kind_filter = request.args.get("kind") or None
    # The "after" cursor is a JSON-encoded map of {sid: last_seq}. New
    # clients pass {} and get the most recent N. Subsequent calls pass
    # the returned cursor.
    cursor_raw = request.args.get("after", "{}")
    try:
        cursor = json.loads(cursor_raw) if cursor_raw else {}
        if not isinstance(cursor, dict): cursor = {}
    except Exception:
        cursor = {}
    out = []
    new_cursor = {}
    for sid, runner in runners.items():
        last = int(cursor.get(sid, 0) or 0)
        try:
            evs = runner.get_events(after_seq=last, limit=limit,
                                    kind_filter=kind_filter)
        except Exception:
            evs = []
        new_cursor[sid] = runner._event_seq
        site_name = (s_meta.get(sid) or {}).get("name") or sid
        for e in evs:
            e = dict(e)
            e["site_id"] = sid
            e["site_name"] = site_name
            out.append(e)
    out.sort(key=lambda e: e.get("ts","") or "")
    return jsonify({"ok": True, "events": out[-limit:], "cursor": new_cursor})

def register_routes(app) -> int:
    app.register_blueprint(events_all_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("events_all."))

