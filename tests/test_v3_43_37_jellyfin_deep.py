"""v3.43.35 regression tests — deep Jellyfin integration.

Mirror of test_v3_43_29_plex_deep.py — same coverage shape since
Jellyfin deep is the parallel feature.
"""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
class _AggregateSrc:
    """Reads runner.py + runner_integrations.py concatenated. The integration
    methods (stash/plex/jellyfin/qb/jd) moved to runner_integrations.py at
    v3.66.398 (Phase 3 decomposition cut 2, pure motion); the orchestration call
    sites that spawn enrichment stay in runner.py core. Reading both preserves
    every source-level assertion across the move (core-first ordering)."""
    def __init__(self, *paths):
        self._paths = paths
    def read_text(self, encoding="utf-8"):
        import pathlib as _pl
        _d = _pl.Path(self._paths[0]).parent
        _files = [_d / "runner.py"] + sorted(_d.glob("runner_*.py"))
        return "\n".join(p.read_text(encoding=encoding) for p in _files)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader" / "runner.py",
                           _REPO_ROOT / "bulk_downloader" / "runner_integrations.py")
_JF_DEEP_PY = _REPO_ROOT / "bulk_downloader" / "jellyfin_deep.py"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_jellyfin_deep_module_importable():
    import bulk_downloader.jellyfin_deep as jd  # noqa: F401


def test_jellyfin_client_requires_both_url_and_api_key():
    """Jellyfin requires BOTH the URL and api key. Without the key,
    every endpoint returns 401."""
    from bulk_downloader.jellyfin_deep import JellyfinClient
    assert JellyfinClient("", "").configured is False
    assert JellyfinClient("http://jf:8096", "").configured is False
    assert JellyfinClient("", "abc").configured is False
    assert JellyfinClient("http://jf:8096", "abc").configured is True


def test_jellyfin_url_helper_excludes_api_key():
    """F-COREBD11-02: the api_key travels in the X-Emby-Token header, not
    the query string -- so it must NOT appear in the composed URL (no leak
    to access logs / Referer / proxy request lines). Other params still
    pass through."""
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("http://jf:8096", "abc123")
    url = c._url("/System/Info")
    assert "abc123" not in url
    assert "api_key" not in url
    assert url == "http://jf:8096/System/Info"
    # With extra params: they pass through; the key still does not.
    url2 = c._url("/Items", {"Limit": "10"})
    assert "Limit=10" in url2
    assert "abc123" not in url2
    assert "api_key" not in url2


def _jf_capture_request():
    """Drive one _request through a swapped urlopen; return the Request
    object so tests can inspect the real URL + headers."""
    import json as _json
    import urllib.request as _ur
    from bulk_downloader.jellyfin_deep import JellyfinClient

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps({"ok": True}).encode()

    real = _ur.urlopen
    def fake(req, timeout=None):
        captured["req"] = req
        return _Resp()
    _ur.urlopen = fake
    try:
        JellyfinClient("https://jf.demo", "SECRETKEY123")._request(
            "GET", "/System/Info")
    finally:
        _ur.urlopen = real
    return captured["req"]


def test_jellyfin_request_api_key_not_in_url():
    """F-COREBD11-02: a live _request must not carry the key in the URL."""
    req = _jf_capture_request()
    assert "SECRETKEY123" not in req.full_url


def test_jellyfin_request_api_key_in_emby_token_header():
    """F-COREBD11-02: the key must ride the X-Emby-Token header instead."""
    req = _jf_capture_request()
    val = req.get_header("X-emby-token") or req.headers.get("X-Emby-Token")
    assert val == "SECRETKEY123"


def test_deep_enabled_requires_all_three():
    from bulk_downloader.jellyfin_deep import deep_enabled
    assert deep_enabled({}) is False
    assert deep_enabled({"jellyfin_url": "http://x"}) is False
    assert deep_enabled({"jellyfin_url": "http://x",
                          "jellyfin_api_key": "k"}) is False, (
        "URL+key alone shouldn't enable deep features"
    )
    assert deep_enabled({
        "jellyfin_url": "http://x",
        "jellyfin_api_key": "k",
        "jellyfin_deep_enabled": True,
    }) is True


def test_jellyfin_diagnose_handles_unreachable():
    """Unreachable server returns structured result, not raise."""
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("http://127.0.0.1:1", "fake-key")
    result = c.diagnose()
    assert result["ok"] is False
    assert result["error"]


def test_jellyfin_diagnose_handles_no_config():
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("", "")
    result = c.diagnose()
    assert result["ok"] is False
    assert "not configured" in (result["error"] or "").lower()


def test_jellyfin_diagnose_does_not_leak_api_key():
    """API key must NEVER appear in the diagnose response — that's
    leaked to anyone who can hit /api/sites/<sid>/jellyfin/diagnose."""
    from bulk_downloader.jellyfin_deep import JellyfinClient
    secret = "super-secret-key-not-leaked"
    c = JellyfinClient("http://127.0.0.1:1", secret)
    result = c.diagnose()
    # Convert the whole response to string and check
    import json
    body = json.dumps(result)
    assert secret not in body, (
        f"API key leaked in diagnose response: {body}"
    )


def test_jellyfin_read_methods_return_safe_defaults_on_failure():
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("http://127.0.0.1:1", "abc")
    assert c.list_libraries() == []
    assert c.find_item_by_path("user-1", "/x/y.mp4",
                                timeout_s=0.5) is None
    assert c.find_collection_by_name("user-1", "Test") is None


def test_jellyfin_write_methods_return_false_on_failure():
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("http://127.0.0.1:1", "abc")
    assert c.refresh_item("42") is False
    assert c.refresh_library() is False
    assert c.add_to_collection("99", "42") is False
    assert c.create_collection("Test", "42") is None


def test_create_collection_skipped_with_empty_name():
    """An empty name shouldn't ping the server."""
    from bulk_downloader.jellyfin_deep import JellyfinClient
    c = JellyfinClient("http://127.0.0.1:1", "abc")
    assert c.create_collection("") is None


def test_build_open_in_jellyfin_url():
    from bulk_downloader.jellyfin_deep import build_open_in_jellyfin_url
    assert "abc123" in build_open_in_jellyfin_url(
        "http://jf:8096", "abc123")
    assert build_open_in_jellyfin_url("", "abc") == ""
    assert build_open_in_jellyfin_url("http://jf:8096", "") == ""


def test_summarize_item():
    from bulk_downloader.jellyfin_deep import summarize_item
    assert summarize_item(None) == ""
    assert summarize_item({}) == ""
    s = summarize_item({"name": "Test Movie", "year": 2024})
    assert "Test Movie" in s
    assert "2024" in s
    assert "[unmatched]" in summarize_item({"name": "X", "unmatched": True})


def test_jellyfin_error_carries_kind():
    from bulk_downloader.jellyfin_deep import JellyfinError
    e = JellyfinError("auth", "test", {"hint": "fix"})
    assert e.kind == "auth"
    assert e.detail == {"hint": "fix"}


# ── DEFAULTS / CFG_FIELDS ─────────────────────────────────────────────

def test_jellyfin_deep_defaults_present():
    src = _APP_SRC()
    assert '"jellyfin_deep_enabled": False' in src
    assert '"jellyfin_user_id": ""' in src
    assert '"jellyfin_match_confirm": False' in src
    assert '"jellyfin_collection": ""' in src
    assert '"jellyfin_auto_create_collection": False' in src


def test_jellyfin_deep_fields_in_cfg_fields():
    src = _APP_SRC()
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[": depth += 1
        elif src[pos] == "]": depth -= 1
        pos += 1
    block = src[start:pos - 1]
    for fld in ("jellyfin_deep_enabled", "jellyfin_user_id",
                 "jellyfin_match_confirm", "jellyfin_collection",
                 "jellyfin_auto_create_collection"):
        assert f'"{fld}"' in block, f"{fld} missing from CFG_FIELDS"


# ── Runner integration ───────────────────────────────────────────────

def test_runner_has_jellyfin_methods():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    for name in ("_get_jellyfin_client", "_jellyfin_enrich_after_scan"):
        assert f"def {name}" in src, f"SiteRunner.{name} missing"


def test_jellyfin_enrichment_fires_after_completed():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    fe_pos = src.find('fire_event("completed"')
    assert fe_pos > 0
    nearby = src[fe_pos:fe_pos + 4500]
    assert "_jellyfin_enrich_after_scan" in nearby
    assert "jellyfin-enrich-" in nearby


def test_jellyfin_enrich_skips_when_user_id_missing():
    """User ID is required for path-scoped queries. Without it,
    method must skip cleanly with a log entry."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _jellyfin_enrich_after_scan")
    body = src[method_pos:method_pos + 5000]
    assert "jellyfin_user_id" in body


def test_jellyfin_collection_routing_in_enrichment():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _jellyfin_enrich_after_scan")
    body = src[method_pos:method_pos + 5000]
    assert "find_collection_by_name" in body
    assert "create_collection" in body
    assert "add_to_collection" in body
    assert "jellyfin_auto_create_collection" in body


# ── Endpoints ────────────────────────────────────────────────────────

def test_jellyfin_diagnose_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/jellyfin/diagnose' in src
    assert "def api_jellyfin_diagnose" in src


def test_jellyfin_libraries_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/jellyfin/libraries' in src
    assert "def api_jellyfin_libraries" in src


# ── UI ───────────────────────────────────────────────────────────────





