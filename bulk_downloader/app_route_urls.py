"""route_urls API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/route_urls views moved onto a Flask Blueprint.
Endpoint labels gain a "route_urls." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import re
from flask import Blueprint, jsonify, request

route_urls_bp = Blueprint("route_urls", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@route_urls_bp.route("/api/route_urls",methods=["POST"])
def api_route_urls():
    """POST {"text": "url1\\nurl2\\n..."}  â†’  distributes to matching sites.

    Routing logic mirrors quick_add: per-site url_patterns regex first,
    then hostname match. URLs that don't match any site go into the
    response under `unrouted` so the user can decide what to do with them
    (typically: pick a default site and re-import them manually)."""
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    text = (request.json or {}).get("text", "")
    urls = [u.strip() for u in text.splitlines() if u.strip().startswith("http")]
    if not urls: return jsonify({"ok": False, "error": "no http URLs in input"}), 400

    # Phase 75 (v3.38.x): AI pre-classifier. When enabled per request via
    # ?ai_filter=1 (or json {"ai_filter": true}), filter URLs that look
    # like LISTING pages (rather than download pages) before routing them.
    # The AI fast path uses URL pattern heuristics first â€” only escalates
    # to a real model call for ambiguous URLs.
    body = request.json or {}
    ai_filter = body.get("ai_filter") or request.args.get("ai_filter") == "1"
    skipped_listings = []
    if ai_filter:
        from urllib.parse import urlparse as _up
        keep = []
        for url in urls:
            # Heuristic fast path â€” common listing patterns
            try:
                path = (_up(url).path or "").lower()
            except Exception:
                path = ""
            is_listing = any(seg in path for seg in (
                "/category/", "/categories/", "/tag/", "/tags/",
                "/page/", "/search", "/browse", "/list",
                "/channel/", "/playlist", "/feed", "/sitemap",
            ))
            # Listing-looking path with no terminal numeric ID = probably listing
            if is_listing:
                last_seg = path.rstrip("/").split("/")[-1]
                if not last_seg.isdigit():
                    skipped_listings.append(url)
                    continue
            keep.append(url)
        urls = keep

    from urllib.parse import urlparse
    by_site = {}  # sid -> list of urls
    unrouted = []
    results = []
    for url in urls:
        try: target_host = (urlparse(url).hostname or "").lower()
        except Exception: target_host = ""
        best_sid = None; best_score = -1
        for sid, cfg in s_cfg.items():
            score = 0
            patterns = (cfg.get("url_patterns") or "").strip()
            if patterns:
                for line in patterns.replace(",", "\n").splitlines():
                    pat = line.strip()
                    if not pat: continue
                    # v3.46.4 F9: bound regex eval time
                    if len(pat) > 512: continue
                    try:
                        if re.search(pat, url[:4096], re.IGNORECASE):
                            score = max(score, 200); break
                    except re.error: continue
            for fld in ("login_url", "success_url"):
                v = (cfg.get(fld) or "").lower()
                if not v: continue
                try: h = (urlparse(v).hostname or "").lower()
                except Exception: continue
                if h and target_host:
                    if h == target_host: score = max(score, 100)
                    elif h.endswith("." + target_host) or target_host.endswith("." + h):
                        score = max(score, 50)
            if score > best_score: best_score = score; best_sid = sid
        if best_score > 0 and best_sid:
            by_site.setdefault(best_sid, []).append(url)
            results.append({"url": url, "site_id": best_sid,
                            "matched": True})
        else:
            unrouted.append(url)
            results.append({"url": url, "site_id": None,
                            "matched": False})

    summary = {}
    for sid, site_urls in by_site.items():
        if sid not in runners: continue
        added, dupes, *rest = runners[sid].load_urls(site_urls)
        summary[sid] = {"name": s_cfg[sid].get("name", sid),
                        "added": added, "dupes": dupes,
                        "total_in_batch": len(site_urls)}
    return jsonify({"ok": True, "results": results,
                    "summary": summary, "unrouted": unrouted,
                    "unrouted_count": len(unrouted), "total_in": len(urls),
                    "skipped_listings": skipped_listings if ai_filter else [],
                    "skipped_listings_count": len(skipped_listings) if ai_filter else 0})

def register_routes(app) -> int:
    app.register_blueprint(route_urls_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("route_urls."))
