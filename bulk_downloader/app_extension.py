"""extension API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/extension views moved onto a Flask Blueprint.
Endpoint labels gain a "extension." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn

extension_bp = Blueprint("extension", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@extension_bp.route("/api/extension/lookup_url")
def api_extension_lookup_url():
    """Given a URL, return what BD knows about it. Used by the
    browser extension's live status badge to surface
      • "already downloaded N times"
      • "currently in queue (status: X)"
      • "would route to site Y"
      • "blocked by content_rights"
    without the user needing to open BD."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    info = {"url": url, "known": False}
    try:
        from . import content_rights as _cr
        block = _cr.url_is_blocked(url)
        if block:
            info["blocked"] = True
            info["block_reason"] = block.get("reason", "")
    except Exception:
        pass
    try:
        with db_conn() as cx:
            row = cx.execute("""
                SELECT id, status, filename, file_size, ts, site_id
                FROM history WHERE url = ?
                ORDER BY id DESC LIMIT 1
            """, (url,)).fetchone()
            if row:
                info["known"] = True
                info["history"] = dict(row)
            count_row = cx.execute(
                "SELECT COUNT(*) as c FROM history WHERE url = ?",
                (url,)).fetchone()
            info["history_count"] = count_row["c"] if count_row else 0
    except Exception:
        pass
    try:
        for sid, runner in (runners or {}).items():
            jobs = getattr(runner, "jobs", {}) or {}
            if url in jobs:
                j = jobs[url]
                info["in_queue"] = True
                info["queue_site"] = sid
                info["queue_status"] = j.get("status", "?")
                break
    except Exception:
        pass
    try:
        import re as _re
        # v3.46.4 F9: bound URL + pattern length for regex eval
        url_match = url[:4096]
        for sid, cfg in (s_cfg or {}).items():
            patterns = cfg.get("url_patterns") or []
            for p in patterns:
                if not p:
                    continue
                if len(p) > 512:
                    continue
                try:
                    if _re.search(p, url_match):
                        info["would_route_to"] = {
                            "site_id": sid,
                            "site_name": cfg.get("name", sid),
                        }
                        break
                except _re.error:
                    continue
            if "would_route_to" in info:
                break
    except Exception:
        pass
    return jsonify(info)

def register_routes(app) -> int:
    app.register_blueprint(extension_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("extension."))

