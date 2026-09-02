"""app_dev.maint -- 6 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

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



@dev_bp.route("/api/dev/version_check")
def api_dev_version_check():
    """Flag any banner/version string not matching __version__."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.version_consistency())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/import_preflight")
def api_dev_import_preflight():
    """U15/D-89 — preflight a bulk-import CSV/XLSX file before
    importing it (read-only). Param: ?path=<file in the BD home dir>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        path = request.args.get("path") or None
        if not path:
            return jsonify({"error": "?path= is required"}), 400
        existing = [(rn.config or {}).get("login_url")
                    for _sid, rn in _runners_generation(runners)]
        known = None
        try:
            from . import login_templates_data as _lt
            known = [t.get(k) for t in _lt.list_login_templates()
                     for k in ("id", "name", "host")]
        except Exception:
            known = None
        return jsonify(_ds.import_preflight(
            path=path, existing_urls=existing,
            known_login_templates=known))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/tempdir_clean", methods=["POST"])
def api_dev_tempdir_clean():
    """U25/D-114 (A) — remove BD's own stale temp dirs / .lock files.
    Body {dry_run?, min_age_seconds?}. dry_run defaults TRUE — pass
    dry_run:false to actually sweep. POST + CSRF (state-changing)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        body = request.json or {}
        return jsonify(_ds.tempdir_clean(
            dry_run=body.get("dry_run", True),
            min_age_seconds=body.get("min_age_seconds")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/maintenance_mode")
def api_dev_maintenance_mode():
    """T36/D-122 — read-only status: active maintenance windows +
    which actions are paused. Mutating routes are
    /api/dev/maintenance_enable and /api/dev/maintenance_disable."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.maintenance_mode_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/maintenance_enable", methods=["POST"])
def api_dev_maintenance_enable():
    """T36/D-122 mutating — start an immediate maintenance override.
    Body: {actions_paused?: [str], note?: str}. CSRF-gated by the
    global before_request hook.
    Returns {ok, window_id, actions_paused, note}.
    """
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    actions = body.get("actions_paused") or ["workers"]
    if not isinstance(actions, list):
        return jsonify({"ok": False,
                        "error": "actions_paused must be a list"}), 400
    note = body.get("note", "")
    if not isinstance(note, str):
        note = ""
    try:
        from . import maintenance as _mw
        wid = _mw.add_window_now(actions_paused=actions, note=note)
        if wid is None:
            return jsonify({"ok": False,
                            "error": "could not create window"}), 500
        return jsonify({
            "ok": True,
            "window_id": wid,
            "actions_paused": actions,
            "note": note,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/maintenance_disable", methods=["POST"])
def api_dev_maintenance_disable():
    """T36/D-122 mutating — end all active immediate-override windows.
    Does NOT touch real scheduled windows. CSRF-gated.
    Returns {ok, removed}.
    """
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import maintenance as _mw
        n = _mw.end_active_overrides()
        return jsonify({"ok": True, "removed": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
