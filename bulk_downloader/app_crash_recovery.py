"""crash_recovery API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/crash_recovery views moved onto a Flask Blueprint.
Endpoint labels gain a "crash_recovery." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

crash_recovery_bp = Blueprint("crash_recovery", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@crash_recovery_bp.route("/api/crash_recovery/scan")
def api_crash_recovery_scan():
    """List orphan .part files across all sites. Files <24h old or
    in an active job map are excluded."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    try:
        from . import crash_recovery as _cr
        return jsonify({
            "orphans": _cr.scan_for_orphans(
                s_cfg=s_cfg, runners=runners),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@crash_recovery_bp.route("/api/crash_recovery/stats")
def api_crash_recovery_stats():
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    try:
        from . import crash_recovery as _cr
        return jsonify(_cr.stats(s_cfg=s_cfg, runners=runners))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@crash_recovery_bp.route("/api/crash_recovery/delete", methods=["POST"])
def api_crash_recovery_delete():
    """Delete an orphan .part + sidecar. Body: {path}."""
    _check_csrf()
    body = request.json or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        from . import crash_recovery as _cr
        return jsonify(_cr.delete_orphan(path))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@crash_recovery_bp.route("/api/crash_recovery/ignore", methods=["POST"])
def api_crash_recovery_ignore():
    """Mark an orphan as 'leave alone' so future scans skip it.
    Body: {path, site_id?}."""
    _check_csrf()
    body = request.json or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        from . import crash_recovery as _cr
        return jsonify({
            "ok": _cr.mark_decision(path, "ignore",
                                     site_id=body.get("site_id", "")),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@crash_recovery_bp.route("/api/crash_recovery/resume", methods=["POST"])
def api_crash_recovery_resume():
    """Re-enqueue the URL associated with an orphan .part. The
    runner's existing resume logic detects the .part on disk +
    picks up where the crashed worker left off. Body: {path}."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        from . import crash_recovery as _cr
        # Get the URL from the sidecar
        from pathlib import Path as _Path
        meta = _cr._read_meta_sidecar(_Path(path))
        url = (meta or {}).get("url") or ""
        if not url:
            return jsonify({"ok": False,
                            "error": "no URL in sidecar — can't resume"}), 200
        # Find the runner for this URL's site
        # (the orphan record carries site_id; look it up)
        orphans = _cr.scan_for_orphans(s_cfg=s_cfg, runners=runners)
        sid = next((o["site_id"] for o in orphans if o["path"] == path), None)
        if not sid or sid not in runners:
            return jsonify({"ok": False,
                            "error": "site for this orphan not found "
                                     "or not running"}), 200
        # Re-enqueue. v3.66.8: was `runner.add_urls(...)` — no such
        # method on SiteRunner; the real one is `load_urls(...)`. The
        # whole block is wrapped in `try/except Exception` below, so
        # the bug presented as a silent 500 every time crash-recovery
        # tried to resume an orphan. Caught during v3.66.8 #4 audit.
        runner = runners[sid]
        added, dupes, skipped = runner.load_urls([url])
        _cr.mark_decision(path, "resumed", site_id=sid)
        return jsonify({"ok": True, "url": url, "site_id": sid,
                        "added": added, "dupes": dupes,
                        "skipped": skipped})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(crash_recovery_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("crash_recovery."))

