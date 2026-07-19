"""dedup API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/dedup views moved onto a Flask Blueprint.
Endpoint labels gain a "dedup." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_DEDUP_AVAILABLE, _dedup, _dedup_scan_lock, _dedup_scan_state, s_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import os
import threading
import time
from flask import Blueprint, jsonify, request

dedup_bp = Blueprint("dedup", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _dedup_get_registry(*_a, **_k):
    """Delegate to app._dedup_get_registry at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_dedup_get_registry")(*_a, **_k)

def _app__DEDUP_AVAILABLE():
    """The live shared _DEDUP_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_DEDUP_AVAILABLE")

def _app__dedup():
    """The live shared _dedup from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_dedup")

def _app__dedup_scan_lock():
    """The live shared _dedup_scan_lock from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_dedup_scan_lock")

def _app__dedup_scan_state():
    """The live shared _dedup_scan_state from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "_dedup_scan_state")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")


@dedup_bp.route("/api/dedup/status", methods=["GET"])
def api_dedup_status():
    """Module availability + registry stats + scan progress."""
    _DEDUP_AVAILABLE = _app__DEDUP_AVAILABLE()
    _dedup = _app__dedup()
    _dedup_scan_lock = _app__dedup_scan_lock()
    _dedup_scan_state = _app__dedup_scan_state()
    reg = _dedup_get_registry()
    stats = reg.stats() if reg is not None else {"total": 0, "last_computed_at": 0.0}
    with _dedup_scan_lock:
        scan_snapshot = dict(_dedup_scan_state)
    # Strip non-serializable bits
    scan_snapshot.pop("thread", None)
    scan_snapshot.pop("cancel_event", None)
    return jsonify({
        "ok": True,
        "available": bool(_DEDUP_AVAILABLE and _dedup is not None
                         and _dedup.is_available()),
        "videohash_installed": bool(_DEDUP_AVAILABLE and _dedup is not None
                                    and _dedup.is_videohash_available()),
        "ffmpeg_installed": bool(_DEDUP_AVAILABLE and _dedup is not None
                                 and _dedup.is_ffmpeg_available()),
        "stats": stats,
        "scan": scan_snapshot,
    })


@dedup_bp.route("/api/dedup/scan", methods=["POST"])
def api_dedup_scan():
    """Kick off a background folder scan. Returns immediately."""
    _DEDUP_AVAILABLE = _app__DEDUP_AVAILABLE()
    _dedup = _app__dedup()
    _dedup_scan_lock = _app__dedup_scan_lock()
    _dedup_scan_state = _app__dedup_scan_state()
    s_cfg = _app_s_cfg()
    _check_csrf()
    if not (_DEDUP_AVAILABLE and _dedup is not None):
        return jsonify({"ok": False, "error": "dedup module unavailable"})
    if not _dedup.is_available():
        return jsonify({
            "ok": False,
            "error": "videohash + ffmpeg required (install with: "
                     "pip install videohash && winget install ffmpeg)",
        })
    body = request.get_json(silent=True) or {}
    root = (body.get("root") or "").strip()
    if not root:
        # Default to the first site's download_dir
        for sid, cfg in s_cfg.items():
            dd = (cfg.get("download_dir") or "").strip()
            if dd:
                root = dd
                break
    if not root or not os.path.isdir(root):
        return jsonify({"ok": False,
                        "error": f"not a directory: {root!r}"})
    with _dedup_scan_lock:
        if _dedup_scan_state["running"]:
            return jsonify({
                "ok": False,
                "error": "scan already running",
                "state": {k: v for k, v in _dedup_scan_state.items()
                          if k not in ("thread", "cancel_event")},
            })
        cancel_event = threading.Event()
        _dedup_scan_state.update({
            "running": True, "started_at": time.time(),
            "done": 0, "total": 0, "current_path": "",
            "summary": None, "cancel_event": cancel_event,
        })
    reg = _dedup_get_registry()
    if reg is None:
        with _dedup_scan_lock:
            _dedup_scan_state["running"] = False
        return jsonify({"ok": False, "error": "registry init failed"})

    def _scan_worker():
        def _progress(done, total, current_path):
            with _dedup_scan_lock:
                _dedup_scan_state["done"] = done
                _dedup_scan_state["total"] = total
                _dedup_scan_state["current_path"] = current_path

        def _cancel_check():
            return cancel_event.is_set()

        try:
            summary = reg.scan_folder(
                root, progress_cb=_progress, cancel_check=_cancel_check,
            )
        except Exception as e:
            summary = {"error": str(e), "elapsed_s": 0.0}
        with _dedup_scan_lock:
            _dedup_scan_state["running"] = False
            _dedup_scan_state["summary"] = summary

    t = threading.Thread(target=_scan_worker, daemon=True,
                         name="dedup-scan")
    with _dedup_scan_lock:
        _dedup_scan_state["thread"] = t
    t.start()
    return jsonify({"ok": True, "started": True, "root": root})


@dedup_bp.route("/api/dedup/scan/cancel", methods=["POST"])
def api_dedup_scan_cancel():
    """Cancel an in-progress folder scan."""
    _dedup_scan_lock = _app__dedup_scan_lock()
    _dedup_scan_state = _app__dedup_scan_state()
    _check_csrf()
    with _dedup_scan_lock:
        ev = _dedup_scan_state.get("cancel_event")
    if ev is None:
        return jsonify({"ok": False, "error": "no scan running"})
    ev.set()
    return jsonify({"ok": True, "cancelled": True})


@dedup_bp.route("/api/dedup/find", methods=["POST"])
def api_dedup_find():
    """Find duplicates for a specific file path (must already be in
    the registry, or we'll compute its hash first)."""
    _DEDUP_AVAILABLE = _app__DEDUP_AVAILABLE()
    _dedup = _app__dedup()
    _check_csrf()
    if not (_DEDUP_AVAILABLE and _dedup is not None):
        return jsonify({"ok": False, "error": "dedup module unavailable"})
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    distance = int(body.get("distance", 4) or 4)
    distance = max(0, min(32, distance))
    if not path:
        return jsonify({"ok": False, "error": "missing 'path'"})
    reg = _dedup_get_registry()
    if reg is None:
        return jsonify({"ok": False, "error": "registry init failed"})
    # Already in registry?
    existing = reg.lookup(path)
    if existing is not None:
        hash_hex = existing["hash_hex"]
    else:
        if not _dedup.is_available():
            return jsonify({
                "ok": False,
                "error": "file not in registry and videohash unavailable",
            })
        res = _dedup.compute_hash(path)
        if not res.ok:
            return jsonify({
                "ok": False, "error": f"hash failed: {res.error}",
            })
        reg.add(res, notes="api_find")
        hash_hex = res.hash_hex
    dups = reg.find_duplicates(hash_hex, distance=distance,
                                exclude_path=path)
    return jsonify({
        "ok": True,
        "path": path,
        "hash_hex": hash_hex,
        "distance_threshold": distance,
        "duplicates": dups,
    })


@dedup_bp.route("/api/dedup/groups", methods=["GET"])
def api_dedup_groups():
    """List all duplicate groups in the registry. For the review UI."""
    _DEDUP_AVAILABLE = _app__DEDUP_AVAILABLE()
    _dedup = _app__dedup()
    if not (_DEDUP_AVAILABLE and _dedup is not None):
        return jsonify({"ok": False, "error": "dedup module unavailable"})
    reg = _dedup_get_registry()
    if reg is None:
        return jsonify({"ok": False, "error": "registry init failed"})
    distance = int(request.args.get("distance", 4) or 4)
    distance = max(0, min(32, distance))
    # Walk every hash in the registry and find groups with >1 file
    try:
        c = reg._conn()
        try:
            rows = c.execute(
                "SELECT path, hash_hex, file_size_bytes, computed_at "
                "FROM video_hashes"
            ).fetchall()
        finally:
            c.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    # For efficiency on huge libraries, bucket by exact hash first
    # (Hamming-distance grouping is O(N²) so we cap at 5000 files).
    if len(rows) > 5000:
        return jsonify({
            "ok": False,
            "error": (f"registry too large ({len(rows)} files); "
                      "Hamming grouping is O(N²). Use /api/dedup/find "
                      "on specific files instead."),
        })
    seen: set = set()
    groups: list = []
    for i, r in enumerate(rows):
        if r[0] in seen:
            continue
        # Find all rows within `distance` of this one
        group: list = [{
            "path": r[0], "hash_hex": r[1],
            "file_size_bytes": r[2] or 0,
            "computed_at": r[3] or 0.0,
            "distance": 0,
        }]
        for r2 in rows[i+1:]:
            if r2[0] in seen:
                continue
            d = _dedup.hamming_distance(r[1], r2[1])
            if 0 <= d <= distance:
                group.append({
                    "path": r2[0], "hash_hex": r2[1],
                    "file_size_bytes": r2[2] or 0,
                    "computed_at": r2[3] or 0.0,
                    "distance": d,
                })
                seen.add(r2[0])
        if len(group) > 1:
            seen.add(r[0])
            groups.append({
                "members": group,
                "max_distance": max(m["distance"] for m in group),
                "size_diff_bytes": max(m["file_size_bytes"] for m in group)
                                   - min(m["file_size_bytes"] for m in group),
            })
    return jsonify({
        "ok": True, "distance": distance,
        "total_files": len(rows),
        "group_count": len(groups),
        "groups": groups,
    })


@dedup_bp.route("/api/dedup/remove", methods=["POST"])
def api_dedup_remove():
    """Remove a path from the registry (does NOT delete the file)."""
    _DEDUP_AVAILABLE = _app__DEDUP_AVAILABLE()
    _dedup = _app__dedup()
    _check_csrf()
    if not (_DEDUP_AVAILABLE and _dedup is not None):
        return jsonify({"ok": False, "error": "dedup module unavailable"})
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "missing 'path'"})
    reg = _dedup_get_registry()
    if reg is None:
        return jsonify({"ok": False, "error": "registry init failed"})
    ok = reg.remove(path)
    return jsonify({"ok": ok, "path": path})

def register_routes(app) -> int:
    app.register_blueprint(dedup_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("dedup."))

