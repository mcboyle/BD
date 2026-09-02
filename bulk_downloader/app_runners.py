"""runners API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/runners/{pause_all,resume_all} views moved onto a
Flask Blueprint. Endpoint labels gain a "runners." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant diffs
empty).

Shared state (runners) is owned by app.py and reached via a _app_runners()
accessor (getattr, fresh per call -- same object by reference). sse_broker is a
sibling-package module imported directly.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

runners_bp = Blueprint("runners", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


def _runners_snapshot():
    """One stable generation of (sid, runner), captured under the registry lock.

    Both views below act on every runner they enumerate, so they must walk a
    snapshot: a site created or deleted mid-walk otherwise raises RuntimeError at
    the `for` statement -- after stop()/start() has already been applied to a
    prefix -- and the count the operator relies on never arrives.
    """
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_snapshot")()


@runners_bp.route("/api/runners/pause_all", methods=["POST"])
def api_runners_pause_all():
    """Pause every site's runner (stops dequeueing new URLs; in-flight
    jobs are NOT cancelled, they run to completion). Returns the count
    of sites paused."""
    paused = 0
    failures = []
    for sid, runner in _runners_snapshot():
        try:
            runner.stop()
            paused += 1
        except Exception as e:
            failures.append({"site_id": sid, "error": str(e)[:200]})
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "op": "pause_all", "count": paused,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "paused": paused, "failures": failures})


@runners_bp.route("/api/runners/resume_all", methods=["POST"])
def api_runners_resume_all():
    """Inverse of pause_all — restart all paused runners. Idempotent
    (already-running runners are skipped)."""
    resumed = 0
    failures = []
    for sid, runner in _runners_snapshot():
        try:
            if getattr(runner, "_state", "") != "running":
                runner.start()
                resumed += 1
        except Exception as e:
            failures.append({"site_id": sid, "error": str(e)[:200]})
    try:
        from . import sse_broker as _sse
        _sse.publish("queue_change", {
            "op": "resume_all", "count": resumed,
        })
    except Exception:
        pass
    return jsonify({"ok": True, "resumed": resumed, "failures": failures})


def register_routes(app) -> int:
    app.register_blueprint(runners_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("runners."))
