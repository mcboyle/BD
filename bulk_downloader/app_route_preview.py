"""route_preview API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/route_preview views moved onto a Flask Blueprint.
Endpoint labels gain a "route_preview." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

route_preview_bp = Blueprint("route_preview", __name__)

def _score_url_against_sites(*_a, **_k):
    """Delegate to app._score_url_against_sites at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_score_url_against_sites")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


def _runners_generation(mapping):
    """A stable (sid, runner) list; locked when `mapping` is the live registry."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_generation")(mapping)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@route_preview_bp.route("/api/route_preview", methods=["POST", "GET"])
def api_route_preview():
    """Dry-run routing: given URL(s), return which site each would
    route to and why, without actually queueing.

    POST {"urls": [...]} OR {"url": "..."} OR GET ?url=...

    Returns:
      {
        "ok": true,
        "decisions": [
          {
            "url": "...",
            "site_id": "wow",
            "site_name": "Wow Girls",
            "score": 200,
            "reason": "matched url_patterns: wowgirls\\.com",
            "already_in_queue": false,
            "would_be_added": true
          },
          ...
        ]
      }

    Used by the extension popup to show "this URL would go to <site>"
    before the user clicks Send. Also useful as a debugging endpoint
    for the user to test routing rules without contaminating the
    queue."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")
    if not urls:
        single = body.get("url") or request.args.get("url")
        urls = [single] if single else []
    urls = [str(u).strip() for u in (urls or []) if str(u).strip().startswith("http")]
    if not urls:
        return jsonify({"ok": False, "error": "no http URLs provided"}), 400
    cfg_snapshot = list(s_cfg.items())
    # Pre-compute existing queue membership for quick lookup
    in_queue = set()
    for _sid, runner in _runners_generation(runners):
        try:
            with runner._lock:
                for u in runner.jobs:
                    in_queue.add(u)
        except Exception:
            continue
    decisions = []
    for u in urls:
        best_sid, best_score, reason = _score_url_against_sites(u, cfg_snapshot)
        already = u in in_queue
        if best_score > 0 and best_sid:
            decisions.append({
                "url": u,
                "site_id": best_sid,
                "site_name": s_cfg.get(best_sid, {}).get("name") or best_sid,
                "score": round(best_score, 2),
                "reason": reason,
                "already_in_queue": already,
                "would_be_added": not already,
            })
        else:
            decisions.append({
                "url": u,
                "site_id": None,
                "site_name": None,
                "score": 0,
                "reason": "no matching site (would go to default_quick_add_site or be unrouted)",
                "already_in_queue": already,
                "would_be_added": False,
            })
    return jsonify({"ok": True, "decisions": decisions})

def register_routes(app) -> int:
    app.register_blueprint(route_preview_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("route_preview."))

