"""v3.43.29 regression tests — deep Plex integration.

Tests the plex_deep module + runner integration. Same shape as the
v3.43.28 Stash deep test file since Plex deep is the mirror feature.

Functional tests against a real Plex server require live integration
testing; here we cover:
  - Module surface
  - Defensive handling of bad inputs
  - Configuration helpers
  - Runner integration (method exists, enrichment fires on completed)
  - App wiring (DEFAULTS, CFG_FIELDS, endpoints)
  - UI: modal fields, handlers
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
_PLEX_DEEP_PY = _REPO_ROOT / "bulk_downloader" / "plex_deep.py"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_plex_deep_module_importable():
    import bulk_downloader.plex_deep as pd  # noqa: F401


def test_plex_client_construction_handles_empty_config():
    """Empty URL/token is the common case — most sites don't use Plex.
    Must initialize without errors and report configured=False."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="", token="")
    assert c.configured is False
    # Just one of the two also = not configured (need both)
    c2 = PlexClient(base_url="http://plex:32400", token="")
    assert c2.configured is False
    c3 = PlexClient(base_url="", token="abc")
    assert c3.configured is False


def test_plex_client_requires_both_url_and_token():
    """Plex requires BOTH url and token. Unlike Stash where API key
    is optional for some endpoints, Plex needs the token for every
    call."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://plex:32400", token="abc")
    assert c.configured is True


def test_plex_url_helper_appends_token():
    """URL composition must include the X-Plex-Token param. Without
    it, Plex returns 401."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://plex:32400", token="abc123")
    url = c._url("/library/sections")
    assert "X-Plex-Token=abc123" in url
    # Existing query string is preserved
    url2 = c._url("/library/sections", {"key": "value"})
    assert "key=value" in url2
    assert "X-Plex-Token=abc123" in url2


def test_deep_enabled_default_false():
    """Sites must explicitly opt in to deep Plex features. Just
    having plex_url+plex_token (which many sites have for the basic
    refresh) isn't enough."""
    from bulk_downloader.plex_deep import deep_enabled
    assert deep_enabled({}) is False
    assert deep_enabled({"plex_url": "http://x"}) is False
    assert deep_enabled({"plex_url": "http://x", "plex_token": "t"}) is False, (
        "URL+token alone shouldn't enable deep features; "
        "the user has to explicitly opt in"
    )
    # Flag without URL/token still False
    assert deep_enabled({"plex_deep_enabled": True}) is False
    # All three required
    assert deep_enabled({
        "plex_url": "http://x",
        "plex_token": "t",
        "plex_deep_enabled": True,
    }) is True


def test_plex_diagnose_handles_unreachable():
    """Unreachable Plex returns structured result, not raise."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://127.0.0.1:1", token="abc")
    result = c.diagnose()
    assert "ok" in result
    assert result["ok"] is False
    assert result["error"]


def test_plex_diagnose_handles_no_config():
    """Blank config produces 'not configured' result, not crash."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="", token="")
    result = c.diagnose()
    assert result["ok"] is False
    assert "not configured" in (result["error"] or "").lower()
    # Just missing token
    c2 = PlexClient(base_url="http://plex:32400", token="")
    result2 = c2.diagnose()
    assert result2["ok"] is False
    assert "token" in (result2["error"] or "").lower()


def test_plex_read_methods_return_none_on_failure():
    """All read methods must return None on failure (any kind),
    never raise. The runner's hot path can't afford exception
    handling on every URL."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://127.0.0.1:1", token="abc")
    # All these hit an unreachable port
    assert c.find_by_path("3", "/some/path.mp4") is None
    assert c.find_collection_by_title("3", "Test") is None
    assert c.list_sections() == []


def test_plex_write_methods_return_false_on_failure():
    """Write methods must return False (not raise) on failure so the
    runner can decide whether to retry or move on."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://127.0.0.1:1", token="abc")
    assert c.refresh_path("3", "/some/path.mp4") is False
    assert c.update_added_at("42", "3") is False
    assert c.add_to_collection("99", "3", "42") is False
    assert c.create_collection("3", "Test", "42") is None


def test_create_collection_skipped_without_seed_item():
    """Plex requires at least one item to create a collection. We
    skip the call rather than 400."""
    from bulk_downloader.plex_deep import PlexClient
    c = PlexClient(base_url="http://127.0.0.1:1", token="abc")
    # No seed item — must return None without attempting the call
    assert c.create_collection("3", "Test", "") is None


def test_build_open_in_plex_url():
    """Deep-link URL generation for the queue context menu."""
    from bulk_downloader.plex_deep import build_open_in_plex_url
    # With machine ID
    url = build_open_in_plex_url("http://plex:32400", "42",
                                   "abc123machine")
    assert "abc123machine" in url
    assert "42" in url
    # Without machine ID — fallback URL still references the item
    url2 = build_open_in_plex_url("http://plex:32400", "42")
    assert "42" in url2
    # Empty inputs return empty
    assert build_open_in_plex_url("", "42") == ""
    assert build_open_in_plex_url("http://plex:32400", "") == ""


def test_summarize_item():
    from bulk_downloader.plex_deep import summarize_item
    assert "Test" in summarize_item({"title": "Test"})
    assert "[unmatched]" in summarize_item({"title": "T",
                                              "unmatched": True})
    assert summarize_item(None) == ""
    assert summarize_item({}) == ""


def test_plex_error_carries_kind():
    """Runner branches on .kind, so the exception must carry it."""
    from bulk_downloader.plex_deep import PlexError
    e = PlexError("auth", "test", {"hint": "fix"})
    assert e.kind == "auth"
    assert e.message == "test"
    assert e.detail == {"hint": "fix"}


# ── DEFAULTS / CFG_FIELDS ────────────────────────────────────────────

def test_plex_deep_defaults_present():
    src = _APP_SRC()
    assert '"plex_deep_enabled": False' in src
    assert '"plex_match_confirm": False' in src
    assert '"plex_recently_added_boost": False' in src
    assert '"plex_collection": ""' in src
    assert '"plex_auto_create_collection": False' in src


def test_plex_deep_fields_in_cfg_fields():
    """Without these in CFG_FIELDS, the values would be dropped on
    every config reload."""
    src = _APP_SRC()
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    assert m
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        pos += 1
    fields_block = src[start:pos - 1]
    for fld in ("plex_deep_enabled", "plex_match_confirm",
                 "plex_recently_added_boost", "plex_collection",
                 "plex_auto_create_collection"):
        assert f'"{fld}"' in fields_block, f"{fld} missing from CFG_FIELDS"


# ── Runner integration ───────────────────────────────────────────────

def test_runner_has_plex_methods():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    for name in ("_get_plex_client", "_plex_enrich_after_scan"):
        assert f"def {name}" in src, f"SiteRunner.{name} missing"


def test_plex_enrichment_fires_after_completed():
    """The Plex enrichment must trigger from the completed event,
    same as Stash enrichment."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    fe_pos = src.find('fire_event("completed"')
    assert fe_pos > 0
    nearby = src[fe_pos:fe_pos + 3000]
    assert "_plex_enrich_after_scan" in nearby, (
        "Plex enrichment must trigger from completed event"
    )
    assert "plex-enrich-" in nearby, "Thread name should identify plex"


def test_plex_enrich_skips_when_section_id_missing():
    """plex_section_id is required for path-scoped operations. Without
    it, the method must skip cleanly with a log entry, not crash."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _plex_enrich_after_scan")
    assert method_pos > 0
    method_body = src[method_pos:method_pos + 5000]
    assert "plex_section_id" in method_body
    assert "return" in method_body, (
        "Method must early-return when section_id missing"
    )


def test_plex_enrich_handles_unmatched():
    """Match confirmation must log a warning (not crash) when Plex
    leaves the file unmatched."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _plex_enrich_after_scan")
    method_body = src[method_pos:method_pos + 5000]
    assert "unmatched" in method_body.lower()
    assert "plex_match_confirm" in method_body


def test_plex_enrich_runs_recently_added_boost_conditionally():
    """The boost only fires when the user opts in."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _plex_enrich_after_scan")
    method_body = src[method_pos:method_pos + 5000]
    assert "plex_recently_added_boost" in method_body
    assert "update_added_at" in method_body


def test_plex_enrich_handles_collection_routing():
    """Collection routing must:
      1. Try to find existing collection by name
      2. Create it if missing AND auto_create_collection is on
      3. Skip silently when not configured
      4. Add the new item to the collection
    """
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _plex_enrich_after_scan")
    method_body = src[method_pos:method_pos + 5000]
    assert "find_collection_by_title" in method_body
    assert "create_collection" in method_body
    assert "add_to_collection" in method_body
    assert "plex_auto_create_collection" in method_body


# ── App endpoints ────────────────────────────────────────────────────

def test_plex_diagnose_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/plex/diagnose' in src
    assert "def api_plex_diagnose" in src


def test_plex_sections_endpoint_registered():
    """For the UI's 'List sections' button — populates a list of
    libraries so the user can pick a section ID without spelunking."""
    src = _APP_SRC()
    assert '/api/sites/<sid>/plex/sections' in src
    assert "def api_plex_sections" in src


# ── UI ────────────────────────────────────────────────────────────────





