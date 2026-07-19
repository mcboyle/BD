"""tools API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/tools view moved onto a Flask Blueprint. Endpoint
label gains a "tools." prefix; the (rule, methods, bare-name) routing surface is
byte-identical (test_route_map_invariant diffs empty).

No app.py module STATE is touched. The handler resolves tools/registry.json via
__file__ (pkg_dir = dirname(abspath(__file__)); repo_root = dirname(pkg_dir)).
app_tools.py sits in the SAME bulk_downloader/ directory as app.py, so that path
math is byte-identical -- no _common depth helper is needed for a same-dir module
extraction (the section-3 __file__ transform is only for .py->package moves).
"""
from __future__ import annotations

import json
import os

from flask import Blueprint, jsonify

tools_bp = Blueprint("tools", __name__)


@tools_bp.route("/api/tools")
def api_tools():
    """List registered companion tools from tools/registry.json.

    Each tool is annotated with `present`: True iff its `script` exists
    on disk (tools without a script — e.g. bdctl subcommands — report
    present=True since there's nothing to locate)."""
    import json as _json
    # tools/ sits next to the bulk_downloader package
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(pkg_dir)
    tools_dir = os.path.join(repo_root, "tools")
    registry_path = os.path.join(tools_dir, "registry.json")
    if not os.path.exists(registry_path):
        # No registry shipped — not an error, just an empty list.
        return jsonify({"ok": True, "tools": [], "registry": False})
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({"ok": False,
                        "error": f"registry unreadable: {e}"}), 500
    tools = []
    for entry in data.get("tools", []):
        if not isinstance(entry, dict):
            continue
        script = entry.get("script")
        if script:
            present = os.path.exists(os.path.join(tools_dir, script))
        else:
            # No script to locate (e.g. a bdctl subcommand) — present.
            present = True
        tools.append({**entry, "present": present})
    return jsonify({"ok": True, "tools": tools, "registry": True,
                    "schema_version": data.get("schema_version", 1)})


def register_routes(app) -> int:
    app.register_blueprint(tools_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("tools."))
