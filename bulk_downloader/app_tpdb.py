"""tpdb API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/tpdb views moved onto a Flask Blueprint.
Endpoint labels gain a "tpdb." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from .db import db_conn

tpdb_bp = Blueprint("tpdb", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@tpdb_bp.route("/api/tpdb/lookup/<int:hid>", methods=["POST"])
def api_tpdb_lookup(hid):
    """Look up TPDB metadata for one history row. Returns scene info
    without writing to disk; UI shows preview then operator confirms
    to call /api/tpdb/apply for nfo+atom embed."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    try:
        from . import tpdb as _tpdb
        if not _tpdb.is_available():
            return jsonify({"ok": False,
                            "error": "TPDB module unavailable "
                            "(install python-requests)"}), 503
        with db_conn() as cx:
            row = cx.execute("SELECT site_id, url, filename "
                            "FROM history WHERE id = ?",
                            (hid,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "no such hid"}), 404
        site_id, url, filename = row[0], row[1], row[2]
        s_cfg_entry = (s_cfg or {}).get(site_id, {})
        # Prefer site key; fall back to global
        api_key = s_cfg_entry.get("tpdb_api_key") or ""
        if not api_key:
            try:
                from .global_config import get_config
                api_key = (get_config() or {}).get("tpdb_api_key") or ""
            except Exception:
                pass
        if not _tpdb.is_configured(api_key):
            return jsonify({"ok": False,
                            "error": "TPDB API key not configured "
                            "(set per-site or global)"}), 400
        result = _tpdb.enrich(url, filename, api_key=api_key)
        return jsonify({"ok": True, "result": result,
                        "history_id": hid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

@tpdb_bp.route("/api/tpdb/apply/<int:hid>", methods=["POST"])
def api_tpdb_apply(hid):
    """Apply TPDB metadata to the file on disk: write .nfo + atom-embed.
    Requires the lookup result in the body OR will re-lookup."""
    s_cfg = _app_s_cfg()
    _check_csrf()
    body = request.json or {}
    try:
        from . import tpdb as _tpdb
        with db_conn() as cx:
            row = cx.execute("SELECT site_id, url, filename "
                            "FROM history WHERE id = ?",
                            (hid,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "no such hid"}), 404
        site_id, url, filename = row[0], row[1], row[2]
        if not filename:
            return jsonify({"ok": False,
                            "error": "history row has no filename"}), 400
        # Get metadata (either from body, or fresh lookup)
        metadata = body.get("metadata")
        if not metadata:
            s_cfg_entry = (s_cfg or {}).get(site_id, {})
            api_key = s_cfg_entry.get("tpdb_api_key") or ""
            if not api_key:
                try:
                    from .global_config import get_config
                    api_key = (get_config() or {}).get("tpdb_api_key") or ""
                except Exception:
                    pass
            if not _tpdb.is_configured(api_key):
                return jsonify({"ok": False,
                                "error": "no metadata in body and no API key"}), 400
            metadata = _tpdb.enrich(url, filename, api_key=api_key) or {}
        # Write .nfo sidecar
        applied = {"nfo_written": False}
        try:
            from . import library_final as _lf
            _lf.write_nfo(filename, metadata)
            applied["nfo_written"] = True
        except Exception as e:
            applied["nfo_error"] = str(e)[:80]
        # NOTE: MP4 atom embed not done here — `mp4_metadata.tag_mp4`
        # requires a MetadataContext, not a raw dict. Operators who
        # want atom embed should run with use_tpdb=True so the normal
        # _update_job hook fires; this on-demand path is .nfo-only.
        return jsonify({"ok": True, "applied": applied,
                        "metadata": metadata})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

def register_routes(app) -> int:
    app.register_blueprint(tpdb_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("tpdb."))

