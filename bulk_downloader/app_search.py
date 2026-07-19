"""search API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/search views moved onto a Flask Blueprint.
Endpoint labels gain a "search." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_SEARCH_AVAILABLE, _search_mod, runners, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

search_bp = Blueprint("search", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _serialize_search_result(*_a, **_k):
    """Delegate to app._serialize_search_result at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_serialize_search_result")(*_a, **_k)

def _app__SEARCH_AVAILABLE():
    """The live shared _SEARCH_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_SEARCH_AVAILABLE")

def _app__search_mod():
    """The live shared _search_mod from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_search_mod")

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


def _facet_counts(query: str = "", site_id: str = "", status: str = "") -> dict:
    """Group matching history rows by site_id and by status. ``query`` is a LIKE
    match on filename/url. Read-only; the same filter fields a saved search uses,
    so the UI can show 'N matches across M sites' and drill down."""
    from . import db as _db
    where, params = ["1=1"], []
    if site_id:
        where.append("site_id = ?")
        params.append(site_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if query:
        where.append("(filename LIKE ? OR url LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    wsql = " AND ".join(where)
    by_site: dict = {}
    by_status: dict = {}
    total = 0
    try:
        with _db.db_conn() as cx:
            rows = cx.execute(
                f"SELECT site_id, status, COUNT(*) AS c FROM history "
                f"WHERE {wsql} GROUP BY site_id, status", params).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "site_id": r[0], "status": r[1], "c": r[2]}
            sid = d.get("site_id") or "?"
            stt = d.get("status") or "?"
            c = int(d.get("c") or 0)
            by_site[sid] = by_site.get(sid, 0) + c
            by_status[stt] = by_status.get(stt, 0) + c
            total += c
    except Exception:
        return {"by_site": {}, "by_status": {}, "total": 0}
    return {"by_site": by_site, "by_status": by_status, "total": total}


@search_bp.route("/api/search/facets")
def api_search_facets():
    """Read-only facet breakdown of history matches by site and status for the
    given query/site_id/status filters."""
    q = request.args.get("query", "") or request.args.get("q", "")
    site_id = request.args.get("site_id", "")
    status = request.args.get("status", "")
    return jsonify({"ok": True, "facets": _facet_counts(q, site_id, status)})


@search_bp.route("/api/search")
def api_search():
    """GET /api/search?q=<query>&site_id=<sid>&status=<s>&limit=<n>

    Returns: {results: [...], count: N, query: q}
    Each result has the full history row plus snippet_url/_filename/_message
    fields with <mark>...</mark> highlights for the matching terms.

    Empty query returns empty results (not all-rows). Capped at 500
    results regardless of limit param to prevent runaway payloads."""
    q = (request.args.get("q", "") or "").strip()
    if not q:
        return jsonify({"results": [], "count": 0, "query": ""})
    site_id = request.args.get("site_id") or None
    status = request.args.get("status") or None
    try:
        limit = min(500, max(1, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    try:
        from . import db as _db
        results = _db.db_search_fts(q, site_id=site_id, status=status, limit=limit)
        return jsonify({"results": results, "count": len(results), "query": q})
    except Exception as e:
        return jsonify({"results": [], "count": 0, "query": q,
                        "error": str(e)[:200]}), 500
@search_bp.route("/api/search/site", methods=["POST"])
def api_search_site():
    """Search one configured site for a query string.

    Body: {site_id, query, max_results?}
    Returns the SearchResult with hits + metadata. Doesn't queue
    anything — the UI calls /api/route_urls (or equivalent) on the
    user's picks."""
    _SEARCH_AVAILABLE = _app__SEARCH_AVAILABLE()
    _search_mod = _app__search_mod()
    runners = _app_runners()
    _check_csrf()
    if not (_SEARCH_AVAILABLE and _search_mod is not None):
        return jsonify({"ok": False, "error": "search_extractor unavailable"})
    body = request.get_json(silent=True) or {}
    site_id = (body.get("site_id") or "").strip()
    query = (body.get("query") or "").strip()
    max_results = int(body.get("max_results", 50) or 50)
    max_results = max(1, min(200, max_results))
    if not site_id:
        return jsonify({"ok": False, "error": "missing 'site_id'"})
    if not query:
        return jsonify({"ok": False, "error": "missing 'query'"})
    if site_id not in runners:
        return jsonify({"ok": False, "error": "unknown site_id"})
    runner = runners[site_id]
    try:
        result = runner._search_site(query, max_results=max_results)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"search_raised:{type(e).__name__}:{str(e)[:80]}"})
    return jsonify(_serialize_search_result(result))
@search_bp.route("/api/search/all", methods=["POST"])
def api_search_all():
    """Search every site that has use_search_extractor=True for a
    query.

    Body: {query, max_results_per_site?, sites?}
    `sites` (optional): list of site_ids to restrict to. Default: all.

    Returns: {ok, query, results: {site_id → SearchResult dict}, stats}

    This is intentionally serial (one site at a time) so we don't
    hammer the user's network or trigger rate limits across sites.
    """
    _SEARCH_AVAILABLE = _app__SEARCH_AVAILABLE()
    _search_mod = _app__search_mod()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    _check_csrf()
    if not (_SEARCH_AVAILABLE and _search_mod is not None):
        return jsonify({"ok": False, "error": "search_extractor unavailable"})
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    max_per_site = int(body.get("max_results_per_site", 20) or 20)
    max_per_site = max(1, min(100, max_per_site))
    sites_filter = body.get("sites")
    if sites_filter is not None and not isinstance(sites_filter, list):
        return jsonify({"ok": False, "error": "'sites' must be a list"})
    if not query:
        return jsonify({"ok": False, "error": "missing 'query'"})

    # Find searchable sites
    candidates: dict = {}
    for sid, cfg in s_cfg.items():
        if not cfg.get("use_search_extractor", False):
            continue
        if not _search_mod.is_site_searchable(cfg):
            continue
        if sites_filter is not None and sid not in sites_filter:
            continue
        if sid not in runners:
            continue
        candidates[sid] = cfg

    if not candidates:
        return jsonify({
            "ok": True, "query": query, "results": {},
            "stats": _search_mod.aggregate_stats({}),
            "note": "no sites have use_search_extractor enabled",
        })

    results_dict: dict = {}
    for sid in candidates:
        runner = runners[sid]
        try:
            result = runner._search_site(query, max_results=max_per_site)
        except Exception as e:
            result = _search_mod.SearchResult(
                ok=False, site_id=sid, query=query,
                error=f"raised:{type(e).__name__}",
            )
        results_dict[sid] = result

    return jsonify({
        "ok": True,
        "query": query,
        "stats": _search_mod.aggregate_stats(results_dict),
        "results": {
            sid: _serialize_search_result(r)
            for sid, r in results_dict.items()
        },
    })
@search_bp.route("/api/search/sites_available", methods=["GET"])
def api_search_sites_available():
    """List sites that have use_search_extractor=True AND a valid
    search_url_pattern. Lets the UI show "search 7 of your 12 sites"
    instead of running a no-op search."""
    _SEARCH_AVAILABLE = _app__SEARCH_AVAILABLE()
    _search_mod = _app__search_mod()
    s_cfg = _app_s_cfg()
    if not (_SEARCH_AVAILABLE and _search_mod is not None):
        return jsonify({"ok": False, "error": "search_extractor unavailable"})
    available = []
    for sid, cfg in s_cfg.items():
        if not cfg.get("use_search_extractor", False):
            continue
        if not _search_mod.is_site_searchable(cfg):
            continue
        available.append({
            "site_id": sid,
            "name": cfg.get("name", sid),
        })
    return jsonify({
        "ok": True,
        "available": available,
        "count": len(available),
        "total_sites": len(s_cfg),
    })

def register_routes(app) -> int:
    app.register_blueprint(search_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("search."))

