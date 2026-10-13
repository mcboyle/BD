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


def _app_runners():
    """The live shared runners map (same accessor pattern as _app_s_cfg)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


def _site_host(site_cfg):
    """aac-fix F3: the host a domainless pasted cookie belongs to -- the
    site's login_url, else success_url. "" when neither names a host."""
    from urllib.parse import urlsplit
    for key in ("login_url", "success_url"):
        try:
            host = urlsplit(str(site_cfg.get(key) or "")).hostname or ""
        except ValueError:
            host = ""
        if host:
            return host
    return ""


def _parse_summary(parsed):
    """aac-fix r2 (D170 R3): what a refusal may say about a paste -- format,
    count, cookie names and why any were skipped. Never a cookie value."""
    from . import cookie_clipboard as _cc
    return {"format": parsed.get("format"),
            "count": parsed.get("count", 0),
            "names": [_cc._shown_name(c.get("name"))
                      for c in parsed.get("cookies") or []],
            "skipped": parsed.get("skipped") or [],
            "all_attempts": parsed.get("all_attempts") or []}


@cookie_clipboard_bp.route("/api/cookie_clipboard/parse", methods=["POST"])
def api_cookie_clipboard_parse():
    """Parse pasted cookie text (Netscape / JSON / cURL / cookie header)
    and return structured cookies + detected format + confidence."""
    _check_csrf()
    body = request.json or {}
    text = body.get("text", "")
    if not isinstance(text, str):  # aac-fix F7: text=5 was a 500
        return jsonify({"format": None, "cookies": [], "count": 0,
                        "confidence": 0, "error": "text must be a string"}), 400
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
    if not isinstance(text, str):  # aac-fix F7
        return jsonify({"ok": False, "error": "text must be a string"}), 400
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
                            "details": _parse_summary(parsed)}), 400
        # Convert to Playwright format. A header/cURL paste carries no domain:
        # take the site's host (F3); with none, refuse -- Playwright rejects a
        # domainless cookie and with it the whole add_cookies batch.
        unsafe = []
        pw_cookies = _cc.to_playwright_format(
            parsed["cookies"], default_domain=_site_host(site_cfg),
            skipped=unsafe)
        skipped = (parsed.get("skipped") or []) + unsafe
        if not pw_cookies:  # F4: every cookie was unsafe; keep the old jar
            return jsonify({"ok": False,
                            "error": "no usable cookies (all were rejected)",
                            "details": dict(_parse_summary(parsed),
                                            skipped=skipped)}), 400
        if any(not c.get("domain") for c in pw_cookies):
            return jsonify({"ok": False,
                            "error": "pasted cookies have no domain and the "
                                     "site has no login_url to take it from"}), 400
        # F1: store BD's on-disk form (expirationDate), which the loader reads;
        # the Playwright `expires` key alone reloaded as a session cookie.
        try:
            _ck.save_cookies_to_file(cookie_path, _ck.pw_to_json(pw_cookies))
        except _ck.CookieFileNotAJarError as e:  # F12
            return jsonify({"ok": False, "error": str(e)[:200]}), 409
        # F9: publish to the live runner, as an upload does.
        runner = (_app_runners() or {}).get(sid)
        if runner is not None:
            runner.set_cookies_from_file(cookie_path)
        return jsonify({"ok": True,
                        "count": len(pw_cookies),
                        "format": parsed.get("format"),
                        "confidence": parsed.get("confidence"),
                        "path": cookie_path,
                        # D170 R2: a cookie left out is named, never silent
                        "skipped": skipped})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(cookie_clipboard_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("cookie_clipboard."))

