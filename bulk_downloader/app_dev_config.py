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


def _json_object_body():
    """A POST body as a dict: {} when the body is empty, None when it is
    anything but a JSON object (list, string, number, null, form data,
    malformed JSON). The route answers None with _bad_body()."""
    if not request.get_data(cache=True):
        return {}
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _bad_body():
    return jsonify({"ok": False,
                    "error": "request body must be a JSON object"}), 400


# Substrings of a tool's error that mean the server failed, not the request.
_SERVER_FAULTS = ("write failed", "cannot create state dir",
                  "restore failed", "app module unavailable",
                  "import failed")


def _result_status(res):
    """HTTP status for a dev tool's result dict, so a client that reads
    only the status code never takes a refusal for success: 200 unless
    ok is False; then 404 for a missing snapshot, 500 for a server-side
    fault, else 400."""
    if not isinstance(res, dict) or res.get("ok") is not False:
        return 200
    err = str(res.get("error") or res.get("verdict") or "")
    if err.startswith("no snapshot named"):
        return 404
    if any(h in err for h in _SERVER_FAULTS):
        return 500
    return 400


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
    body = _json_object_body()
    if body is None:
        return _bad_body()
    targets = body.get("targets")
    if not (targets is None or isinstance(targets, str)
            or (isinstance(targets, list)
                and all(isinstance(t, str) for t in targets))):
        return jsonify({"ok": False,
                        "error": "targets must be a string or a list of "
                                 "strings"}), 400
    try:
        from . import dev_suite as _ds
        res = _ds.cache_clear(targets=targets)
        return jsonify(res), _result_status(res)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/config_snapshot", methods=["POST"])
def api_dev_config_snapshot():
    """U29/D-90 (A) — snapshot the live app config. Body {name?}.
    POST + CSRF (writes a snapshot file)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    body = _json_object_body()
    if body is None:
        return _bad_body()
    try:
        from . import dev_suite as _ds
        res = _ds.config_snapshot(name=body.get("name"))
        return jsonify(res), _result_status(res)
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
    body = _json_object_body()
    if body is None:
        return _bad_body()
    try:
        from . import dev_suite as _ds
        res = _ds.config_restore(name=body.get("name"))
        return jsonify(res), _result_status(res)
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
        res = _ds.config_snapshot_diff(name=request.args.get("name"))
        return jsonify(res), _result_status(res)
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


def _flag_actor() -> str:
    """The caller's credential class (app._request_actor_class), resolved on
    the LIVE app module at call time."""
    try:
        import importlib
        return importlib.import_module(
            "bulk_downloader.app")._request_actor_class()
    except Exception:
        return "none"


def _log_flag_change(op, name, value, actor, result):
    """Audit line for every feature-flag write: name, actor class, ts."""
    import logging
    import time as _time
    logging.getLogger("bulk_downloader.feature_flags").warning(
        "feature_flag_change op=%s name=%r value=%r actor=%s result=%s ts=%.3f",
        op, str(name)[:64], value, actor, result, _time.time())


def _is_replayed_request() -> bool:
    """r2 (PM F2a): True iff this request bears the request_replay marker
    header, i.e. it is a replay of a captured request."""
    from .request_replay import REPLAY_MARKER_HEADER
    return request.headers.get(REPLAY_MARKER_HEADER) is not None


def _refuse_replayed_capture_change(op, name, value, actor):
    """A replayed request never changes request_capture (it would arrive from
    loopback and launder the gate below). 403 + audit line, else None."""
    if name != "request_capture" or not _is_replayed_request():
        return None
    _log_flag_change(op, name, value, actor, "refused-replay")
    return jsonify({"ok": False,
                    "error": "request_capture cannot be changed by a "
                             "replayed request"}), 403


@dev_bp.route("/api/dev/feature_flag_set", methods=["POST"])
def api_dev_feature_flag_set():
    """T40/D-121 mutating — set a feature flag. Body: {name, value}.
    Value must be a literal bool. CSRF-gated."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = _json_object_body()
    if body is None:
        return _bad_body()
    name = body.get("name")
    value = body.get("value")
    actor = _flag_actor()
    refused = _refuse_replayed_capture_change("set", name, value, actor)
    if refused: return refused
    # request-capture-redact: capture records every /api/ request, so turning
    # it ON is a master-level act -- loopback or the master token only. A
    # session (any source, pair_redeem included) or a same-origin Referer,
    # which pass _dev_mode_guard, do not. Turning it OFF stays open.
    if name == "request_capture" and value is True \
            and actor not in ("loopback", "master_token"):
        _log_flag_change("set", name, value, actor, "refused")
        return jsonify({"ok": False,
                        "error": "request_capture can only be turned on from "
                                 "loopback or with the master token"}), 403
    # JSON booleans round-trip as Python bools. If the caller sent
    # something else (e.g. the string "true") reject — silent coercion
    # is what set_flag refuses too.
    try:
        from . import feature_flags as _ff
        res = _ff.set_flag(name, value)
        _log_flag_change("set", name, value, actor,
                         "ok" if res.get("ok") else "failed")
        return jsonify(res), _result_status(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/feature_flag_delete", methods=["POST"])
def api_dev_feature_flag_delete():
    """T40/D-121 mutating — remove a feature flag. Body: {name}.
    Idempotent. CSRF-gated."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = _json_object_body()
    if body is None:
        return _bad_body()
    name = body.get("name")
    refused = _refuse_replayed_capture_change("delete", name, None, _flag_actor())
    if refused: return refused
    try:
        from . import feature_flags as _ff
        res = _ff.delete_flag(name)
        _log_flag_change("delete", name, None, _flag_actor(),
                         "ok" if res.get("ok") else "failed")
        return jsonify(res), _result_status(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
