"""v3.43.28 regression tests — deep Stash integration.

Tests the stash_deep module + runner integration. We can't reach a
real Stash server in test env, so most tests verify:
  - Module surface (importable, methods present)
  - Configuration helpers (deep_enabled, get_client_for_site)
  - URL/helper functions (build_open_in_stash_url, summarize_scrape)
  - Defensive handling of malformed inputs (StashError shape, None
    returns instead of crashes)
  - Runner integration (method exists, branch in _process_one,
    enrichment fires on completed)
  - App wiring (DEFAULTS, CFG_FIELDS, diagnose endpoint)
  - UI: modal fields, testStash handler

Functional tests against a real Stash require live integration
testing — they're documented as comments where applicable.
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
_STASH_DEEP_PY = _REPO_ROOT / "bulk_downloader" / "stash_deep.py"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_stash_deep_module_importable():
    import bulk_downloader.stash_deep as sd  # noqa: F401


def test_stash_client_construction_handles_empty_url():
    """An empty stash_url is the common case — most sites don't use
    Stash. The client must initialize without errors and report
    configured=False so the runner can short-circuit."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="", api_key="")
    assert c.configured is False


def test_stash_client_appends_graphql_path():
    """If user enters the base URL without /graphql, the client adds
    it. Both forms must work."""
    from bulk_downloader.stash_deep import StashClient
    c1 = StashClient(base_url="http://stash:9999", api_key="")
    assert c1._graphql_url == "http://stash:9999/graphql"
    # Already has /graphql — don't double-up
    c2 = StashClient(base_url="http://stash:9999/graphql", api_key="")
    assert c2._graphql_url == "http://stash:9999/graphql"
    # Trailing slash trimmed
    c3 = StashClient(base_url="http://stash:9999/", api_key="")
    assert c3._graphql_url == "http://stash:9999/graphql"


def test_deep_enabled_default_false():
    """Sites without explicit opt-in must NOT do Stash queries on
    every URL — would hammer Stash with unwanted requests."""
    from bulk_downloader.stash_deep import deep_enabled
    assert deep_enabled({}) is False
    assert deep_enabled({"stash_url": "http://x"}) is False, (
        "Just having stash_url shouldn't auto-enable deep features; "
        "the user has to explicitly opt in"
    )
    assert deep_enabled({"stash_deep_enabled": True}) is False, (
        "Deep features require BOTH the flag AND a configured URL"
    )
    assert deep_enabled({
        "stash_url": "http://x",
        "stash_deep_enabled": True,
    }) is True


def test_build_open_in_stash_url():
    """Deep-link URL generation for the queue context menu."""
    from bulk_downloader.stash_deep import build_open_in_stash_url
    assert build_open_in_stash_url("http://stash:9999", "42") == \
           "http://stash:9999/scenes/42"
    # /graphql in the URL is stripped (user might have pasted the
    # GraphQL endpoint in stash_url field)
    assert build_open_in_stash_url("http://stash:9999/graphql", "42") == \
           "http://stash:9999/scenes/42"
    # Empty inputs return empty (no broken links)
    assert build_open_in_stash_url("", "42") == ""
    assert build_open_in_stash_url("http://stash:9999", "") == ""


def test_summarize_scrape_format():
    """Render scrape results into a UI-friendly one-liner."""
    from bulk_downloader.stash_deep import summarize_scrape
    # Full result
    s = summarize_scrape({
        "title": "Test Scene",
        "studio": {"name": "TestStudio"},
        "performers": [{"name": "Alice"}, {"name": "Bob"}],
    })
    assert "Test Scene" in s
    assert "TestStudio" in s
    assert "Alice" in s
    assert "Bob" in s
    # Partial results don't crash
    assert isinstance(summarize_scrape({"title": "Only"}), str)
    assert isinstance(summarize_scrape({}), str)
    assert summarize_scrape(None) == ""
    # Performer truncation at 3
    big = summarize_scrape({
        "performers": [{"name": f"P{i}"} for i in range(10)]
    })
    assert "+more" in big


def test_stash_error_carries_kind():
    """The runner's retry-vs-fallback logic branches on .kind, so the
    exception class must carry it."""
    from bulk_downloader.stash_deep import StashError
    e = StashError("auth", "test message", {"hint": "fix it"})
    assert e.kind == "auth"
    assert e.message == "test message"
    assert e.detail == {"hint": "fix it"}


def test_stash_diagnose_handles_unreachable():
    """An unreachable Stash must return structured error, not raise."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="http://127.0.0.1:1", api_key="")
    result = c.diagnose()
    assert "ok" in result
    assert result["ok"] is False
    assert result["error"]


def test_stash_diagnose_handles_no_config():
    """A blank stash_url must produce a clean 'not configured' result,
    not a crash."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="", api_key="")
    result = c.diagnose()
    assert result["ok"] is False
    assert "not configured" in (result["error"] or "").lower()


def test_find_scene_by_url_returns_none_on_failure():
    """All read methods must return None on failure (any kind of
    failure), never raise. The runner's hot path can't afford to
    handle exceptions on every URL."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="http://127.0.0.1:1", api_key="")
    # Unreachable — must return None
    assert c.find_scene_by_url("https://example.com/v") is None
    assert c.find_scene_by_path("/some/path.mp4") is None
    assert c.scrape_url("https://example.com/v") is None
    assert c.find_studio_by_name("X") is None


def test_find_or_create_tags_handles_garbage_input():
    """Tag-resolution must tolerate non-string entries (a stale
    config could have None entries). Skip silently rather than crash."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="http://127.0.0.1:1", api_key="")
    # Unreachable + garbage input — should return [] without raising
    result = c.find_or_create_tags(["valid", None, "", 42, "another"])
    assert isinstance(result, list)
    # All entries fail to resolve (unreachable), but no crash


def test_update_scene_skips_call_with_only_id():
    """If the caller passes only scene_id and no fields to update,
    don't waste a network round-trip — return None immediately."""
    from bulk_downloader.stash_deep import StashClient
    c = StashClient(base_url="http://127.0.0.1:1", api_key="")
    # All None — should NOT raise (would be unreachable if it tried)
    result = c.update_scene("scene-42")
    assert result is None


# ── DEFAULTS / CFG_FIELDS ────────────────────────────────────────────

def test_stash_deep_defaults_present():
    """Deep features must default to OFF so existing sites with
    just the v3.20 basic Stash integration are unaffected."""
    src = _APP_SRC()
    assert '"stash_deep_enabled": False' in src
    assert '"stash_dedup_check": False' in src
    assert '"stash_scrape_preview": False' in src
    assert '"stash_studio_id": ""' in src
    assert '"stash_auto_studio": False' in src
    assert '"stash_tags": ""' in src


def test_stash_deep_fields_in_cfg_fields():
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
    for fld in ("stash_deep_enabled", "stash_dedup_check",
                 "stash_scrape_preview", "stash_studio_id",
                 "stash_auto_studio", "stash_tags"):
        assert f'"{fld}"' in fields_block, f"{fld} missing from CFG_FIELDS"


# ── Runner integration ───────────────────────────────────────────────

def test_runner_has_stash_methods():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    for name in ("_get_stash_client", "_stash_dedup_check",
                  "_stash_scrape_preview", "_stash_enrich_after_scan"):
        assert f"def {name}" in src, f"SiteRunner.{name} missing"


def test_dedup_check_runs_before_backend_chain():
    """The whole point of dedup is to skip the download. It must run
    BEFORE the JD/qB/teach branch chain selects a backend."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    proc_pos = src.find("def _process_one(")
    teach_pos = src.find("Phase 9.3: pick context strategy")
    assert proc_pos > 0 and teach_pos > 0
    body = src[proc_pos:teach_pos]
    dedup_pos = body.find("_stash_dedup_check")
    qb_branch = body.find("is_qb_backend")
    jd_branch = body.find("is_jd_backend")
    assert dedup_pos > 0
    # Dedup must come before all backend branches
    assert dedup_pos < qb_branch, (
        "Stash dedup must run before qB branch — otherwise we waste "
        "a HEAD probe / submit on a URL we already have"
    )
    assert dedup_pos < jd_branch, (
        "Stash dedup must run before JD branch"
    )


def test_dedup_check_fails_open():
    """When Stash is unreachable, the dedup check must NOT block the
    download. Better to redownload than to leave URLs stuck."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    method_pos = src.find("def _stash_dedup_check")
    assert method_pos > 0
    method_body = src[method_pos:method_pos + 3000]
    # Pattern: catch exception, log, return False (fail open)
    assert "Fail open" in method_body or "fail open" in method_body.lower()
    # Verify the actual control flow: on exception, return False
    assert "return False" in method_body


def test_enrichment_fires_after_completed():
    """The enrichment trigger must run from the completed-hook
    callsite so it gets a real file path."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Find the fire_event("completed", ...) call and verify the
    # enrichment spawn is near it
    fe_pos = src.find('fire_event("completed"')
    assert fe_pos > 0
    nearby = src[fe_pos:fe_pos + 1500]
    assert "_stash_enrich_after_scan" in nearby, (
        "Enrichment must trigger from the completed event so the "
        "Stash scan has time to start before we try to find the scene"
    )
    # And it must spawn in a thread (don't block worker)
    assert "Thread(" in nearby


def test_dedup_check_short_circuits_on_no_config():
    """Sites without stash_url must not even attempt the import +
    config check chain. Verify the fast path."""
    from bulk_downloader import stash_deep
    # An empty config has deep_enabled=False
    assert not stash_deep.deep_enabled({})
    # A config with just the URL still has deep_enabled=False
    assert not stash_deep.deep_enabled({"stash_url": "http://x"})


# ── App endpoints ────────────────────────────────────────────────────

def test_stash_diagnose_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/stash/diagnose' in src
    assert "def api_stash_diagnose" in src


def test_stash_preview_endpoint_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/stash/preview_url' in src
    assert "def api_stash_preview_url" in src


# ── UI ────────────────────────────────────────────────────────────────





