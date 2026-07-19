"""runs API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/runs views moved onto a Flask Blueprint.
Endpoint labels gain a "runs." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from . import run_history as _run_history

runs_bp = Blueprint("runs", __name__)


@runs_bp.route("/api/runs", methods=["GET"])
def api_runs():
    """Most-recent-first list of tracked download runs (read-only).

    Cut 4: optional ?status=<state> filter (e.g. failed) for the JobErrorModal
    grouping; each row carries reason_code.
    """
    try:
        limit = min(int(request.args.get("limit", 200) or 200), 500)
    except (TypeError, ValueError):
        limit = 200
    status = request.args.get("status") or None
    return jsonify({"ok": True,
                    "runs": _run_history.list_runs(limit=limit, status=status)})


@runs_bp.route("/api/runs/<int:run_id>/timeline", methods=["GET"])
def api_run_timeline(run_id):
    """A single run + its ordered event timeline (read-only). 404 with a JSON
    envelope when the run id is unknown (distinct from a route-absent 404)."""
    run = _run_history.get_run(run_id)
    if run is None:
        return jsonify({"ok": False, "error": "run not found", "run_id": run_id}), 404
    return jsonify({"ok": True, "run": run,
                    "events": _run_history.get_timeline(run_id)})

def register_routes(app) -> int:
    app.register_blueprint(runs_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("runs."))

