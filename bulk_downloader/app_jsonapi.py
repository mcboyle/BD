"""jsonapi API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/jsonapi views moved onto a Flask Blueprint.
Endpoint labels gain a "jsonapi." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

jsonapi_bp = Blueprint("jsonapi", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@jsonapi_bp.route("/api/jsonapi/probe", methods=["POST"])
def api_jsonapi_probe():
    """v3.43.68: probe a site for HereSphere/DeoVR JSON API support.

    Body: {
        "site_root": "https://members.<site>.com",
        "extra_hosts": ["https://api.<site>api.com", ...]  (optional)
        "cookies": {name: value, ...}                       (optional)
        "user_agent": "..."                                 (optional)
    }

    Returns:
        {ok: True/False,
         heresphere_url: "..." (if found),
         deovr_url: "..."      (if found),
         api_host_override: "..." (if non-default host),
         sample_title: "..."    (sanity check),
         probed_urls: [[url, status], ...],
         error: "..." (on failure)}

    This is what the site-add wizard hits to ask "does this site
    expose either protocol?" The user can then opt-in to the JsonAPI
    extractor with a single button click.
    """
    # v3.66.522 (VR-P04): match the 164 sibling handlers -- bare _check_csrf().
    # The shim forwards *args to app._check_csrf(), which takes ZERO args and
    # returns None on success / a (response, 403) tuple on failure (the
    # early-return-response idiom). The old ``if not _check_csrf(request):``
    # both forwarded ``request`` into the 0-arg callee (TypeError -> 500 every
    # call) and would have 403'd on success (None is falsy).
    _check_csrf()
    try:
        from . import extractors_jsonapi as _jsonapi
    except Exception as e:
        return jsonify({"ok": False, "error": f"module_unavailable: {e}"}), 500
    body = request.json or {}
    site_root = (body.get("site_root") or "").strip()
    if not site_root:
        return jsonify({"ok": False, "error": "site_root required"}), 400
    if not site_root.startswith(("http://", "https://")):
        site_root = "https://" + site_root
    extra_hosts = body.get("extra_hosts") or []
    if not isinstance(extra_hosts, list):
        extra_hosts = []
    cookies = body.get("cookies") or {}
    if not isinstance(cookies, dict):
        cookies = {}
    user_agent = body.get("user_agent") or ""
    # v3.66.541 (F-APP05-01): SSRF guard -- site_root and every extra_hosts entry
    # are request-supplied fetch targets; validate each is publicly routable (via
    # the canonical bulk_downloader.app._is_url_public) before probe_site, which
    # fetches with follow_redirects=True.
    import importlib as _il
    _is_pub = _il.import_module("bulk_downloader.app")._is_url_public
    def _as_url(_h):
        _h = str(_h).strip()
        return _h if _h.startswith(("http://", "https://")) else "https://" + _h
    if not _is_pub(site_root):
        return jsonify({"ok": False,
                        "error": "site_root host is not publicly routable"}), 400
    for _h in extra_hosts:
        if _h and not _is_pub(_as_url(_h)):
            return jsonify({"ok": False,
                            "error": f"extra_hosts entry is not publicly routable: {_h}"}), 400
    try:
        outcome = _jsonapi.probe_site(
            site_root,
            cookies=cookies,
            user_agent=user_agent,
            extra_hosts=extra_hosts,
        )
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"probe_raised: {type(e).__name__}: {str(e)[:200]}",
        }), 500
    return jsonify({
        "ok": outcome.ok,
        "heresphere_url": outcome.heresphere_url,
        "deovr_url": outcome.deovr_url,
        "api_host_override": outcome.api_host_override,
        "sample_title": outcome.sample_title,
        "probed_urls": outcome.probed_urls,
        "error": outcome.error,
    })

def register_routes(app) -> int:
    app.register_blueprint(jsonapi_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("jsonapi."))

