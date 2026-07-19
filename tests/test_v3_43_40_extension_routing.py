"""v3.43.40 regression tests — extension URL routing improvements.

Coverage:
  - _score_url_against_sites: per-(url, site) scoring + reason text
  - /api/sites_list: shape, no secrets leaked
  - /api/route_preview: dry-run routing returns decisions
  - /api/sites/<sid>/queue_url: explicit-site bypass routing
  - Extension manifest v1.2.0: commands, tabs permission
  - background.js: site cache, submenu rebuild, message handlers
  - popup.js: preview wired on open, send-all-tabs button,
    override picker
"""
from __future__ import annotations

import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
import re as _re
class _AppAggregateSrc:
    """app.py + every app_*.py blueprint module (PHASE 4 decomposition: route
    groups moved onto Flask blueprints); glob keeps source-coupled guards green
    across all current and future app.py route-group cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "app.py"] + sorted(pkg_dir.glob("app_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_APP_PY = _AppAggregateSrc(_REPO_ROOT / "bulk_downloader")
_BG_JS = _REPO_ROOT / "extension" / "background.js"
_POPUP_JS = _REPO_ROOT / "extension" / "popup.js"
_POPUP_HTML = _REPO_ROOT / "extension" / "popup.html"
_MANIFEST_JSON = _REPO_ROOT / "extension" / "manifest.json"


# ── Server: helper function ──────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_score_helper_function_exists():
    """The extracted scoring helper must be in app.py — both
    _route_urls_internal and the new preview endpoint depend on it."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "def _score_url_against_sites" in src


def test_route_urls_internal_uses_helper():
    """_route_urls_internal must delegate to _score_url_against_sites
    rather than carry its own copy of the scoring loop."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def _route_urls_internal")
    end = src.find("\ndef ", pos + 50)
    body = src[pos:end]
    assert "_score_url_against_sites" in body


def test_score_helper_returns_reason():
    """Manually call the helper with a synthesized site config and
    verify it returns (sid, score, reason) tuple shape."""
    # Import within the test to ensure the function exists at runtime,
    # not just textually
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", {"name": "Site A", "login_url": "https://wowgirls.com/login"}),
        ("site_b", {"name": "Site B", "url_patterns": "ultrafilms\\.com"}),
    ]
    sid, score, reason = _app._score_url_against_sites(
        "https://wowgirls.com/video/123", cfg_snapshot)
    assert sid == "site_a"
    assert score >= 100
    assert "hostname" in reason.lower() or "login_url" in reason


def test_score_helper_pattern_beats_hostname():
    """A url_patterns regex match (200) must beat a hostname match
    (100) — explicit user-defined rules trump inferred ones."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", {"login_url": "https://wowgirls.com/login"}),
        ("site_b", {"url_patterns": "wowgirls\\.com/video"}),
    ]
    sid, score, reason = _app._score_url_against_sites(
        "https://wowgirls.com/video/123", cfg_snapshot)
    # site_b wins because url_patterns scores 200 vs site_a's 100
    assert sid == "site_b"
    assert score >= 200


def test_score_helper_no_match_returns_zero():
    """When no site matches, the helper returns score=0 (or less)."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", {"login_url": "https://wowgirls.com/login"}),
    ]
    sid, score, reason = _app._score_url_against_sites(
        "https://unrelated-site.com/page", cfg_snapshot)
    # No match → score is at or below 0 (or tiny tie-breaker)
    assert score < 50


def test_score_helper_handles_bad_regex():
    """A site with an invalid url_patterns regex must not crash —
    the bad pattern is silently skipped."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_bad", {"url_patterns": "[invalid("}),
    ]
    # Must not raise
    sid, score, reason = _app._score_url_against_sites(
        "https://example.com/", cfg_snapshot)


# ── Server: new endpoints ────────────────────────────────────────────

def test_sites_list_endpoint_registered():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "/api/sites_list" in src
    assert "def api_sites_list" in src


def test_sites_list_excludes_secrets():
    """The endpoint must return only id/name/hostname/patterns —
    NOT username, password, cookie_file, api_key, or anything
    that could leak credentials. The extension is a paired
    client but the sites list is sent to all browser tabs."""
    src = _APP_SRC()
    pos = src.find("def api_sites_list")
    # Bound the body at the next top-level def or decorator after it. The
    # handler now lives in app_sites_list.py on a Flask blueprint, so the next
    # construct is `def register_routes` (decorator is @sites_list_bp.route, not
    # @app.route); bounding on the next "\ndef " or "\n@" -- whichever comes
    # first -- captures just this handler's body whether it sits in app.py or an
    # extracted blueprint module. (PHASE 4: never bound a moved handler's body on
    # a bare "@app.route" literal -- blueprint modules don't use it.)
    _cands = [c for c in (src.find("\n@", pos + 1), src.find("\ndef ", pos + 1)) if c != -1]
    end = min(_cands) if _cands else len(src)
    body = src[pos:end]
    # Must NOT mention secret-bearing fields in the returned shape
    for forbidden in ("password", "api_key", "cookie_file",
                        "captcha_api_key"):
        # These shouldn't appear in the output construction. Allow
        # them in COMMENTS but not in the returned dict.
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("#"))
        assert forbidden not in body_no_comments, (
            f"sites_list endpoint references {forbidden} — "
            f"could leak it to extension")


def test_route_preview_endpoint_registered():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "/api/route_preview" in src
    assert "def api_route_preview" in src


def test_route_preview_accepts_batch():
    """The preview endpoint must accept both single url and
    batch urls — same shape as route_urls/quick_add."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def api_route_preview")
    end = src.find("\n@app.route", pos + 1)
    body = src[pos:end]
    # Looks for both 'urls' (batch) and 'url' (single) handling
    assert "urls" in body
    assert "url" in body


def test_route_preview_reports_reason():
    """Each decision must include the `reason` field so the
    extension can show 'matched url_patterns: X' to the user."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def api_route_preview")
    end = src.find("\n@app.route", pos + 1)
    body = src[pos:end]
    assert '"reason"' in body
    # And uses the shared helper rather than re-implementing scoring
    assert "_score_url_against_sites" in body


def test_route_preview_reports_already_in_queue():
    """If the URL is already in any site's queue, the decision
    must say so — protects against double-queueing from share
    sheet retries."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def api_route_preview")
    end = src.find("\n@app.route", pos + 1)
    body = src[pos:end]
    assert "already_in_queue" in body
    assert "in_queue" in body


def test_queue_url_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/queue_url" in src
    assert "def api_site_queue_url" in src


def test_queue_url_bypasses_routing():
    """The endpoint must call load_urls directly on the specified
    site, not route through _score_url_against_sites — the whole
    point is explicit user override."""
    src = _APP_SRC()
    pos = src.find("def api_site_queue_url")
    _e = _re.search(r"\n@\w", src[pos + 1:])
    end = pos + 1 + _e.start() if _e else len(src)
    body = src[pos:end]
    assert "runners[sid].load_urls" in body
    # Must NOT score the URL — that would defeat the override
    assert "_score_url_against_sites" not in body


def test_queue_url_returns_404_for_unknown_site():
    """Defensive — invalid site_id must return 404, not crash."""
    src = _APP_SRC()
    pos = src.find("def api_site_queue_url")
    _e = _re.search(r"\n@\w", src[pos + 1:])
    end = pos + 1 + _e.start() if _e else len(src)
    body = src[pos:end]
    assert "404" in body


def test_queue_url_requires_http_urls():
    """Filter to http(s) URLs — chrome:// and about: pages have
    no download value."""
    src = _APP_SRC()
    pos = src.find("def api_site_queue_url")
    _e = _re.search(r"\n@\w", src[pos + 1:])
    end = pos + 1 + _e.start() if _e else len(src)
    body = src[pos:end]
    assert "startswith(\"http\")" in body or 'startswith("http")' in body


# ── Manifest ─────────────────────────────────────────────────────────

def test_manifest_version_bumped():
    manifest = json.loads(_MANIFEST_JSON.read_text(encoding="utf-8"))
    # v3.45.2 Phase 178: bumped to 1.3.0 for live status badge
    assert manifest["version"] == "1.3.0"


def test_manifest_has_tabs_permission():
    """send_all_tabs needs chrome.tabs.query — that requires tabs
    permission."""
    manifest = json.loads(_MANIFEST_JSON.read_text(encoding="utf-8"))
    assert "tabs" in manifest["permissions"]


def test_manifest_has_commands():
    """Keyboard shortcuts must be declared in the manifest."""
    manifest = json.loads(_MANIFEST_JSON.read_text(encoding="utf-8"))
    commands = manifest.get("commands", {})
    assert "send-current-page" in commands
    assert "send-all-tabs" in commands
    # Each command must have a description (Chrome UI requirement)
    for name, cmd in commands.items():
        assert "description" in cmd, f"command {name} missing description"


def test_manifest_command_keys_alt_shift():
    """Defaults must be Alt+Shift+X to avoid clashing with common
    Ctrl+Shift+ bindings used by sites and browser features."""
    manifest = json.loads(_MANIFEST_JSON.read_text(encoding="utf-8"))
    for cmd in manifest.get("commands", {}).values():
        keys = cmd.get("suggested_key", {})
        if "default" in keys:
            assert "Alt" in keys["default"]


# ── Background.js ────────────────────────────────────────────────────

def test_background_has_sites_cache():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "_sitesCache" in src
    assert "refreshSitesCache" in src
    # Cache TTL is bounded
    assert "SITES_CACHE_TTL_MS" in src


def test_background_rebuilds_submenu():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "rebuildSiteSubmenu" in src
    # Must call chrome.contextMenus.create with parentId pointing
    # at bd-send-to-specific
    assert "bd-send-to-specific" in src


def test_background_handles_per_site_submenu_clicks():
    """The onClicked handler must recognize the "bd-site-<sid>"
    pattern and route appropriately."""
    src = _BG_JS.read_text(encoding="utf-8")
    assert 'startsWith("bd-site-")' in src
    assert "sendUrlToSite" in src


def test_background_has_send_all_tabs():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "async function sendAllTabs" in src
    assert "chrome.tabs.query" in src
    # Must filter to http(s) — chrome:// pages have no download value
    assert 'startsWith("http")' in src


def test_background_has_keyboard_command_listener():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "chrome.commands.onCommand" in src
    assert "send-current-page" in src
    assert "send-all-tabs" in src


def test_background_has_preview_routing():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "async function previewRouting" in src
    assert "/api/route_preview" in src


def test_background_has_send_to_site():
    src = _BG_JS.read_text(encoding="utf-8")
    assert "async function sendUrlToSite" in src
    assert "/queue_url" in src


def test_background_message_router_has_new_actions():
    """The popup uses sendMessage to call into background; the
    new actions (route_preview, send_to_site, sites_list,
    send_all_tabs, send_url) must be wired."""
    src = _BG_JS.read_text(encoding="utf-8")
    pos = src.find("chrome.runtime.onMessage.addListener")
    body = src[pos:]
    for action in ("route_preview", "send_to_site", "sites_list",
                     "send_all_tabs", "send_url"):
        assert f'case "{action}"' in body, (
            f"message router missing handler for {action}")


def test_background_refreshes_submenu_on_url_change():
    """When the user updates the bd_url in options, the submenu must
    rebuild against the new server."""
    src = _BG_JS.read_text(encoding="utf-8")
    assert "chrome.storage.onChanged.addListener" in src
    # And the handler triggers a refresh
    pos = src.find("chrome.storage.onChanged.addListener")
    body = src[pos:pos + 500]
    assert "refreshSitesCache" in body
    assert "rebuildSiteSubmenu" in body


# ── Popup ────────────────────────────────────────────────────────────

def test_popup_has_route_preview_section():
    src = _POPUP_HTML.read_text(encoding="utf-8")
    assert 'id="route-preview"' in src
    assert 'id="route-preview-text"' in src


def test_popup_has_send_all_tabs_button():
    src = _POPUP_HTML.read_text(encoding="utf-8")
    assert 'id="send-all-tabs"' in src


def test_popup_has_override_picker():
    src = _POPUP_HTML.read_text(encoding="utf-8")
    assert 'id="override-site"' in src
    assert 'id="override-send"' in src


def test_popup_js_refreshes_preview_on_open():
    src = _POPUP_JS.read_text(encoding="utf-8")
    assert "async function refreshRoutePreview" in src
    # Must be called on popup load (last line of file)
    assert "refreshRoutePreview()" in src


def test_popup_js_wires_send_all_tabs():
    src = _POPUP_JS.read_text(encoding="utf-8")
    pos = src.find('getElementById("send-all-tabs")')
    assert pos > 0
    body = src[pos:pos + 1000]
    assert "send_all_tabs" in body


def test_popup_js_loads_sites_lazily():
    """The override picker must lazy-load — fetching on every popup
    open is wasteful when most users won't expand it."""
    src = _POPUP_JS.read_text(encoding="utf-8")
    assert 'getElementById("override-details")' in src
    pos = src.find('getElementById("override-details")')
    body = src[pos:pos + 1000]
    # Toggle event triggers the load
    assert "toggle" in body
    assert "loadOverrideSites" in body


def test_popup_js_escapes_html_in_preview():
    """User-controlled strings (site name, reason) MUST be
    escaped before being injected into innerHTML — site names
    could legitimately contain < or & characters."""
    src = _POPUP_JS.read_text(encoding="utf-8")
    # We use an esc() helper for HTML output
    assert "function esc" in src or "esc(" in src
    # And it's called when rendering preview
    pos = src.find("async function refreshRoutePreview")
    body = src[pos:pos + 3000]
    assert "esc(" in body


# ── Adversarial regression locks ────────────────────────────────────

def test_score_helper_handles_none_config_dict():
    """Audit catch: cfg snapshot with a None entry crashed `.get()`
    with AttributeError. Must skip silently."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", None),
        ("site_b", {"login_url": "https://example.com/"}),
    ]
    # Must not raise
    sid, score, reason = _app._score_url_against_sites(
        "https://example.com/x", cfg_snapshot)
    # site_b is the valid one and should win
    assert sid == "site_b"


def test_score_helper_handles_non_dict_cfg():
    """Strings or numbers in the cfg slot must be skipped, not crashed."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_bad1", "not a dict"),
        ("site_bad2", 12345),
        ("site_good", {"login_url": "https://example.com/"}),
    ]
    sid, score, reason = _app._score_url_against_sites(
        "https://example.com/", cfg_snapshot)
    assert sid == "site_good"


def test_score_helper_handles_non_string_patterns():
    """Audit catch: url_patterns=12345 (numeric) crashed `.strip()`
    with AttributeError. Must skip the pattern step."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", {"url_patterns": 12345,
                      "login_url": "https://example.com/"}),
    ]
    # Must not raise; falls back to login_url match
    sid, score, reason = _app._score_url_against_sites(
        "https://example.com/", cfg_snapshot)
    assert sid == "site_a"
    assert score >= 100


def test_score_helper_handles_non_string_login_url():
    """Audit catch: login_url=12345 crashed `.lower()` with
    AttributeError. Must skip that field."""
    import bulk_downloader.app as _app
    cfg_snapshot = [
        ("site_a", {"login_url": 12345,
                      "success_url": "https://example.com/done"}),
    ]
    # success_url should still match
    sid, score, reason = _app._score_url_against_sites(
        "https://example.com/", cfg_snapshot)
    assert sid == "site_a"
