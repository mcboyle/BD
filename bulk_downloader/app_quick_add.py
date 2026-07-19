"""quick_add API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/quick_add views moved onto a Flask Blueprint.
Endpoint labels gain a "quick_add." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_app_cfg, runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import re
from flask import Blueprint, jsonify, request

quick_add_bp = Blueprint("quick_add", __name__)

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@quick_add_bp.route("/api/quick_add",methods=["POST","GET"])
def api_quick_add():
    """Add a single URL to whichever site matches its hostname.

    POST {"url": "..."}  or  GET /api/quick_add?url=...
    Returns: {"ok": true, "site_id": "...", "site_name": "...", "added": true|false}

    Works as a simple iOS Shortcut: "Get URL from Input → POST to <BD URL>".
    The URL field lands in the Shortcut's request body and we route it to
    the right site by matching domains against existing site login_url /
    success_url. If multiple sites match, picks the one with the most
    URLs (most likely the right one). If no site matches, uses the
    `default_quick_add_site` setting in app_config (settable via the UI)
    or falls back to the first site."""
    _app_cfg = _app__app_cfg()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    url = (request.json or {}).get("url") if request.is_json else None
    if not url: url = request.args.get("url") or request.form.get("url")
    if not url or not str(url).startswith("http"):
        return jsonify({"ok": False, "error": "url required (must start with http)"}), 400
    url = str(url).strip()

    # Already in any site? Treat as success (idempotent for share-sheet retries).
    for runner in runners.values():
        if url in runner.jobs:
            return jsonify({"ok": True, "site_id": runner.site_id,
                            "site_name": runner.config.get("name", runner.site_id),
                            "added": False, "reason": "already in queue"})

    # Find the best site by hostname match OR explicit URL pattern (Phase 18.21)
    from urllib.parse import urlparse
    try: target_host = (urlparse(url).hostname or "").lower()
    except Exception: target_host = ""
    best_sid = None; best_score = -1
    for sid, cfg in s_cfg.items():
        score = 0
        # Phase 18.21: explicit url_patterns wins over hostname inference.
        # User-defined regexes get highest priority (score 200) so a wildcard
        # routing rule can't be overridden by accidental hostname matches.
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
                except re.error:
                    continue  # bad regex — skip silently
        for fld in ("login_url", "success_url"):
            v = (cfg.get(fld) or "").lower()
            if not v: continue
            try: h = (urlparse(v).hostname or "").lower()
            except Exception: continue
            if h and target_host:
                if h == target_host: score = max(score, 100)
                # Subdomain or apex match (e.g. www.x.com vs x.com)
                elif h.endswith("." + target_host) or target_host.endswith("." + h):
                    score = max(score, 50)
        # Tie-breaker: site with more existing URLs is more likely the right one
        score += min(len(runners[sid].urls), 99) * 0.01 if sid in runners else 0
        if score > best_score:
            best_score = score; best_sid = sid

    # Fall back to configured default if no hostname matched
    if best_score <= 0:
        default = (_app_cfg or {}).get("default_quick_add_site") or ""
        if default in runners:
            best_sid = default
        elif runners:
            best_sid = next(iter(runners.keys()))

    if not best_sid:
        return jsonify({"ok": False, "error": "no site configured to receive URL"}), 400

    runner = runners[best_sid]
    added, dupes = runner.load_urls([url])[:2]
    # Phase 18.21: report which routing rule won so the caller (Shortcut/UI)
    # can sanity-check unexpected routes. "regex" beats "host" beats "default".
    if best_score >= 200: match_type = "regex"
    elif best_score >= 100: match_type = "host"
    elif best_score >= 50: match_type = "host_partial"
    else: match_type = "default"
    return jsonify({
        "ok": True,
        "site_id": best_sid,
        "site_name": s_cfg[best_sid].get("name", best_sid),
        "added": added > 0,
        "match_type": match_type,
        "host_matched": best_score >= 100,  # kept for backwards compat
    })

def register_routes(app) -> int:
    app.register_blueprint(quick_add_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("quick_add."))

