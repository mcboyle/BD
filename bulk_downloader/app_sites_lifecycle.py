"""app_sites.lifecycle -- 9 @sites_bp route handlers, sub-sliced from app_sites.py (Tier M, pure motion).

Handlers attach to the SHARED sites_bp (imported from .app_sites); the routing surface
(rule, methods, bare-name) is byte-identical -- test_route_map_invariant diffs EMPTY.
"""
from __future__ import annotations
import os as _os
import json
import os
import re
import sys
import time
import uuid
from flask import Blueprint, Response, jsonify, request
from pathlib import Path
from .constants import SCREENSHOTS_DIR
from .runner import SiteRunner
from .runner import _ts
from datetime import datetime
from .db import db_search
from .db import queue_upsert
from .app_sites import (
    _app_runners,
    _app_s_cfg,
    _chk,
    _do_action,
    _oi_dir_writable,
    _save_sites_config,
    _start_session_keepers,
    sites_bp,
)


@sites_bp.route("/api/sites/<sid>/readiness", methods=["GET"])
def api_site_readiness(sid):
    """Read-only composite readiness for one site (Cut 4) -> green/amber/red +
    'fix this' hints. The deep per-signal version stays in the Cockpit."""
    s_cfg = _app_s_cfg()
    if sid not in s_cfg:
        return jsonify({"ok": False, "error": "unknown site", "site_id": sid}), 404
    cfg = s_cfg.get(sid) or {}
    checks = []
    fixes = []
    # config completeness
    if not cfg.get("login_url"):
        checks.append(_chk("login_url", "Login URL", "warn", "no login URL set"))
        fixes.append("Set this site's login URL.")
    else:
        checks.append(_chk("login_url", "Login URL", "ok", "configured"))
    dl = cfg.get("download_dir")
    if not dl:
        checks.append(_chk("download_dir", "Download directory", "warn", "not set"))
        fixes.append("Set a download directory for this site.")
    else:
        exists, writable = _oi_dir_writable(dl)
        if exists and writable:
            checks.append(_chk("download_dir", "Download directory", "ok", f"{dl} writable"))
        else:
            checks.append(_chk("download_dir", "Download directory", "fail",
                               f"{dl} {'not writable' if exists else 'missing'}"))
            fixes.append(f"Create or fix permissions on {dl}.")
    # auth health for this site
    try:
        from . import cookie_health as _ch
        info = (_ch.status_all() or {}).get(sid) if isinstance(_ch.status_all(), dict) else None
        if isinstance(info, dict):
            blob = " ".join(str(info.get(k, "")) for k in ("status", "state", "class")).lower()
            if any(m in blob for m in ("expired", "unhealthy")):
                checks.append(_chk("auth_health", "Auth health", "fail", "credentials need refresh"))
                fixes.append("Re-login / refresh this site's credentials.")
            else:
                checks.append(_chk("auth_health", "Auth health", "ok", "healthy"))
    except Exception:
        pass
    # drift for this site
    try:
        from . import selector_drift as _sd
        st = _sd.status_for(sid) if hasattr(_sd, "status_for") else {}
        blob = " ".join(str((st or {}).get(k, "")) for k in ("status", "state")).lower()
        if "stale" in blob or "drift" in blob:
            checks.append(_chk("selector_drift", "Selector drift", "warn", "drift-stale"))
            fixes.append("Re-teach selectors for this site.")
    except Exception:
        pass

    if any(ch["status"] == "fail" for ch in checks):
        level = "red"
    elif any(ch["status"] == "warn" for ch in checks):
        level = "amber"
    else:
        level = "green"
    return jsonify({"ok": True, "site_id": sid, "level": level,
                    "checks": checks, "fixes": fixes})


@sites_bp.route("/api/sites/<sid>/watch/scan_now", methods=["POST"])
def api_watch_scan_now(sid):
    """Force an immediate scan of the configured watch folder for
    this site. Useful for testing the setup without waiting for the
    next poll cycle. Returns a summary of files processed."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    folder = (cfg.get("watch_folder") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "watch_folder not configured"})
    try:
        from bulk_downloader import watch_folder as _wf
        runner = runners[sid]
        files = _wf.scan_once(folder)
        results = []
        priority = (cfg.get("watch_url_priority") or "normal").strip().lower()
        for f in files:
            r = _wf.process_file(f, runner, priority=priority)
            results.append({
                "file": f.name,
                "ok": r["ok"],
                "urls_imported": r["urls_imported"],
                "errors": r["errors"][:5],
                "moved_to": r["moved_to"],
            })
        return jsonify({"ok": True, "scanned": len(files),
                         "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"})


@sites_bp.route("/api/sites/<sid>/watch/status")
def api_watch_status(sid):
    """Show watch-folder status: enabled, folder, last few processed
    + failed file names. Used by the UI status panel."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    if sid not in runners: return jsonify({"error": "Not found"}), 404
    cfg = s_cfg.get(sid, {})
    folder = (cfg.get("watch_folder") or "").strip()
    result = {
        "enabled": bool(cfg.get("watch_enabled")),
        "folder": folder,
        "poll_seconds": int(cfg.get("watch_poll_seconds") or 30),
        "priority": cfg.get("watch_url_priority") or "normal",
        "folder_exists": False,
        "pending_files": 0,
        "recent_processed": [],
        "recent_failed": [],
    }
    if not folder:
        return jsonify(result)
    try:
        from pathlib import Path as _P
        p = _P(folder)
        result["folder_exists"] = p.is_dir()
        if p.is_dir():
            # Pending (top-level .txt files not yet processed)
            result["pending_files"] = sum(
                1 for f in p.iterdir()
                if f.is_file() and f.suffix.lower() == ".txt"
                and not f.name.startswith("."))
            # Recent processed
            proc = p / ".processed"
            if proc.is_dir():
                items = sorted(proc.iterdir(),
                                key=lambda f: f.stat().st_mtime,
                                reverse=True)[:5]
                result["recent_processed"] = [f.name for f in items]
            fail = p / ".failed"
            if fail.is_dir():
                items = sorted(fail.iterdir(),
                                key=lambda f: f.stat().st_mtime,
                                reverse=True)[:5]
                result["recent_failed"] = [f.name for f in items
                                            if not f.name.endswith(".log")]
    except OSError as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return jsonify(result)


@sites_bp.route("/api/sites/<sid>/reconnect", methods=["POST"])
def api_site_reconnect(sid):
    """Force the keep-alive checker to run immediately for this site.
    Returns 404 if the site or its keeper doesn't exist. Body may
    contain {"account_idx": N} to target a specific account, otherwise
    triggers all accounts for the site."""
    s_cfg = _app_s_cfg()
    from . import session_keeper as _sk
    if sid not in s_cfg:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    acc_idx = body.get("account_idx")
    triggered = []
    if acc_idx is not None:
        if _sk.force_check(sid, int(acc_idx)):
            triggered.append(int(acc_idx))
    else:
        # All accounts for this site
        for k in _sk.get_status():
            if k["site_id"] == sid:
                if _sk.force_check(sid, k["account_idx"]):
                    triggered.append(k["account_idx"])
    return jsonify({"ok": True, "triggered": triggered, "count": len(triggered)})


@sites_bp.route("/api/sites/<sid>/keep_alive_toggle", methods=["POST"])
def api_site_keep_alive_toggle(sid):
    """Enable / disable keep-alive for a site. Body: {"enabled": bool}.
    Persists to sites_config and respawns or stops the keeper."""
    s_cfg = _app_s_cfg()
    from . import session_keeper as _sk
    if sid not in s_cfg:
        return jsonify({"ok": False, "error": "unknown site"}), 404
    body = request.json or {}
    enabled = bool(body.get("enabled", True))
    s_cfg[sid]["keep_alive_enabled"] = enabled
    _save_sites_config()
    if not enabled:
        # Stop any keepers for this site
        for k in _sk.get_status():
            if k["site_id"] == sid:
                _sk.stop_keeper(sid, k["account_idx"])
    else:
        # Spawn keepers (re-uses the helper at startup)
        _start_session_keepers(site_id=sid)
    return jsonify({"ok": True, "enabled": enabled})


@sites_bp.route("/api/sites/<sid>/start",  methods=["POST"])
def api_start(sid):  return _do_action(sid, "start")


@sites_bp.route("/api/sites/<sid>/pause",  methods=["POST"])
def api_pause(sid):  return _do_action(sid, "pause")


@sites_bp.route("/api/sites/<sid>/resume", methods=["POST"])
def api_resume(sid): return _do_action(sid, "resume")


@sites_bp.route("/api/sites/<sid>/stop",   methods=["POST"])
def api_stop(sid):   return _do_action(sid, "stop")
