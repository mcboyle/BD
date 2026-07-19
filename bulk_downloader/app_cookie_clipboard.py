"""cookie_clipboard API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/cookie_clipboard views moved onto a Flask Blueprint.
Endpoint labels gain a "cookie_clipboard." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

cookie_clipboard_bp = Blueprint("cookie_clipboard", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@cookie_clipboard_bp.route("/api/cookie_clipboard/parse", methods=["POST"])
def api_cookie_clipboard_parse():
    """Parse pasted cookie text (Netscape / JSON / cURL / cookie header)
    and return structured cookies + detected format + confidence."""
    _check_csrf()
    body = request.json or {}
    text = body.get("text", "")
    if not text:
        return jsonify({"format": None, "cookies": [],
                        "count": 0, "confidence": 0,
                        "error": "no text provided"}), 400
    try:
        from . import cookie_clipboard as _cc
        return jsonify(_cc.auto_detect_and_parse(text))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@cookie_clipboard_bp.route("/api/cookie_clipboard/save/<sid>", methods=["POST"])
def api_cookie_clipboard_save(sid):
    """Parse text + save into the configured cookie_file path for site
    `sid`. Returns {ok, count, format, path}."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    text = body.get("text", "")
    site_cfg = (s_cfg or {}).get(sid)
    if not site_cfg:
        return jsonify({"ok": False, "error": f"no such site: {sid}"}), 404
    cookie_path = site_cfg.get("cookie_file", "")
    if not cookie_path:
        return jsonify({"ok": False,
                        "error": "site has no cookie_file configured"}), 400
    try:
        from . import cookie_clipboard as _cc
        from . import cookies as _ck
        parsed = _cc.auto_detect_and_parse(text)
        if not parsed.get("cookies"):
            return jsonify({"ok": False,
                            "error": "could not parse any cookies",
                            "details": parsed}), 400
        # Convert to Playwright format + save
        pw_cookies = _cc.to_playwright_format(parsed["cookies"])
        _ck.save_cookies_to_file(cookie_path, pw_cookies)
        return jsonify({"ok": True,
                        "count": len(pw_cookies),
                        "format": parsed.get("format"),
                        "confidence": parsed.get("confidence"),
                        "path": cookie_path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cookie_clipboard_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cookie_clipboard."))

