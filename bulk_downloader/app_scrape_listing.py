"""scrape_listing API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/scrape_listing view moved onto a Flask Blueprint.
Endpoint label gains a "scrape_listing." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

_is_url_public() delegates to app at call time (lazy; avoids an import cycle).

FIX (v3.66.446, was DEFERRED_FIXES.md FIX-1): the body uses `httpx.Client(...)`,
but app.py never imported httpx -- so before this fix a public-URL POST that passed
the SSRF gate raised NameError -> 500 (a latent bug present in 437-445; no test
covered the path). The cut-442 extraction PRESERVED that behavior byte-identically
(pure motion); this is the separate RED-first fix that restores the endpoint:
`import httpx` below makes the call resolve. Covered by
tests/test_v3_66_446_scrape_listing_httpx.py.
"""
from __future__ import annotations

import httpx

from flask import Blueprint, jsonify, request

scrape_listing_bp = Blueprint("scrape_listing", __name__)

def _is_url_public(*_a, **_k):
    """Delegate to app._is_url_public at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_is_url_public")(*_a, **_k)


@scrape_listing_bp.route("/api/scrape_listing", methods=["POST"])
def api_scrape_listing():
    """Phase 71 (v3.43.16): server-side listing-page scrape. Paste a
    listing-page URL, get back the video-looking links found in its HTML.

    Body: {"url": "...", "max": 100?, "filter_listings": true?}
    Returns: {"ok", "url", "found": [...], "count", "html_size"}

    Heuristic only — looks for anchors with video-like hrefs (extensions
    or path patterns). For sites with JS-rendered listings, this won't
    find anything; the user should fall back to the browser extension's
    Phase 77 scrape-page action."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"ok": False, "error": "missing or invalid url"}), 400
    # AUDIT FIX (v3.43.16): SSRF defence. The original endpoint blindly
    # fetched any user-supplied URL with follow_redirects=True. An auth'd
    # user could use it to probe internal services (10.0.0.0/8, 127.0.0.1,
    # 169.254.169.254 metadata, etc.) and exfiltrate the response bodies.
    # We validate the URL's resolved IP and reject anything that lands in
    # a private/loopback/link-local/reserved range.
    if not _is_url_public(url):
        return jsonify({"ok": False, "error": "URL must point to a public host"}), 400
    max_links = int(body.get("max") or 200)
    max_links = max(1, min(max_links, 500))
    # Use httpx with a real-browser User-Agent
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        # AUDIT FIX: disable redirect-following so we don't get bounced
        # at internal services via 302. If a user needs redirects they
        # can pass the final URL.
        with httpx.Client(timeout=30.0, follow_redirects=False) as cl:
            r = cl.get(url, headers=headers)
            if r.status_code in (301, 302, 303, 307, 308):
                return jsonify({"ok": False,
                                "error": f"URL returned {r.status_code} redirect — pass the final URL directly"}), 400
            r.raise_for_status()
            # AUDIT FIX: cap response size to prevent OOM on a malicious large response.
            html = r.text
            if len(html) > 5 * 1024 * 1024:
                html = html[: 5 * 1024 * 1024]
    except httpx.HTTPError as e:
        return jsonify({"ok": False, "error": f"fetch failed: {type(e).__name__}: {e}"}), 502
    # Extract <a href="..."> values
    import re as _re
    from urllib.parse import urljoin, urlparse
    hrefs = _re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, _re.I)
    seen, found = set(), []
    VIDEO_EXT = _re.compile(r"\.(mp4|mkv|webm|avi|mov|m3u8|mpd|ts|flv)(\?|#|$)", _re.I)
    VIDEO_PATTERNS = _re.compile(r"/(video|watch|v|play|movie|episode|stream)/", _re.I)
    LISTING_PATTERNS = _re.compile(r"/(category|categories|tag|tags|page|search|browse|list|channel|playlist|feed|sitemap)/", _re.I)
    filter_listings = bool(body.get("filter_listings", True))
    for href in hrefs:
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        absolute = urljoin(url, href)
        if not absolute.startswith("http"): continue
        if absolute in seen: continue
        is_video = bool(VIDEO_EXT.search(absolute) or VIDEO_PATTERNS.search(absolute))
        if not is_video: continue
        if filter_listings and LISTING_PATTERNS.search(absolute):
            try:
                last = urlparse(absolute).path.rstrip("/").rsplit("/", 1)[-1]
                if not last.isdigit():
                    continue  # listing page, not a video page
            except Exception: pass
        seen.add(absolute); found.append(absolute)
        if len(found) >= max_links: break
    return jsonify({"ok": True, "url": url, "found": found,
                    "count": len(found), "html_size": len(html)})


def register_routes(app) -> int:
    app.register_blueprint(scrape_listing_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("scrape_listing."))
