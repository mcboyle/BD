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
        if block and block.get("unknown"):
            info["blocked"] = None
            info["blocklist_status"] = "unknown"
            info["blocklist_error"] = block.get("error", "blocklist unavailable")
        elif block:
            info["blocked"] = True
            info["blocklist_status"] = "measured"
            info["block_reason"] = block.get("reason", "")
        else:
            info["blocklist_status"] = "measured"
    except Exception as e:
        info["blocked"] = None
        info["blocklist_status"] = "unknown"
        info["blocklist_error"] = f"blocklist unavailable: {e}"[:200]
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
    except Exception as e:
        # ext-routing r1 (F8): an unreadable history is not "never downloaded".
        info["known"] = None
        info["history_status"] = "unknown"
        info["history_error"] = f"history unavailable: {e}"[:200]
    try:
        for sid, runner in _runners_generation(runners):
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
        # ext-routing r1 (F2/F3): rank sites with the shared scorer, as
        # route_preview does -- url_patterns is a newline/comma string, and
        # login/success hostnames route too.  A queue-size tie-break alone
        # (score < 1) is not a match.  Lazy: no new static import edge.
        import importlib
        score = getattr(importlib.import_module("bulk_downloader.app"),
                        "_score_url_against_sites")
        cfg_snapshot = list((s_cfg or {}).items())
        best_sid, best_score, _reason = score(url, cfg_snapshot)
        if best_sid and best_score >= 1:
            cfg = dict(cfg_snapshot)[best_sid]
            info["would_route_to"] = {
                "site_id": best_sid,
                "site_name": cfg.get("name", best_sid),
            }
    except Exception:
        pass
    return jsonify(info)

def register_routes(app) -> int:
    app.register_blueprint(extension_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("extension."))
