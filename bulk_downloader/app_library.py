"""library API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/library views moved onto a Flask Blueprint.
Endpoint labels gain a "library." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from pathlib import Path

library_bp = Blueprint("library", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _validate_path(*_a, **_k):
    """Delegate to app._validate_path at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_path")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@library_bp.route("/api/library/audit", methods=["POST"])
def api_library_audit():
    """Run a full audit on a download directory.

    Body: download_dir (required), site_id (optional).

    Returns library_final.audit()'s payload verbatim; that function's
    docstring is the single source of truth for the key set. This one
    deliberately names no keys. It was the fourth statement of a contract
    that already had three -- audit(), api-types.ts and Library.tsx -- and
    the only one no gate's denominator could reach, so it drifted: it
    documented five keys audit() never returned, and glossed the `orphans`
    int count as "files", which is the invitation to `.length` on an int
    that PR #99 had to fix in the SPA panel.
    test_handler_docstring_is_not_a_fourth_drifting_contract now covers it."""
    _check_csrf()
    body = request.json or {}
    download_dir = body.get("download_dir") or ""
    site_id = body.get("site_id") or None
    if not download_dir:
        return jsonify({"error": "download_dir required"}), 400
    try:
        from . import library_final as _lf
        return jsonify(_lf.audit(download_dir=download_dir,
                                  site_id=site_id))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@library_bp.route("/api/library/regen_nfos", methods=["POST"])
def api_library_regen_nfos():
    """Bulk-regenerate NFO sidecars from the history table. Body:
    {site_id?, overwrite?, dry_run?, download_dir?}. Useful after upgrades
    that change the NFO schema, or to backfill files that never had NFOs.

    download_dir is OPTIONAL and is what a recorded basename resolves
    against. Unlike /api/library/audit it does NOT 400 when absent: the
    shipped panel sends only {dry_run}, so requiring it would break the
    button. Absent, rows report as `unknown` rather than as missing."""
    _check_csrf()
    body = request.json or {}
    try:
        from . import library_final as _lf
        return jsonify(_lf.regen_nfos_from_history(
            site_id=body.get("site_id") or None,
            overwrite=bool(body.get("overwrite", False)),
            dry_run=bool(body.get("dry_run", True)),
            download_dir=body.get("download_dir") or ""))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@library_bp.route("/api/library/orphans", methods=["POST"])
def api_library_orphans_post():
    """Find files on disk that aren't in history (likely manual
    drops or files from a previous BD install). Body: {download_dir,
    site_id?}.

    v3.49: renamed from `api_library_orphans` to disambiguate from the
    v3.50 GET endpoint with the same path. Same path, different methods,
    different modules — Flask requires unique endpoint names per
    function."""
    _check_csrf()
    body = request.json or {}
    download_dir = body.get("download_dir") or ""
    if not download_dir:
        return jsonify({"error": "download_dir required"}), 400
    try:
        from . import library_final as _lf
        return jsonify({"orphans": _lf.list_orphans(
            download_dir, site_id=body.get("site_id") or None)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@library_bp.route("/api/library/browse")
def api_library_browse():
    from . import library as _lib
    args = request.args
    def _b(v):  # bool parse, "1"/"true"/"yes" → True, None for missing
        if v in (None, ""):
            return None
        return v.lower() in ("1", "true", "yes", "on")
    try:
        limit = max(1, min(int(args.get("limit", "100")), 500))
    except ValueError:
        limit = 100
    try:
        after_id = int(args["after_id"]) if args.get("after_id") else None
    except ValueError:
        after_id = None
    try:
        year = int(args["year"]) if args.get("year") else None
    except ValueError:
        year = None
    rows, next_cursor = _lib.library_browse(
        site_id=args.get("site_id") or None,
        studio=args.get("studio") or None,
        performer=args.get("performer") or None,
        year=year,
        watched=_b(args.get("watched")),
        tag=args.get("tag") or None,
        query=args.get("q") or None,
        missing_only=bool(_b(args.get("missing_only"))),
        sort=args.get("sort", "added_at_desc"),
        limit=limit,
        after_id=after_id,
    )
    return jsonify({"ok": True, "rows": rows,
                    "count": len(rows), "next_cursor": next_cursor})
@library_bp.route("/api/library/<int:lid>")
def api_library_get(lid):
    from . import library as _lib
    row = _lib.library_get(lid)
    if row is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "row": row})
@library_bp.route("/api/library/<int:lid>", methods=["DELETE"])
def api_library_delete(lid):
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    also = bool(body.get("delete_file", False))
    result = _lib.library_delete(lid, also_delete_file=also)
    code = 200 if result.get("ok") else 400
    if not result.get("ok") and "not found" in (result.get("error", "")):
        code = 404
    return jsonify(result), code
@library_bp.route("/api/library/<int:lid>/watched", methods=["POST"])
def api_library_watched(lid):
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    watched = bool(body.get("watched", True))
    ok = _lib.library_set_watched(lid, watched)
    if not ok:
        return jsonify({"ok": False, "error": "not found or no change"}), 404
    return jsonify({"ok": True, "watched": watched})
@library_bp.route("/api/library/<int:lid>/rating", methods=["POST"])
def api_library_rating(lid):
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    rating = body.get("rating")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "rating must be integer or null"}), 400
    ok = _lib.library_set_rating(lid, rating)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "rating": rating})
@library_bp.route("/api/library/<int:lid>/notes", methods=["POST"])
def api_library_notes(lid):
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    notes = body.get("notes", "")
    ok = _lib.library_set_notes(lid, notes)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})
@library_bp.route("/api/library/<int:lid>/tags", methods=["POST"])
def api_library_tag_add(lid):
    """Body: {tag: 'name'} → attach (creating tag if needed)."""
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    name = (body.get("tag") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "tag name required"}), 400
    ok = _lib.library_add_tag(lid, name)
    if not ok:
        return jsonify({"ok": False, "error": "tag attach failed"}), 400
    return jsonify({"ok": True})
@library_bp.route("/api/library/<int:lid>/tags/<path:tag_name>",
           methods=["DELETE"])
def api_library_tag_remove(lid, tag_name):
    from . import library as _lib
    _lib.library_remove_tag(lid, tag_name)
    # Idempotent — return ok even if the tag wasn't attached
    return jsonify({"ok": True})
@library_bp.route("/api/library/stats")
def api_library_stats():
    from . import library as _lib
    return jsonify({"ok": True, "stats": _lib.library_stats()})
@library_bp.route("/api/library/tags")
def api_library_tags_list():
    from . import library as _lib
    return jsonify({"ok": True, "tags": _lib.tag_list()})
@library_bp.route("/api/library/tags/<int:tag_id>", methods=["DELETE"])
def api_library_tag_delete(tag_id):
    from . import library as _lib
    ok = _lib.tag_delete(tag_id)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})
@library_bp.route("/api/library/orphans")
def api_library_orphans_v2():
    """v3.50: Files under `?root=` that aren't in the library table.

    Different from the legacy POST endpoint of the same path which
    used a different module (library_final). This one is GET-based,
    uses the v3.50 `library` module, and accepts the root as a query
    parameter for easy use from the new library tab."""
    from . import library as _lib
    root = request.args.get("root", "").strip()
    if not root:
        return jsonify({"ok": False, "error": "root required"}), 400
    # Reuse path validation — same trust model as cookie_file etc.
    ok, msg_or_path = _validate_path(root, "root")
    if not ok:
        return jsonify({"ok": False, "error": msg_or_path}), 400
    return jsonify({"ok": True,
                    "orphans": _lib.library_orphans(msg_or_path)})
@library_bp.route("/api/library/scan/start", methods=["POST"])
def api_library_scan_start():
    """Body: {roots: [...]} — start a scan. If roots is omitted, use
    every site's download_dir as defaults."""
    s_cfg = _app_s_cfg()
    from . import library as _lib
    body = request.get_json(silent=True) or {}
    roots = body.get("roots")
    if not roots:
        # Default: scan every site's configured download_dir
        roots = []
        for sid, cfg in s_cfg.items():
            d = cfg.get("download_dir") or ""
            if d and Path(d).is_dir() and d not in roots:
                roots.append(d)
        if not roots:
            return jsonify({"ok": False,
                            "error": "no download_dir configured "
                                     "on any site; pass `roots` explicitly"}), 400
    # Validate every root with the same path-allowlist semantics
    cleaned = []
    for r in roots:
        ok, msg_or_path = _validate_path(str(r), "root")
        if not ok:
            return jsonify({"ok": False,
                            "error": f"root {r}: {msg_or_path}"}), 400
        cleaned.append(msg_or_path)
    result = _lib.scan_start(cleaned)
    code = 200 if result.get("ok") else 409
    return jsonify(result), code
@library_bp.route("/api/library/scan/status")
def api_library_scan_status():
    from . import library as _lib
    return jsonify({"ok": True, "scan": _lib.scan_status()})
@library_bp.route("/api/library/scan/cancel", methods=["POST"])
def api_library_scan_cancel():
    from . import library as _lib
    cancelled = _lib.scan_cancel()
    return jsonify({"ok": True, "cancelled": cancelled})
@library_bp.route("/api/library/integrity")
def api_library_integrity():
    """#69 — file-system integrity / rename detection.

    Surfaces the bitrot module's integrity_issues table — files that
    have gone missing, been modified, or truncated since they were
    recorded. Read-only; the scan itself is triggered elsewhere."""
    try:
        from . import bitrot as _bitrot
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"bitrot unavailable: {e}"}), 503
    kind = request.args.get("kind") or None
    try:
        limit = max(1, min(int(request.args.get("limit", "100")), 1000))
    except ValueError:
        limit = 100
    try:
        issues = _bitrot.list_issues(kind=kind, repaired=False,
                                     limit=limit)
        stats = _bitrot.stats()
        return jsonify({"ok": True, "issues": issues, "stats": stats,
                        "count": len(issues)})
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(library_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("library."))

