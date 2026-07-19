"""health API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/health{,/checklist,/v2} views moved onto a Flask
Blueprint. Endpoint labels gain a "health." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant diffs
empty).

Shared state (runners, s_cfg, _app_boot_time) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by
reference). app_test_mode() delegates to app at call time. db_conn / healthcheck
/ __version__ are sibling-package names imported directly (identical objects).
"""
from __future__ import annotations

import json
import os
import time

from flask import Blueprint, jsonify

from .db import db_conn

health_bp = Blueprint("health", __name__)

def app_test_mode(*_a, **_k):
    """Delegate to app.app_test_mode at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "app_test_mode")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app__app_boot_time():
    """The live shared _app_boot_time from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_app_boot_time")


@health_bp.route("/api/health")
def api_health():
    _app_boot_time = _app__app_boot_time()
    runners = _app_runners()
    import sqlite3 as _sqlite3
    from . import __version__ as _bd_version
    payload = {
        "ok": True,
        "version": _bd_version,
        "uptime_s": round(time.time() - _app_boot_time, 1),
        "test_mode": app_test_mode(),   # v3.66.317: advisory only (no behavior effect)
    }
    # Queue depth across all sites. Sum-then-report; never iterate twice.
    try:
        total_queued = 0
        total_active = 0
        for r in runners.values():
            try:
                st = r.get_status(light=True)
                total_queued += int(st.get("queued") or 0)
                total_active += int(st.get("active") or 0)
            except Exception:
                # Per-runner status failure shouldn't fail the health probe
                # itself — we report what we can and flag the discrepancy.
                payload["ok"] = False
                payload["degraded"] = "runner_status_error"
        payload["queue_depth"] = total_queued
        payload["active_downloads"] = total_active
        payload["sites_loaded"] = len(runners)
    except Exception as e:
        payload["ok"] = False
        payload["degraded"] = f"queue_query_failed: {type(e).__name__}"
    # DB liveness — single read against a known-cheap table. If the DB
    # is locked or the file is missing, this fails fast and we report ok=False.
    try:
        with db_conn() as cx:
            cx.execute("SELECT 1").fetchone()
        payload["db_ok"] = True
    except _sqlite3.Error as e:
        payload["ok"] = False
        payload["db_ok"] = False
        payload["degraded"] = f"db_error: {type(e).__name__}"
    # B1.3 (post-365): build identity. Read build_info.json from the install
    # dir so the Dashboard can compare the FE-loaded VITE_BUILD_STAMP against
    # the backend build sha. Absent file -> no `build` key (graceful: dev tree
    # or a pre-B1.3 build). Never fails the probe.
    try:
        _bi_dir = os.environ.get("BD_INSTALL_DIR") or os.path.dirname(os.path.dirname(__file__))
        _bi_path = os.path.join(_bi_dir, "build_info.json")
        if os.path.exists(_bi_path):
            with open(_bi_path, encoding="utf-8") as _bf:
                _bi = json.load(_bf)
            if isinstance(_bi, dict) and _bi.get("sha"):
                payload["build"] = {"sha": _bi.get("sha"),
                                    "built_at": _bi.get("built_at")}
    except Exception:
        pass  # build identity is advisory — never break the health probe
    return jsonify(payload), (200 if payload["ok"] else 503)
@health_bp.route("/api/health/checklist")
def api_health_checklist():
    s_cfg = _app_s_cfg()
    try:
        from . import healthcheck as _hc
        return jsonify(_hc.run_checklist(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@health_bp.route("/api/health/v2")
def api_health_v2():
    """SPA-shaped full health surface. Superset of /api/health — adds
    WAL mode, disk free per download dir, Ollama reachability check
    (cached for 30s to avoid hammering the backend), VPN detection
    (best-effort), and last-suite timestamp."""
    _app_boot_time = _app__app_boot_time()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import sqlite3 as _sqlite3
    import time as _t
    from . import __version__ as _bd_version
    payload = {
        "ok": True,
        "version": _bd_version,
        "uptime_s": round(_t.time() - _app_boot_time, 1),
    }
    # Queue + DB liveness — same logic as /api/health.
    try:
        total_queued = total_active = 0
        for r in runners.values():
            try:
                st = r.get_status(light=True)
                total_queued += int(st.get("queued") or 0)
                total_active += int(st.get("active") or 0)
            except Exception:
                payload["ok"] = False
                payload.setdefault("degraded", "runner_status_error")
        payload["queue_depth"] = total_queued
        payload["active_downloads"] = total_active
        payload["sites_loaded"] = len(runners)
    except Exception as e:
        payload["ok"] = False
        payload["degraded"] = f"queue_query_failed: {type(e).__name__}"
    # DB liveness + WAL mode + integrity_check result (cached on app)
    try:
        with db_conn() as cx:
            cx.execute("SELECT 1").fetchone()
            wal_row = cx.execute("PRAGMA journal_mode").fetchone()
            wal_mode = (wal_row[0] if wal_row else "unknown").lower()
        payload["db_ok"] = True
        payload["db_journal_mode"] = wal_mode
    except _sqlite3.Error as e:
        payload["ok"] = False
        payload["db_ok"] = False
        payload["degraded"] = f"db_error: {type(e).__name__}"
        payload["db_journal_mode"] = "unknown"
    # Disk free per download dir — first 5 only (mockup shows
    # aggregate, not per-dir; this is for the Settings → Health pane).
    disks = []
    seen_dirs = set()
    for sid, cfg in s_cfg.items():
        dl = (cfg or {}).get("download_dir") or ""
        if dl and dl not in seen_dirs:
            seen_dirs.add(dl)
            try:
                import shutil as _shutil
                u = _shutil.disk_usage(dl)
                disks.append({
                    "path": dl,
                    "free_gb": round(u.free / (1024 ** 3), 2),
                    "total_gb": round(u.total / (1024 ** 3), 2),
                    "free_pct": round((u.free / u.total) * 100, 1),
                })
            except Exception:
                pass
        if len(disks) >= 5:
            break
    payload["disks"] = disks
    # Ollama reachability — cached 30s. The cache attribute lives on
    # the function so it survives between requests within a process.
    cache = getattr(api_health_v2, "_ollama_cache", None)
    now = _t.time()
    if cache and (now - cache[0] < 30):
        payload["ollama"] = cache[1]
    else:
        ollama_status = {"reachable": False, "model": None,
                          "error": None}
        try:
            import urllib.request as _ur
            req = _ur.Request("http://127.0.0.1:11434/api/tags")
            with _ur.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    import json as _j
                    body = _j.loads(resp.read())
                    ollama_status["reachable"] = True
                    models = [m.get("name") for m in body.get("models", [])]
                    ollama_status["model"] = models[0] if models else None
        except Exception as e:
            ollama_status["error"] = f"{type(e).__name__}"
        payload["ollama"] = ollama_status
        api_health_v2._ollama_cache = (now, ollama_status)
    # Last-suite timestamp — read from SUMMARY.txt if it exists. The
    # SPA's Health pane displays "Last full suite ran at X" without
    # re-running anything.
    payload["last_suite"] = {"available": False}
    try:
        from pathlib import Path as _P
        summary = _P(__file__).parent.parent / "SUMMARY.txt"
        if summary.is_file():
            payload["last_suite"] = {
                "available": True,
                "mtime_ts": int(summary.stat().st_mtime),
                "size_bytes": summary.stat().st_size,
            }
    except Exception:
        pass
    return jsonify(payload), (200 if payload["ok"] else 503)

def register_routes(app) -> int:
    app.register_blueprint(health_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("health."))
