"""bulk API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/bulk views moved onto a Flask Blueprint.
Endpoint labels gain a "bulk." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bulk_bp = Blueprint("bulk", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


@bulk_bp.route("/api/bulk/enqueue", methods=["POST"])
def api_bulk_enqueue():
    """Cut 8: enqueue a BATCH of URLs on a configured site, tracked via the
    existing run path. Body: {site_id, urls:[...]}. Validated + CSRF-gated.
    Idempotency on double-submit is inherent -- SiteRunner.load_urls de-dupes
    against the live queue (a resubmit reports dupes, adds 0). Never touches
    capture/extraction; it only hands URLs to the same per-site enqueue path
    add_url uses. Returns {ok, site_id, requested, added, dupes, skipped}."""
    runners = _app_runners()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    sid = (body.get("site_id") or "").strip()
    urls = body.get("urls")
    if not sid:
        return jsonify({"ok": False, "error": "site_id required"}), 400
    if not isinstance(urls, list) or not urls:
        return jsonify({"ok": False,
                        "error": "urls must be a non-empty list"}), 400
    if sid not in runners or not runners[sid]:
        return jsonify({"ok": False,
                        "error": f"unknown site_id {sid!r}"}), 400
    clean = [str(u).strip() for u in urls if str(u).strip()]
    CAP = 1000  # safety cap, matches discovery / capture_schedules
    overflow = max(0, len(clean) - CAP)
    clean = clean[:CAP]
    try:
        added, dupes, skipped = runners[sid].load_urls(clean)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }), 500
    return jsonify({
        "ok": True, "site_id": sid, "requested": len(urls),
        "added": int(added), "dupes": int(dupes),
        "skipped": int(skipped) + overflow,
    })

def register_routes(app) -> int:
    app.register_blueprint(bulk_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("bulk."))

