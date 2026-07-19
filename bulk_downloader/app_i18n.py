"""i18n API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/i18n/{locales,load,template,save} views moved onto a
Flask Blueprint. Endpoint labels gain an "i18n." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant diffs
empty).

No app.py module STATE is touched. _check_csrf() delegates to app at call time
(lazy; avoids an import cycle). i18n is a sibling-package module imported
directly. __file__-anchored templates/ path is unchanged (app_i18n.py sits in
the same bulk_downloader/ dir as app.py).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

i18n_bp = Blueprint("i18n", __name__)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)


@i18n_bp.route("/api/i18n/locales")
def api_i18n_locales():
    try:
        from . import i18n as _i
        return jsonify({"available": _i.available_locales()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@i18n_bp.route("/api/i18n/load/<lang>")
def api_i18n_load(lang):
    try:
        from . import i18n as _i
        return jsonify({"lang": lang, "strings": _i.load_locale(lang)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@i18n_bp.route("/api/i18n/template")
def api_i18n_template():
    """Generate a translation template from all the .html files
    in templates/."""
    try:
        from . import i18n as _i
        from pathlib import Path
        templates_dir = Path(__file__).parent / "templates"
        files = list(templates_dir.glob("*.html"))
        return jsonify(_i.dump_template([str(f) for f in files]))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

@i18n_bp.route("/api/i18n/save/<lang>", methods=["POST"])
def api_i18n_save(lang):
    _check_csrf()
    body = request.json or {}
    if not isinstance(body.get("translations"), dict):
        return jsonify({"ok": False, "error": "translations dict required"}), 400
    try:
        from . import i18n as _i
        return jsonify({"ok": _i.save_locale(lang, body["translations"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def register_routes(app) -> int:
    app.register_blueprint(i18n_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("i18n."))
