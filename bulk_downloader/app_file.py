"""file API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/file views moved onto a Flask Blueprint.
Endpoint labels gain a "file." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from pathlib import Path

file_bp = Blueprint("file", __name__)

def _validate_path(*_a, **_k):
    """Delegate to app._validate_path at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_path")(*_a, **_k)


def _validate_reveal_path(*_a, **_k):
    """Delegate to app._validate_reveal_path at call time (lazy; avoids an
    import cycle). Reveal-scoped: rejects paths outside the download/reveal
    roots even when path_allowlist is the legacy-permissive empty default."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_validate_reveal_path")(*_a, **_k)


@file_bp.route("/api/file/reveal", methods=["POST"])
def api_file_reveal():
    import subprocess, sys
    body = request.json or {}
    raw_path = body.get("path", "")
    if not raw_path or not isinstance(raw_path, str):
        return jsonify({"ok": False, "error": "path required"}), 400
    # F-APP06-01: reveal-scoped validation -- an empty path_allowlist must NOT
    # let reveal open an arbitrary absolute path (falls back to download roots).
    ok, msg_or_path = _validate_reveal_path(raw_path, "path")
    if not ok:
        return jsonify({"ok": False, "error": msg_or_path}), 400
    p = Path(msg_or_path)
    # If the path doesn't exist, fall back to the parent (which probably
    # does — common case is "the file was deleted but the dir still
    # has the rest of the download.")
    if not p.exists():
        if p.parent.exists():
            p = p.parent
        else:
            return jsonify({"ok": False,
                            "error": f"path not found: {raw_path}"}), 404
    try:
        if sys.platform == "win32":
            # /select, opens Explorer and highlights the target. The
            # comma + lack of space after /select is the documented
            # syntax. Subprocess split because shell=True is unsafe.
            if p.is_dir():
                subprocess.Popen(["explorer", str(p)], close_fds=True)
            else:
                subprocess.Popen(
                    ["explorer", "/select,", str(p)], close_fds=True)
        elif sys.platform == "darwin":
            # open -R reveals in Finder. Works for both files and dirs.
            subprocess.Popen(["open", "-R", str(p)], close_fds=True)
        else:
            # Linux/BSD: xdg-open the parent dir. There's no portable
            # "reveal and select" — desktop environments vary too much.
            subprocess.Popen(
                ["xdg-open", str(p if p.is_dir() else p.parent)],
                close_fds=True)
        return jsonify({"ok": True, "revealed": str(p)})
    except FileNotFoundError as e:
        return jsonify({"ok": False,
                        "error": f"native reveal tool missing: {e}"}), 500
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"reveal failed: {type(e).__name__}"}), 500

def register_routes(app) -> int:
    app.register_blueprint(file_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("file."))

