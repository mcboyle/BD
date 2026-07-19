"""app_dev.db -- 15 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_runners,
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/db_stats")
def api_dev_db_stats():
    """DB tables + row counts, index count, journal mode, file sizes."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.db_overview())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/wal_checkpoint", methods=["POST"])
def api_dev_wal_checkpoint():
    """Flush the WAL sidecar into the main DB. Body {mode} optional —
    PASSIVE | FULL | RESTART | TRUNCATE (default TRUNCATE)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        mode = (request.json or {}).get("mode", "TRUNCATE")
        return jsonify(_ds.wal_checkpoint(mode))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/sql", methods=["POST"])
def api_dev_sql():
    """Run a single read-only SELECT. Body {query, limit?}. Anything
    that is not a lone SELECT is refused. POST + CSRF for hygiene even
    though the query itself cannot mutate the DB."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        body = request.json or {}
        result = _ds.sql_console(body.get("query"),
                                 body.get("limit", 200))
        return jsonify(result), (200 if result.get("ok") else 400)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/integrity")
def api_dev_integrity():
    """On-demand DB quick_check + integrity_check, each timed."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.integrity_check())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/keepers")
def api_dev_keepers():
    """Session-keeper heartbeat / state monitor."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.keeper_monitor())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/duplicate_sites")
def api_dev_duplicate_sites():
    """U16/D-88 — sites that duplicate each other (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.duplicate_sites(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/orphan_rows")
def api_dev_orphan_rows():
    """U16/D-7 — DB rows whose site_id has no config (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.orphan_rows(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/stale_references")
def api_dev_stale_references():
    """U16/D-92 — config refs pointing at something gone (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.stale_references(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/slow_queries")
def api_dev_slow_queries():
    """U23/D-1 — profile the representative hot DB queries and flag
    slow ones (read-only). Optional ?iterations= & ?slow_ms=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.slow_query_profiler(
            iterations=request.args.get("iterations", 5),
            slow_ms=request.args.get("slow_ms", 25.0)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/index_advisor")
def api_dev_index_advisor():
    """U23/D-2 — list DB indexes and EXPLAIN QUERY PLAN each hot query,
    flagging any full table scan (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.index_advisor())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/migration_status")
def api_dev_migration_status():
    """U24/D-4 — migration ledger snapshot: registered/applied/pending
    migrations + schema-drift detection (read-only; never applies)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.migration_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/queue_table")
def api_dev_queue_table():
    """T3/D-6 — inspect the persisted queue table: per-site/per-status
    counts, retry pressure, stuck pending rows, oldest-pending sample
    (read-only). Optional ?site= and ?sample=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.queue_table_inspect(
            site_id=request.args.get("site") or None,
            sample=request.args.get("sample", 10)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/fts_index")
def api_dev_fts_index():
    """T3/D-5 — inspect the history_fts FTS5 index: availability,
    row-count drift vs history, integrity-check, optimize-sentinel age
    (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.fts_index_inspect())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/db_growth")
def api_dev_db_growth():
    """T4/D-10 — DB size breakdown + a forward growth projection from
    the recent history-row arrival rate (read-only). Optional ?days=
    sets the rate look-back window."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.db_growth_report(
            days=request.args.get("days", 14)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/dedup_hashes")
def api_dev_dedup_hashes():
    """T11/D-37 — read-only view of the perceptual-hash registry:
    total entries, duplicate clusters, per-cluster paths (read-only).
    Optional ?top= caps how many clusters are returned."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.dedup_hash_explore(
            top=request.args.get("top", 20)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
