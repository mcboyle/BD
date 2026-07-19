"""tg API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/tg views moved onto a Flask Blueprint.
Endpoint labels gain a "tg." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_TG_BOT_AVAILABLE, _tg_bot) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

tg_bp = Blueprint("tg", __name__)

def _apply_tg_bot_config(*_a, **_k):
    """Delegate to app._apply_tg_bot_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_apply_tg_bot_config")(*_a, **_k)

def _check_csrf(*_a, **_k):
    """Delegate to app._check_csrf at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_check_csrf")(*_a, **_k)

def _load_global_notify_settings(*_a, **_k):
    """Delegate to app._load_global_notify_settings at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_load_global_notify_settings")(*_a, **_k)

def _save_global_notify_settings(*_a, **_k):
    """Delegate to app._save_global_notify_settings at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_global_notify_settings")(*_a, **_k)

def _app__TG_BOT_AVAILABLE():
    """The live shared _TG_BOT_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_TG_BOT_AVAILABLE")

def _app__tg_bot():
    """The live shared _tg_bot from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_tg_bot")


@tg_bp.route("/api/tg/status", methods=["GET"])
def api_tg_status():
    """Bot health snapshot."""
    _TG_BOT_AVAILABLE = _app__TG_BOT_AVAILABLE()
    _tg_bot = _app__tg_bot()
    if not (_TG_BOT_AVAILABLE and _tg_bot is not None):
        return jsonify({"ok": False, "available": False,
                        "error": "tg_bot module unavailable"})
    bot = _tg_bot.get_bot()
    s = bot.get_status()
    return jsonify({
        "ok": True,
        "available": True,
        "enabled": s.enabled,
        "running": s.running,
        "last_poll_wall": s.last_poll_wall,
        "last_error": s.last_error,
        "updates_received": s.updates_received,
        "commands_executed": s.commands_executed,
        "allowlist_size": s.allowlist_size,
        "bot_username": s.bot_username,
    })


@tg_bp.route("/api/tg/settings", methods=["GET"])
def api_tg_settings_get():
    """Read TG bot settings (subset of notify_apprise.json)."""
    _TG_BOT_AVAILABLE = _app__TG_BOT_AVAILABLE()
    cfg = _load_global_notify_settings()
    return jsonify({
        "ok": True,
        "available": _TG_BOT_AVAILABLE,
        "settings": {
            "tg_bot_enabled": bool(cfg.get("tg_bot_enabled", False)),
            # Mask token in the response (leak protection — log files,
            # screenshots, etc.)
            "tg_bot_token_set": bool(cfg.get("tg_bot_token", "")),
            "tg_bot_allowlist": cfg.get("tg_bot_allowlist", "") or "",
        },
    })


@tg_bp.route("/api/tg/settings", methods=["POST"])
def api_tg_settings_post():
    """Save TG bot settings + apply to running bot."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "expected JSON object"}), 400
    cfg = _load_global_notify_settings()
    if "tg_bot_enabled" in body:
        cfg["tg_bot_enabled"] = bool(body["tg_bot_enabled"])
    # Token is only updated if provided (allows the UI to PATCH the
    # allowlist without forcing the user to re-paste the token).
    if "tg_bot_token" in body:
        tok = str(body["tg_bot_token"] or "").strip()
        if tok:
            cfg["tg_bot_token"] = tok
        elif body.get("tg_bot_token_clear"):
            cfg["tg_bot_token"] = ""
    if "tg_bot_allowlist" in body:
        cfg["tg_bot_allowlist"] = str(body["tg_bot_allowlist"] or "")
    ok = _save_global_notify_settings(cfg)
    if ok:
        _apply_tg_bot_config()
    return jsonify({
        "ok": ok,
        "settings": {
            "tg_bot_enabled": cfg.get("tg_bot_enabled", False),
            "tg_bot_token_set": bool(cfg.get("tg_bot_token", "")),
            "tg_bot_allowlist": cfg.get("tg_bot_allowlist", ""),
        },
    })


@tg_bp.route("/api/tg/test", methods=["POST"])
def api_tg_test():
    """Send a manual test message to all allowlisted chat IDs."""
    _TG_BOT_AVAILABLE = _app__TG_BOT_AVAILABLE()
    _tg_bot = _app__tg_bot()
    _check_csrf()
    if not (_TG_BOT_AVAILABLE and _tg_bot is not None):
        return jsonify({"ok": False, "error": "tg_bot module unavailable"})
    body = request.get_json(silent=True) or {}
    msg = body.get("message",
                   "BulkDownloader test message — bot wiring confirmed.")
    cfg = _load_global_notify_settings()
    token = str(cfg.get("tg_bot_token", "") or "")
    allowlist = _tg_bot.parse_allowlist(cfg.get("tg_bot_allowlist", ""))
    if not token or not allowlist:
        return jsonify({"ok": False,
                        "error": "token or allowlist missing"})
    sent = 0
    errors = []
    bot = _tg_bot.get_bot()
    for chat_id in allowlist:
        try:
            bot._send_message(int(chat_id),
                              _tg_bot._html_escape(msg))
            sent += 1
        except Exception as e:
            errors.append({"chat_id": chat_id, "error": str(e)[:200]})
    return jsonify({
        "ok": sent > 0,
        "sent_count": sent,
        "failed_count": len(allowlist) - sent,
        "errors": errors,
    })

def register_routes(app) -> int:
    app.register_blueprint(tg_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("tg."))

