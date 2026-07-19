"""community_scrapers API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/community_scrapers views moved onto a Flask Blueprint.
Endpoint labels gain a "community_scrapers." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_COMMUNITY_SCRAPERS_AVAILABLE, _community_scrapers) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

community_scrapers_bp = Blueprint("community_scrapers", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app__COMMUNITY_SCRAPERS_AVAILABLE():
    """The live shared _COMMUNITY_SCRAPERS_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_COMMUNITY_SCRAPERS_AVAILABLE")

def _app__community_scrapers():
    """The live shared _community_scrapers from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_community_scrapers")


@community_scrapers_bp.route("/api/community_scrapers/status", methods=["GET"])
def api_community_scrapers_status():
    """Module availability + manifest summary."""
    _COMMUNITY_SCRAPERS_AVAILABLE = _app__COMMUNITY_SCRAPERS_AVAILABLE()
    _community_scrapers = _app__community_scrapers()
    if not _COMMUNITY_SCRAPERS_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "community_scrapers module unavailable"})
    manifest = _community_scrapers.load_manifest()
    installed = manifest.get("installed", {})
    return jsonify({
        "ok": True,
        "installed_count": len(installed),
        "installed_names": sorted(installed.keys()),
    })


@community_scrapers_bp.route("/api/community_scrapers/index", methods=["GET"])
def api_community_scrapers_index():
    """List available scrapers from GitHub. force=1 to bypass cache."""
    _COMMUNITY_SCRAPERS_AVAILABLE = _app__COMMUNITY_SCRAPERS_AVAILABLE()
    _community_scrapers = _app__community_scrapers()
    if not _COMMUNITY_SCRAPERS_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "community_scrapers module unavailable"})
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    entries, err = _community_scrapers.fetch_index(force_refresh=force)
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({
        "ok": True,
        "count": len(entries),
        "entries": [
            {"name": e.name, "yml_path": e.yml_path,
             "sha": e.sha, "size": e.size}
            for e in entries
        ],
    })


@community_scrapers_bp.route("/api/community_scrapers/install", methods=["POST"])
def api_community_scrapers_install():
    """Install one scraper to the configured Stash dir."""
    _COMMUNITY_SCRAPERS_AVAILABLE = _app__COMMUNITY_SCRAPERS_AVAILABLE()
    _community_scrapers = _app__community_scrapers()
    _check_csrf()
    if not _COMMUNITY_SCRAPERS_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "community_scrapers module unavailable"})
    body = request.get_json(silent=True) or {}
    scraper_name = (body.get("scraper_name") or "").strip()
    stash_dir = (body.get("stash_dir") or "").strip()
    if not scraper_name:
        return jsonify({"ok": False, "error": "missing 'scraper_name'"})
    if not stash_dir:
        return jsonify({"ok": False, "error": "missing 'stash_dir'"})
    result = _community_scrapers.install_one(
        scraper_name, stash_dir=stash_dir,
        github_token=body.get("github_token", ""),
    )
    return jsonify({
        "ok": result.ok,
        "scraper_name": result.scraper_name,
        "files_written": result.files_written,
        "error": result.error,
    })


@community_scrapers_bp.route("/api/community_scrapers/remove", methods=["POST"])
def api_community_scrapers_remove():
    """Remove an installed scraper."""
    _COMMUNITY_SCRAPERS_AVAILABLE = _app__COMMUNITY_SCRAPERS_AVAILABLE()
    _community_scrapers = _app__community_scrapers()
    _check_csrf()
    if not _COMMUNITY_SCRAPERS_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "community_scrapers module unavailable"})
    body = request.get_json(silent=True) or {}
    scraper_name = (body.get("scraper_name") or "").strip()
    stash_dir = (body.get("stash_dir") or "").strip()
    if not scraper_name or not stash_dir:
        return jsonify({"ok": False, "error": "missing arguments"})
    result = _community_scrapers.remove_one(
        scraper_name, stash_dir=stash_dir)
    return jsonify({
        "ok": result.ok, "scraper_name": result.scraper_name,
        "files_removed": result.files_written, "error": result.error,
    })


@community_scrapers_bp.route("/api/community_scrapers/bulk_install", methods=["POST"])
def api_community_scrapers_bulk_install():
    """Install all scrapers whose name matches any of the supplied
    keyword strings (case-insensitive substring)."""
    _COMMUNITY_SCRAPERS_AVAILABLE = _app__COMMUNITY_SCRAPERS_AVAILABLE()
    _community_scrapers = _app__community_scrapers()
    _check_csrf()
    if not _COMMUNITY_SCRAPERS_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "community_scrapers module unavailable"})
    body = request.get_json(silent=True) or {}
    keywords = body.get("keywords") or []
    stash_dir = (body.get("stash_dir") or "").strip()
    if not isinstance(keywords, list) or not keywords:
        return jsonify({"ok": False, "error": "missing 'keywords' list"})
    if not stash_dir:
        return jsonify({"ok": False, "error": "missing 'stash_dir'"})
    result = _community_scrapers.bulk_install(
        keywords, stash_dir=stash_dir,
        github_token=body.get("github_token", ""),
    )
    return jsonify(result)

def register_routes(app) -> int:
    app.register_blueprint(community_scrapers_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("community_scrapers."))

