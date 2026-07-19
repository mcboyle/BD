"""app_dev.extract -- 14 @dev_bp route handlers, sub-sliced from app_dev.py (Tier M, pure motion).

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


@dev_bp.route("/api/dev/discover")
def api_dev_discover():
    """List all test files + their test classes/functions (AST parse,
    no execution)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_tools as _dt
        return jsonify(_dt.discover_tests())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/load", methods=["GET", "POST"])
def api_dev_load():
    """GET — current load-injection status. POST {action,...} where
    action is start | stop | purge. start also needs {profile,
    magnitude}; purge frees all held synthetic load."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    from . import perf_lab as _pl
    if request.method == "GET":
        return jsonify(_pl.injection_status())
    _check_csrf()
    body = request.json or {}
    action = (body.get("action") or "start").strip().lower()
    if action == "start":
        return jsonify(_pl.start_injection(
            body.get("profile"), body.get("magnitude", 0), runners=runners))
    if action == "stop":
        return jsonify(_pl.stop_injection())
    if action == "purge":
        return jsonify(_pl.purge(runners=runners))
    return jsonify({"ok": False,
                    "error": "action must be start|stop|purge"}), 400


@dev_bp.route("/api/dev/template_audit")
def api_dev_template_audit():
    """Structural validation of all registered login templates."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.template_audit())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/deep_detect", methods=["POST"])
def api_dev_deep_detect():
    """v3.66.3 — run the full deep-detect pipeline against an HTML
    document and return the ranked report.

    v3.66.4: optional `live=True` mode runs deep_detect_live, which
    layers HTTP probes (HEAD sniffing, meta-refresh follow, optional
    async-workflow polling) on top of the offline detector.

    Body (JSON):
        {
            "html":                "<html>...</html>",        # required
            "base_url":            "https://example.com/",    # optional
            "prefer_resolution":   "highest" | "1080p" | "4k" | "8k" | ...
            "live":                false (default) | true,
            "probe_headers":       {"Cookie": "...", ...},    # optional
            "max_probes":          10,                         # optional
            "probe_timeout":       5.0,
            "probe_parallelism":   4,                          # v3.66.10
            "max_total_probe_time":12.0,                       # v3.66.10
            "follow_manifests":    true,                       # v3.66.10
            "max_manifest_bytes":  1048576,                    # v3.66.10
            "poll_async_workflows": false,
            "poll_max_attempts":   20,
            "poll_interval":       2.0,
        }

    Returns the deep_detect() (or deep_detect_live) dict — see
    bulk_downloader/deep_detect.py for the full schema. On bad input
    returns 400 with an error message; on internal failure returns 500
    with the exception type and a truncated message.

    Truncations:
      • html body is capped at 4 MiB to keep the worker from being
        DoS'd by a malicious payload — Flask enforces this globally,
        oversized bodies are rejected with status 413 before this
        function runs.
      • Response candidate lists are NOT truncated; the caller can
        ask for less if the response is too large for their UI.

    Live-mode rules (when live=True):
      • Probes are NOT skipped automatically when the offline detector
        reports a page-level blocker (DRM, CAPTCHA). v3.66.6 relaxed
        this — the blocker markers are heuristic (a blog post about
        Widevine triggers the same marker as an actual Widevine
        player), so we probe anyway and surface a `disclaimer` field
        in `report["probes"]` explaining what was detected. The
        operator decides whether the disclaimer matters.
      • poll_async_workflows defaults to False even in live mode —
        the caller has to opt in per-call. POST polling executes a
        real form submission and may have side effects on the target.
      • probe_headers (cookies, auth, UA) are passed through verbatim.
    """
    guard = _dev_mode_guard()
    if guard: return guard
    _check_csrf()
    body = request.json or {}
    html = body.get("html") or ""
    if not isinstance(html, str):
        return jsonify({"ok": False,
                        "error": "html must be a string"}), 400
    if len(html) > 4 * 1024 * 1024:
        return jsonify({
            "ok": False,
            "error": (f"html body too large "
                      f"({len(html)} bytes); cap is 4 MiB"),
        }), 413
    base_url = (body.get("base_url") or "").strip()
    prefer = (body.get("prefer_resolution") or "highest").strip()
    live = bool(body.get("live") or False)
    try:
        from . import deep_detect as _dd
        if live:
            report = _dd.deep_detect_live(
                html,
                base_url=base_url,
                prefer_resolution=prefer,
                probe_headers=body.get("probe_headers") or None,
                max_probes=int(body.get("max_probes") or 10),
                probe_timeout=float(body.get("probe_timeout") or 5.0),
                poll_async_workflows=bool(
                    body.get("poll_async_workflows") or False),
                poll_max_attempts=int(
                    body.get("poll_max_attempts") or 20),
                poll_interval=float(body.get("poll_interval") or 2.0),
                # v3.66.10: parallel probing on by default. Empirically
                # ~4–5x faster for typical pages (5–10 candidates).
                # Caller can override or disable via probe_parallelism=1.
                probe_parallelism=int(
                    body.get("probe_parallelism") or 4),
                # Global wall-clock budget across all HEAD probes —
                # protects against a slow page tanking the request.
                # `max_probes × probe_timeout` is the worst-case in
                # serial; with parallelism=4 the cap rarely fires, but
                # we still set one to bound the request latency.
                max_total_probe_time=float(
                    body.get("max_total_probe_time") or 12.0),
                # v3.66.10: manifest-following on by default. When a
                # candidate is an HLS/DASH manifest, the analyzer
                # fetches and parses it to inject per-resolution
                # variants into the candidate list. Caller can opt
                # out via follow_manifests=false.
                follow_manifests=bool(
                    body.get("follow_manifests", True)),
                max_manifest_bytes=int(
                    body.get("max_manifest_bytes") or 1_048_576),
            )
        else:
            report = _dd.deep_detect(
                html, base_url=base_url, prefer_resolution=prefer)
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }), 500


@dev_bp.route("/api/dev/dispatch_chain")
def api_dev_dispatch_chain():
    """U5/D-11 — the ordered _process_one dispatch chain (read-only).
    Optional ?site=<id> annotates which branches that site enables."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        site = request.args.get("site") or None
        return jsonify(_ds.dispatch_chain(site_id=site, runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/dispatch_dryrun")
def api_dev_dispatch_dryrun():
    """U5/D-20 — trace which _process_one branch a URL routes to,
    downloading nothing. Params: ?url=<url>&site=<id>."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        url = request.args.get("url") or ""
        site = request.args.get("site") or None
        return jsonify(_ds.dispatch_dry_run(url, site_id=site,
                                            runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/extractor_matrix")
def api_dev_extractor_matrix():
    """U19/D-31 — the library-extractor capability matrix (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.extractor_matrix())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/extractor_fastpath")
def api_dev_extractor_fastpath():
    """U19/D-39 — simulate the library-extractor fast path for a URL
    (read-only). Params: ?url=<url>&site=<id> (site optional)."""
    runners = _app_runners()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        url = request.args.get("url") or ""
        site = request.args.get("site") or None
        return jsonify(_ds.extractor_fastpath_sim(
            url, site_id=site, runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/ffmpeg_preview")
def api_dev_ffmpeg_preview():
    """U20/D-33 — preview the ffmpeg argv for a streaming URL without
    running it (read-only). Params: ?url=<url>&output=<name>."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        url = request.args.get("url") or ""
        output = request.args.get("output") or "preview.mp4"
        return jsonify(_ds.ffmpeg_command_preview(url,
                                                  output_name=output))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/manual_takeover_log")
def api_dev_manual_takeover_log():
    """T6/D-28 — classified timeline of manual-login / takeover events
    extracted from the app log (read-only). Optional ?limit=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.manual_takeover_log(
            limit=request.args.get("limit", 200)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/prompt_preview")
def api_dev_prompt_preview():
    """T7/D-52 — catalog the AI prompt templates and render filled
    previews with sample values (read-only). Optional ?name=."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.prompt_preview(
            name=request.args.get("name") or None))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/partials")
def api_dev_partials():
    """T11/D-38 — find .part files (resumable-download tempfiles) in
    every configured site's download_dir, reporting size, age, and
    .part.meta sidecar presence (read-only). Optional ?max_files="""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.partial_download_finder(
            site_configs=s_cfg,
            max_files=request.args.get("max_files", 500)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/captcha_relay")
def api_dev_captcha_relay():
    """T15/D-48 — captcha-relay queue grouped by type + status,
    plus whether the runner-provided takeover callbacks are wired
    (read-only)."""
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.captcha_relay_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@dev_bp.route("/api/dev/download_scan")
def api_dev_download_scan():
    """T17/D-113 — scan download_dirs for anomalies (zero-byte
    files, tiny files, orphan .part files, duplicate filenames
    across sites). Read-only."""
    s_cfg = _app_s_cfg()
    guard = _dev_mode_guard()
    if guard: return guard
    try:
        from . import dev_suite as _ds
        return jsonify(_ds.download_folder_scan(
            site_configs=s_cfg,
            max_files=request.args.get("max_files", 20000)))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
