"""app_dev.runtime -- 19 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_runners,
    _app_s_cfg,
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/run", methods=["POST"])
def api_dev_run():
    """Start a test run. Body: {target, kind}.
    Returns 202 + run_id."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    body = request.json or {}
    target = (body.get("target") or "").strip()
    kind = (body.get("kind") or "file").strip()
    if not target:
        return jsonify({"ok": False, "error": "target required"}), 400
    try:
        from . import dev_tools as _dt
        return jsonify(_dt.start_run(target, kind=kind)), 202
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/runs/<run_id>")
def api_dev_run_status(run_id):
    """Poll status of a run. The output field grows incrementally as
    the test produces output, so the UI can show a live console."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_tools as _dt
        r = _dt.run_status(run_id)
        if r is None:
            return jsonify({"error": "unknown run_id"}), 404
        # Add parsed summary for the UI's progress display
        r["summary"] = _dt.parse_summary(r.get("output", ""))
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/runs/<run_id>/cancel", methods=["POST"])
def api_dev_run_cancel(run_id):
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_tools as _dt
        return jsonify(_dt.cancel_run(run_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/runs")
def api_dev_runs_recent():
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_tools as _dt
        limit = min(50, max(1, int(request.args.get("limit", 20))))
        return jsonify({"runs": _dt.recent_runs(limit=limit)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/mem_audit")
def api_dev_mem_audit():
    """Memory snapshot. With ?settle=N, instead runs a settle-and-diff
    audit (baseline, N-second idle settle, second snapshot, deltas +
    heuristic findings)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import perf_lab as _pl
        settle = request.args.get("settle")
        if settle is not None:
            return jsonify(_pl.audit(settle_seconds=settle, runners=runners))
        return jsonify(_pl.snapshot(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/mem_audit/track", methods=["POST"])
def api_dev_mem_audit_track():
    """Body {action: "start"|"stop"} — toggle tracemalloc tracking so
    snapshots carry a top-allocations breakdown."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    from . import perf_lab as _pl
    action = ((request.json or {}).get("action") or "").strip().lower()
    if action == "start":
        return jsonify(_pl.tracemalloc_start())
    if action == "stop":
        return jsonify(_pl.tracemalloc_stop())
    return jsonify({"ok": False, "error": "action must be start|stop"}), 400


@dev_bp.route("/api/dev/threads")
def api_dev_threads():
    """Live thread inventory — name, daemon, alive."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.thread_inventory())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/runner_state")
def api_dev_runner_state():
    """Per-site live runner state — queue depth, workers, paused, etc."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.runner_state(runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/proc")
def api_dev_proc():
    """Process fingerprint — pid, uptime, open fds, RSS, threads."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.process_info())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/gc", methods=["POST"])
def api_dev_gc():
    """Run a full garbage collection; report objects freed."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.force_gc())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/stuck_jobs")
def api_dev_stuck_jobs():
    """Queue rows stuck in 'running'. Query param: older_than (sec)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.stuck_jobs(
            request.args.get("older_than", 1800)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/latency")
def api_dev_latency():
    """p50/p95/p99 request latency per route."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.latency_histogram())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/thread_dump")
def api_dev_thread_dump():
    """Full stack trace of every live thread."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.thread_dump())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/runner_console")
def api_dev_runner_console():
    """U30/D-19 — read-only fleet view of every live SiteRunner's
    state, workers, and job counts."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.runner_console())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/systemd_check")
def api_dev_systemd_check():
    """U31/D-101 — validate a BulkDownloader systemd unit (read-only).
    Optional ?path=<unit file>; auto-locates an installed unit if
    omitted."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.systemd_unit_check(
            unit_path=request.args.get("path")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/queue_throughput")
def api_dev_queue_throughput():
    """T4/D-15 — completed-job throughput series bucketed from the
    history table (read-only). Optional ?hours=, ?bucket=hour|day,
    ?site=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.queue_throughput(
            hours=request.args.get("hours", 24),
            bucket=request.args.get("bucket", "hour"),
            site_id=request.args.get("site") or None))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/worker_profile")
def api_dev_worker_profile():
    """T5/D-14 — per-runner worker-thread attribution: count, alive,
    daemon, hung, vs configured max_concurrent (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.worker_thread_profile(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/disk_usage")
def api_dev_disk_usage():
    """T17/D-112 — per-extension, per-age-bucket, per-site bytes
    across configured download_dirs (read-only). Optional
    ?max_files= caps the walk."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.disk_usage_breakdown(
            site_configs=s_cfg,
            max_files=request.args.get("max_files", 20000)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/storage_tier_status")
def api_dev_storage_tier_status():
    """T35/D-117 — StorageTierScheduler state + per-site candidate
    counts (dry-run of find_candidates). Read-only."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.storage_tier_status(site_configs=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
