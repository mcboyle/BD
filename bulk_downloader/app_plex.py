"""plex API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/plex views moved onto a Flask Blueprint.
Endpoint labels gain a "plex." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import error_reply as _er

plex_bp = Blueprint("plex", __name__)

# plex-error-redact F5: the most items one ?count= may ask Plex for.
COUNT_MAX = 200

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


def _count_arg(default):
    """(count, None) from ?count=: absent or blank -> ``default``, above
    COUNT_MAX -> COUNT_MAX; (None, 400 response) when it is not an integer
    or is below 1 (0 and negatives are refused, not sliced)."""
    raw = request.args.get("count")
    if raw is None or not raw.strip():
        return default, None
    try:
        n = int(raw.strip())
    except ValueError:
        return None, (jsonify({"error": "count must be an integer"}), 400)
    if n < 1:
        return None, (jsonify({"error": "count must be at least 1"}), 400)
    return min(n, COUNT_MAX), None


def _failed(where, e, cfg=None, **extra):
    """500 answer for a foreign exception: generic text + request id; the
    detail goes to the server log, redacted (never str(e) to the client)."""
    return jsonify(_er.failure_reply(
        f"plex.{where}", e, secrets=[(cfg or {}).get("plex_token")], **extra)), 500


@plex_bp.route("/api/plex_deep/backend_status")
def api_plex_deep_backend_status():
    """Report which Plex backend(s) are available. The raw HTTP
    backend (plex_deep) is always available. The plexapi backend
    is opt-in and requires `pip install plexapi`."""
    info = {"raw_http": {"backend": "plex_deep", "available": True}}
    try:
        from . import plex_deep_plexapi as _pdp
        info["plexapi"] = _pdp.status_dict()
    except Exception as e:
        info["plexapi"] = {"backend": "plexapi", "available": False,
                            "import_error": str(e)[:200]}
    return jsonify(info)
@plex_bp.route("/api/plex_advanced/status")
def api_plex_adv_status():
    try:
        from . import plex_advanced as _pa
        return jsonify(_pa.status_dict())
    except Exception as e:
        return _failed("status", e, available=False)
@plex_bp.route("/api/plex_advanced/server_info/<sid>")
def api_plex_adv_server_info(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    try:
        from . import plex_advanced as _pa
        return jsonify(_pa.server_info(cfg))
    except Exception as e:
        return _failed("server_info", e, cfg, available=True)
@plex_bp.route("/api/plex_advanced/library_stats/<sid>")
def api_plex_adv_library_stats(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    try:
        from . import plex_advanced as _pa
        return jsonify(_pa.library_stats(cfg))
    except Exception as e:
        return _failed("library_stats", e, cfg, available=True)
@plex_bp.route("/api/plex_advanced/recently_added/<sid>")
def api_plex_adv_recently_added(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    count, bad = _count_arg(20)
    if bad:
        return bad
    try:
        from . import plex_advanced as _pa
        return jsonify(_pa.recently_added(cfg, count=count))
    except Exception as e:
        return _failed("recently_added", e, cfg, available=True)
@plex_bp.route("/api/plex_advanced/on_deck/<sid>")
def api_plex_adv_on_deck(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    count, bad = _count_arg(20)
    if bad:
        return bad
    try:
        from . import plex_advanced as _pa
        return jsonify(_pa.on_deck(cfg, count=count))
    except Exception as e:
        return _failed("on_deck", e, cfg, available=True)
@plex_bp.route("/api/plex_advanced/search/<sid>")
def api_plex_adv_search(sid):
    s_cfg = _app_s_cfg()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    count, bad = _count_arg(25)
    if bad:
        return bad
    try:
        from . import plex_advanced as _pa
        query = request.args.get("q", "") or ""
        return jsonify(_pa.search(cfg, query, count=count))
    except Exception as e:
        return _failed("search", e, cfg, available=True)
@plex_bp.route("/api/plex_advanced/mark/<sid>/<int:rating_key>", methods=["POST"])
def api_plex_adv_mark(sid, rating_key):
    """Body: {watched: True/False}."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    cfg = (s_cfg or {}).get(sid)
    if not cfg:
        return jsonify({"error": f"no such site: {sid}"}), 404
    # plex-error-redact F6: the body is a JSON object or the call is refused.
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    try:
        from . import plex_advanced as _pa
        if body.get("watched"):
            return jsonify(_pa.mark_watched(cfg, rating_key))
        else:
            return jsonify(_pa.mark_unwatched(cfg, rating_key))
    except Exception as e:
        return _failed("mark", e, cfg, available=True, ok=False)

def register_routes(app) -> int:
    app.register_blueprint(plex_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("plex."))

