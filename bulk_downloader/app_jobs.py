"""jobs API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/jobs/stuck view moved onto a Flask Blueprint.
Endpoint label gains a "jobs." prefix; the (rule, methods, bare-name) routing
surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached via _app_<name>()
accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request


def _runners_generation(mapping):
    """A stable (sid, runner) list; locked when `mapping` is the live registry.

    Row 634: walking ``app_state.runners`` bare raises ``RuntimeError:
    dictionary changed size during iteration`` the instant a site create or
    delete lands mid-walk, AFTER the loop body has already acted on a prefix of
    the fleet.  Imported lazily (importlib, per call) for the same reason the
    other shared-state accessors here are: no new static import edge.
    """
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_generation")(mapping)


jobs_bp = Blueprint("jobs", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@jobs_bp.route("/api/jobs/stuck")
def api_jobs_stuck():
    """List jobs that have made no progress for `?minutes=` (default 30).

    Scans every runner's in-memory job table for entries in an active
    state whose `last_progress_at` is older than the threshold."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import time as _t
    try:
        threshold_min = float(request.args.get("minutes", "30"))
    except ValueError:
        threshold_min = 30.0
    threshold_s = threshold_min * 60.0
    now = _t.time()
    # States that *should* be making progress — a job sitting here with
    # a stale last_progress_at is genuinely stuck. 'pending' is excluded
    # (it's waiting its turn, not stuck) and so are terminal states.
    ACTIVE_STATES = {"running", "downloading", "processing", "active"}
    stuck = []
    for sid, runner in _runners_generation(runners):
        jobs = getattr(runner, "jobs", {}) or {}
        site_name = ""
        try:
            site_name = (s_cfg.get(sid, {}) or {}).get("name") or sid
        except Exception:
            site_name = sid
        for url, job in list(jobs.items()):
            if not isinstance(job, dict):
                continue
            if job.get("status") not in ACTIVE_STATES:
                continue
            last = float(job.get("last_progress_at", 0) or 0)
            if last <= 0:
                continue
            idle_s = now - last
            if idle_s >= threshold_s:
                stuck.append({
                    "site_id": sid,
                    "site_name": site_name,
                    "url": url,
                    "status": job.get("status"),
                    "idle_seconds": round(idle_s, 1),
                    "idle_minutes": round(idle_s / 60.0, 1),
                    "filename": job.get("filename", ""),
                    "retries": job.get("retries", 0),
                })
    # Most-stuck first
    stuck.sort(key=lambda j: j["idle_seconds"], reverse=True)
    return jsonify({"ok": True, "stuck": stuck, "count": len(stuck),
                    "threshold_minutes": threshold_min})


def register_routes(app) -> int:
    app.register_blueprint(jobs_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("jobs."))
