"""app_dev.config -- 18 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app__app_cfg,
    _app_runners,
    _app_s_cfg,
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/enabled")
def api_dev_enabled():
    """Always available — tells the UI whether to show the Dev tab."""
    from . import dev_tools as _dt
    return jsonify({"enabled": _dt.is_dev_mode()})


@dev_bp.route("/api/dev/env")
def api_dev_env():
    """Effective values of the BD_* env flags + behavioural markers."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.effective_settings())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config")
def api_dev_config():
    """Live app + per-site config, secrets redacted."""
    _app_cfg = _app__app_cfg()
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_dump(_app_cfg, s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_check")
def api_dev_config_check():
    """Validate per-site config — empty configs, dup URLs, cred refs."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_integrity(s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/rate_limits")
def api_dev_rate_limits():
    """Per-site rate-limit cooldown state."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.rate_limit_state(runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_audit")
def api_dev_config_audit():
    """U15/D-86 — fleet-wide site-config schema audit (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_schema_audit(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/path_allowlist_test")
def api_dev_path_allowlist_test():
    """U27/D-82 — exercise the real _validate_path against probe paths
    (read-only; reports allowlist mode + per-path accept/reject)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.path_allowlist_test())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_reload", methods=["POST"])
def api_dev_config_reload():
    """U28/D-119 (A) — re-read app_config.json and re-apply it without
    a process restart. POST + CSRF (state-changing)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_hot_reload())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/cache_clear", methods=["POST"])
def api_dev_cache_clear():
    """U28/D-123 (A) — drop BD's in-process caches. Body {targets?} —
    a comma list or array; default all. POST + CSRF (state-changing)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.cache_clear(
            targets=(request.json or {}).get("targets")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_snapshot", methods=["POST"])
def api_dev_config_snapshot():
    """U29/D-90 (A) — snapshot the live app config. Body {name?}.
    POST + CSRF (writes a snapshot file)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_snapshot(
            name=(request.json or {}).get("name")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_snapshots")
def api_dev_config_snapshots():
    """U29/D-90 — list available config snapshots (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_snapshot_list())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_restore", methods=["POST"])
def api_dev_config_restore():
    """U29/D-90 (A) — restore a named config snapshot. Body {name}.
    POST + CSRF (mutates the live config)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_restore(
            name=(request.json or {}).get("name")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_snapshot_diff")
def api_dev_config_snapshot_diff():
    """D-90 — diff the live app config against a named snapshot (read-only;
    secret values redacted). Query: ?name=<snapshot>. GET (no mutation)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.config_snapshot_diff(
            name=request.args.get("name")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/retry_schedule")
def api_dev_retry_schedule():
    """T5/D-13 — per-site auto-retry schedule + live scheduled retries
    (read-only)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.retry_schedule_inspect(runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/filename_template")
def api_dev_filename_template():
    """T12/D-40 — preview filename-template rendering. With ?template=,
    runs the named template through fname.resolve_filename_template
    with a sample context. Without ?template=, shows the catalog of
    known variables + per-variable rendering demo (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.filename_template_preview(
            template=request.args.get("template")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/feature_flags")
def api_dev_feature_flags():
    """T40/D-121 — read-only list of all feature flags + values.
    Mutating routes are /api/dev/feature_flag_set and
    /api/dev/feature_flag_delete."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.feature_flags_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/feature_flag_set", methods=["POST"])
def api_dev_feature_flag_set():
    """T40/D-121 mutating — set a feature flag. Body: {name, value}.
    Value must be a literal bool. CSRF-gated."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    name = body.get("name")
    value = body.get("value")
    # JSON booleans round-trip as Python bools. If the caller sent
    # something else (e.g. the string "true") reject — silent coercion
    # is what set_flag refuses too.
    try:
        from . import feature_flags as _ff
        return jsonify(_ff.set_flag(name, value))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/feature_flag_delete", methods=["POST"])
def api_dev_feature_flag_delete():
    """T40/D-121 mutating — remove a feature flag. Body: {name}.
    Idempotent. CSRF-gated."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    name = body.get("name")
    try:
        from . import feature_flags as _ff
        return jsonify(_ff.delete_flag(name))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
