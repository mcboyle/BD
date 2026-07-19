"""app_dev.testci -- 12 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _check_csrf,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/resolution_test")
def api_dev_resolution_test():
    """U20/D-34 — exercise both INV-005 resolution code paths and show
    them side by side (read-only). Optional ?text=<string>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        text = request.args.get("text") or None
        return jsonify(_ds.resolution_scoring_test(text=text))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/test_run_diff")
def api_dev_test_run_diff():
    """U26/D-74 — diff the in-GUI test runner's recent run history
    (outcome + wall-clock per run; read-only). Optional ?limit=<n>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.test_run_diff(
            limit=request.args.get("limit", 10)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/parametrize_fanout")
def api_dev_parametrize_fanout():
    """T1/D-73 — static scan of tests/*.py: how @parametrize fans test
    methods into counted cases, and any module-level misuse the custom
    runner cannot expand. Read-only."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.parametrize_fanout(
            request.args.get("tests_dir", "tests")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/flaky_tests")
def api_dev_flaky_tests():
    """T1/D-70 — diff failures across several stored test_results.json
    artifacts to flag flaky tests. Optional ?artifacts_dir=<path>.
    Read-only; needs >=2 prior run_tests.py --json results."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.flaky_test_detector(
            request.args.get("artifacts_dir", "test_artifacts")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/fixture_site/start", methods=["POST"])
def api_dev_fixture_site_start():
    """T2/D-72 (A) — start a fixture mock site in-process. Body
    {name?, port?} — name is 'site1'/'site2'. POST + CSRF
    (spawns a server thread)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        body = request.json or {}
        from . import dev_suite as _ds
        return jsonify(_ds.fixture_site_start(
            name=body.get("name", "site1"),
            port=body.get("port")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/fixture_site/stop", methods=["POST"])
def api_dev_fixture_site_stop():
    """T2/D-72 (A) — stop a running fixture mock site. Body {name?}.
    POST + CSRF (state-changing)."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.fixture_site_stop(
            name=(request.json or {}).get("name", "site1")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/fixture_site/status")
def api_dev_fixture_site_status():
    """T2/D-72 — which fixture mock sites are running (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.fixture_site_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/job_replay", methods=["POST"])
def api_dev_job_replay():
    """U30/D-18 (A) — replay a single download by history-row id.
    Body {history_id, dry_run?}. dry_run defaults TRUE. POST + CSRF."""
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    try:
        from . import dev_suite as _ds
        body = request.json or {}
        return jsonify(_ds.job_replay(
            history_id=body.get("history_id"),
            dry_run=body.get("dry_run", True)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/golden_files")
def api_dev_golden_files():
    """T42/D-75 — inventory golden files + report drift between
    .golden and .current siblings. Read-only — never regenerates."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.golden_file_manager())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/request_replay_list")
def api_dev_request_replay_list():
    """T44/D-46 — read-only list of captured /api/* requests. Capture
    is gated by the 'request_capture' feature flag (off by default)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        limit = request.args.get("limit", 50)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        return jsonify(_ds.request_replay_list(limit=limit))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/request_replay/<request_id>", methods=["POST"])
def api_dev_request_replay(request_id):
    """T44/D-46 mutating — re-issue a captured request. The replay
    goes through the SAME flask app so all auth/CSRF middleware
    applies. CSRF-gated. Bounded timeout."""
    guard = _dev_mode_guard()
    if guard: return guard
    body = request.json or {}
    try:
        timeout = float(body.get("timeout", 10.0))
    except (TypeError, ValueError):
        timeout = 10.0
    base_url = body.get("base_url", "http://127.0.0.1:5555")
    if not isinstance(base_url, str) or not base_url.startswith("http"):
        return jsonify({"ok": False,
                        "error": "base_url must be an http(s) URL"}), 400
    try:
        from . import request_replay as _rr
        return jsonify(_rr.replay(request_id, base_url=base_url,
                                    timeout=timeout))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/test_timing")
def api_dev_test_timing():
    """T50/D-71 — slowest tests + per-file totals from the latest
    run_tests.py --json artifact. Read-only.

    Query params:
      ?path=<results_path>  (default: test_results.json in cwd)
      ?top=<N>              (default: 30; cap at 200)

    Requires schema_version >= 2 (per-test durations, added by T49
    in v3.63.1+). Older artifacts return ok=false with a clear
    re-run instruction; no fake/zeroed durations are surfaced."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        path = request.args.get("path") or None
        # Cap top to keep the response bounded.
        try:
            top = max(1, min(int(request.args.get("top", 30)), 200))
        except (TypeError, ValueError):
            top = 30
        return jsonify(_ds.test_timing(results_path=path, top=top))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
