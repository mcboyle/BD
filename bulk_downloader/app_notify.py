"""notify API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/notify views moved onto a Flask Blueprint.
Endpoint labels gain a "notify." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_NOTIFY_APPRISE_AVAILABLE, _notify_apprise) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

notify_bp = Blueprint("notify", __name__)

def _apply_global_notify_config(*_a, **_k):
    """Delegate to app._apply_global_notify_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_apply_global_notify_config")(*_a, **_k)

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

def _app__NOTIFY_APPRISE_AVAILABLE():
    """The live shared _NOTIFY_APPRISE_AVAILABLE from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_NOTIFY_APPRISE_AVAILABLE")

def _app__notify_apprise():
    """The live shared _notify_apprise from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_notify_apprise")


_NOTIFY_PRESETS = [
    {
        "id": "pushover", "name": "Pushover",
        "template": "pover://{user_key}@{app_token}",
        "fields": [
            {"key": "user_key", "label": "User key",
             "hint": "30-char key from your Pushover dashboard"},
            {"key": "app_token", "label": "Application token",
             "hint": "Create an application at pushover.net/apps"},
        ],
        "docs": "https://github.com/caronc/apprise/wiki/Notify_pushover",
    },
    {
        "id": "discord", "name": "Discord",
        "template": "discord://{webhook_id}/{webhook_token}",
        "fields": [
            {"key": "webhook_id", "label": "Webhook ID",
             "hint": "First path segment of the webhook URL"},
            {"key": "webhook_token", "label": "Webhook token",
             "hint": "Second path segment of the webhook URL"},
        ],
        "docs": "https://github.com/caronc/apprise/wiki/Notify_discord",
    },
    {
        "id": "slack", "name": "Slack",
        "template": "slack://{token_a}/{token_b}/{token_c}",
        "fields": [
            {"key": "token_a", "label": "Token A",
             "hint": "First segment of the Slack incoming-webhook token"},
            {"key": "token_b", "label": "Token B",
             "hint": "Second segment"},
            {"key": "token_c", "label": "Token C",
             "hint": "Third segment"},
        ],
        "docs": "https://github.com/caronc/apprise/wiki/Notify_slack",
    },
    {
        "id": "telegram", "name": "Telegram",
        "template": "tgram://{bot_token}/{chat_id}",
        "fields": [
            {"key": "bot_token", "label": "Bot token",
             "hint": "Token from @BotFather"},
            {"key": "chat_id", "label": "Chat ID",
             "hint": "Your numeric chat/user ID"},
        ],
        "docs": "https://github.com/caronc/apprise/wiki/Notify_telegram",
    },
]


@notify_bp.route("/api/notify/apprise/settings", methods=["GET"])
def api_notify_apprise_settings_get():
    """Return current global apprise settings.

    T7 (v3.66.210): apprise URLs embed bot tokens / webhook secrets, so they
    are write-only — the raw ``notify_apprise_urls`` are NEVER echoed back
    (PREP_AUDIT §8 found the legacy handler leaked them RAW into the GET).
    The response surfaces a set-flag + count (the ``tg_bot_token_set``
    pattern); the SPA edits them via a write-only paste field that POSTs
    replacements.
    """
    _NOTIFY_APPRISE_AVAILABLE = _app__NOTIFY_APPRISE_AVAILABLE()
    _notify_apprise = _app__notify_apprise()
    cfg = dict(_load_global_notify_settings())
    raw_urls = cfg.pop("notify_apprise_urls", "")
    if isinstance(raw_urls, str):
        url_list = [u for u in raw_urls.splitlines() if u.strip()]
    elif isinstance(raw_urls, (list, tuple)):
        url_list = [u for u in raw_urls if str(u).strip()]
    else:
        url_list = []
    cfg["notify_apprise_urls_set"] = bool(url_list)
    cfg["notify_apprise_urls_count"] = len(url_list)
    avail = _NOTIFY_APPRISE_AVAILABLE and (
        _notify_apprise.is_available() if _notify_apprise else False)
    return jsonify({
        "ok": True,
        "available": bool(avail),
        "settings": cfg,
    })


@notify_bp.route("/api/notify/apprise/settings", methods=["POST"])
def api_notify_apprise_settings_post():
    """Save global apprise settings + apply to running dispatcher."""
    _check_csrf()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "expected JSON object"}), 400
    cfg = _load_global_notify_settings()
    # Only update keys we recognize
    allowed_keys = {
        "notify_apprise_enabled", "notify_apprise_urls",
        "notify_download_done_mode", "notify_download_done_batch",
        "notify_download_done_wait_s",
        "notify_download_failed_mode", "notify_download_failed_batch",
        "notify_download_failed_wait_s",
        "notify_captcha_mode", "notify_auth_required_mode",
        "notify_disk_full_mode", "notify_queue_empty_mode",
        "notify_queue_paused_mode", "notify_queue_resumed_mode",
        "notify_server_start_mode", "notify_server_shutdown_mode",
    }
    for k, v in body.items():
        if k in allowed_keys:
            cfg[k] = v
    ok = _save_global_notify_settings(cfg)
    if ok:
        _apply_global_notify_config()
    return jsonify({"ok": ok, "settings": cfg})


@notify_bp.route("/api/notify/apprise/validate", methods=["POST"])
def api_notify_apprise_validate():
    """Validate a list of apprise URLs without sending. Returns per-URL
    ok/schema/error."""
    _NOTIFY_APPRISE_AVAILABLE = _app__NOTIFY_APPRISE_AVAILABLE()
    _notify_apprise = _app__notify_apprise()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    text = body.get("urls", "") or ""
    if _NOTIFY_APPRISE_AVAILABLE and _notify_apprise is not None:
        urls = _notify_apprise.parse_urls_text(text)
        results = _notify_apprise.validate_urls(urls)
        return jsonify({
            "ok": True,
            "available": _notify_apprise.is_available(),
            "results": [
                {"url": _notify_apprise._safe_url_display(r.url),
                 "ok": r.ok, "schema": r.schema, "error": r.error}
                for r in results
            ],
        })
    return jsonify({"ok": False, "available": False,
                    "error": "apprise module unavailable"})


@notify_bp.route("/api/notify/presets")
def api_notify_presets():
    """#91 — pre-built notification URL templates for common services.

    Each preset is {id, name, template, fields[], docs}. The template
    has `{placeholder}` slots matching the field keys; the UI collects
    the fields and substitutes them to build a valid apprise URL."""
    _NOTIFY_APPRISE_AVAILABLE = _app__NOTIFY_APPRISE_AVAILABLE()
    _notify_apprise = _app__notify_apprise()
    available = bool(_NOTIFY_APPRISE_AVAILABLE and _notify_apprise)
    return jsonify({"ok": True, "available": available,
                    "presets": _NOTIFY_PRESETS})


@notify_bp.route("/api/notify/apprise/test", methods=["POST"])
def api_notify_apprise_test():
    """Send a test notification to all configured URLs. Returns per-URL
    success/failure summary."""
    _NOTIFY_APPRISE_AVAILABLE = _app__NOTIFY_APPRISE_AVAILABLE()
    _notify_apprise = _app__notify_apprise()
    _check_csrf()
    body = request.get_json(silent=True) or {}
    title = body.get("title", "BulkDownloader test") or "BulkDownloader test"
    msg = body.get("body", "This is a test notification.") or \
          "This is a test notification."
    if not (_NOTIFY_APPRISE_AVAILABLE and _notify_apprise is not None):
        return jsonify({"ok": False, "error": "apprise module unavailable"})
    cfg = _load_global_notify_settings()
    urls = _notify_apprise.parse_urls_text(cfg.get("notify_apprise_urls", ""))
    if not urls:
        return jsonify({
            "ok": False,
            "error": "no apprise URLs configured",
        })
    result = _notify_apprise.send(urls, title=title, body=msg, tag="test")
    return jsonify({
        "ok": result.sent_count > 0,
        "sent_count": result.sent_count,
        "failed_count": result.failed_count,
        "skipped_count": result.skipped_count,
        "errors": [{"url": u, "error": e} for u, e in result.errors],
    })


@notify_bp.route("/api/notify/apprise/schemes", methods=["GET"])
def api_notify_apprise_schemes():
    """List recognized apprise schemes (for the UI to show in a hint)."""
    _NOTIFY_APPRISE_AVAILABLE = _app__NOTIFY_APPRISE_AVAILABLE()
    _notify_apprise = _app__notify_apprise()
    if not (_NOTIFY_APPRISE_AVAILABLE and _notify_apprise is not None):
        return jsonify({"ok": False, "available": False, "schemes": []})
    schemes = _notify_apprise.list_known_schemes()
    return jsonify({"ok": True, "available": _notify_apprise.is_available(),
                    "schemes": schemes})

def register_routes(app) -> int:
    app.register_blueprint(notify_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("notify."))

