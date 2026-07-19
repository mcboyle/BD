"""app_dev.lint -- 20 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

Handlers attach to the SHARED dev_bp (imported from .app_dev); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
from flask import Blueprint, jsonify, request
from .app_dev import (
    _app_s_cfg,
    _dev_mode_guard,
    dev_bp,
)


@dev_bp.route("/api/dev/leak_scan")
def api_dev_leak_scan():
    """Scan for resource-leak signals — orphan processes, stale files."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.leak_scan())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/changelog_lint")
def api_dev_changelog_lint():
    """Confirm CHANGELOG.md has a current '## vX.Y.Z' entry."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.changelog_lint())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/bat_lint")
def api_dev_bat_lint():
    """Lint .bat files — ASCII, CRLF, for-loop redirection."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.bat_lint())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/sh_lint")
def api_dev_sh_lint():
    """Lint .sh files — LF endings, executable bit."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.sh_lint())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/zip_manifest")
def api_dev_zip_manifest():
    """Verify a built release zip against the source tree. ?path= ."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.zip_manifest_check(
            request.args.get("path", "")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/backup_check")
def api_dev_backup_check():
    """Verify a backup file, asserting the WAL sidecars. ?path= ."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.backup_check(request.args.get("path", "")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/deadlock_check")
def api_dev_deadlock_check():
    """Heuristic deadlock check — two-snapshot blocked-acquire scan."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.deadlock_detector())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/manifest_probe")
def api_dev_manifest_probe():
    """U21/D-32 — probe an HLS/DASH manifest's structure (read-only;
    an HTTP GET only, downloads no media). Param: ?url=<manifest>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        url = request.args.get("url") or ""
        if not url:
            return jsonify({"error": "?url= is required"}), 400
        return jsonify(_ds.manifest_probe(url=url))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/lockfile_scan")
def api_dev_lockfile_scan():
    """U25/D-118 — report BD's lingering temp dirs and .lock files in
    the system temp dir (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.lockfile_scan())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/guard_status")
def api_dev_guard_status():
    """U26/D-77 — guard/pin-test status from a test_results.json
    artifact (read-only). Optional ?path=<artifact>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.guard_test_status(
            results_path=request.args.get("path")))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/coverage_map")
def api_dev_coverage_map():
    """U26/D-69 — which bulk_downloader modules have a matching test
    file (name-match heuristic; read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.test_coverage_map())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/dependency_audit")
def api_dev_dependency_audit():
    """U27/D-79 — requirements*.txt pin-discipline audit (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.dependency_audit())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/secret_scan")
def api_dev_secret_scan():
    """U27/D-80 — grep the tree for hardcoded-secret shapes (read-only;
    flags shapes, every hit needs a manual review)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.secret_scan(
            max_findings=request.args.get("max", 200)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/sast_summary")
def api_dev_sast_summary():
    """U27/D-78 — surface the tools/ SAST/DAST pipeline's latest
    SUMMARY output (read-only; a viewer, not a scanner)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.sast_summary())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/pin_drift")
def api_dev_pin_drift():
    """U31/D-100 — cross-check requirements.txt against the installed
    environment (declared-vs-installed drift; read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.dependency_pin_drift())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/magic_bytes")
def api_dev_magic_bytes():
    """T10/D-35 — identify a file by its first bytes and flag any
    container-vs-filename mismatch (read-only). The `path` query
    parameter MUST resolve under the configured path_allowlist."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.magic_bytes_check(
            path=request.args.get("path") or ""))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/mp4_metadata")
def api_dev_mp4_metadata():
    """T10/D-36 — walk the top-level MP4 atoms; flag 'no moov' and
    'mdat before moov' (read-only). The `path` query parameter MUST
    resolve under the configured path_allowlist."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.mp4_metadata_inspect(
            path=request.args.get("path") or ""))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/stealth_audit")
def api_dev_stealth_audit():
    """T16/D-50 — per-site UA + stealth configuration audit; flags
    misconfig like use_stealth_library=True without the library
    being importable (read-only)."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.stealth_audit(site_configs=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/dead_css")
def api_dev_dead_css():
    """T34/D-107 — find CSS selectors not referenced in templates or
    JS. Read-only."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.dead_css_finder())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/i18n_coverage")
def api_dev_i18n_coverage():
    """T38/D-108 — i18n catalog coverage vs strings extracted from
    templates. Read-only."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.i18n_coverage())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
