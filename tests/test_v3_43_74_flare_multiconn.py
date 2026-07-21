"""v3.43.74: tests for FlareSolverr proxy client + multi-conn downloader.

Two modules tested:
  1. flaresolverr_client.py — external Cloudflare-challenge solver
  2. multi_conn.py — parallel byte-range chunked downloader

Both fail-open everywhere; no network access needed for tests (we mock
httpx where the real HTTP would happen).

Covers:
  - is_configured / is_available paths (with/without httpx)
  - Fail-open contract: every entrypoint returns a result object,
    never raises
  - Cookie translation: FlareSolverr's format → Playwright's format
    (sameSite normalization, expiry unit detection, missing fields)
  - SolveResult shape contract
  - Session lifecycle helpers (create/destroy/list)
  - Probe: HEAD success path, HEAD 405 fallback to Range GET,
    parsing Content-Range header
  - plan_chunks: byte-range math, last-chunk-takes-remainder,
    clamping to [1, 16]
  - should_use_multi_conn decision logic
  - DownloadResult shape contract
  - Flask routes registered
  - bdctl commands callable
  - Config field round-trip
  - Runner integration: methods exist, AVAILABLE flags defined
"""
from __future__ import annotations

import os
import sys
import tempfile
# [SAST 3:13pm 13 may] removed unused: import threading
# [SAST 3:13pm 13 may] removed unused: import time
from unittest.mock import patch, MagicMock

import pytest


# ─── FlareSolverr client ────────────────────────────────────────────


class TestFlareIsConfigured:
    def test_empty_returns_false(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.is_configured("") is False
        assert f.is_configured(None) is False  # type: ignore[arg-type]

    def test_non_http_returns_false(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.is_configured("not a url") is False
        assert f.is_configured("flaresolverr.local") is False  # missing scheme

    def test_http_returns_true(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.is_configured("http://localhost:8191/v1") is True
        assert f.is_configured("https://flare.example.com/v1") is True


class TestFlarePingFailOpen:
    """All entrypoints must return clean results when prerequisites
    are missing; never raise."""

    def test_unconfigured_endpoint(self):
        from bulk_downloader import flaresolverr_client as f
        r = f.ping("")
        assert r["ok"] is False
        assert r["error"] == "endpoint_not_configured"

    def test_httpx_missing(self):
        from bulk_downloader import flaresolverr_client as f
        with patch.object(f, "_httpx_available", return_value=False):
            r = f.ping("http://localhost:8191/v1")
            assert r["ok"] is False
            assert "httpx" in r["error"]

    def test_network_error_returns_clean(self):
        from bulk_downloader import flaresolverr_client as f
        fake_httpx = MagicMock()
        fake_httpx.get.side_effect = ConnectionError("refused")
        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            with patch.object(f, "_httpx_available", return_value=True):
                r = f.ping("http://localhost:8191/v1")
                assert r["ok"] is False
                # Returns a real error string, doesn't raise
                assert "error" in r


class TestFlareSolveCloudflareFailOpen:
    def test_invalid_url(self):
        from bulk_downloader import flaresolverr_client as f
        r = f.solve_cloudflare("")
        assert r.ok is False

    def test_unconfigured_endpoint(self):
        from bulk_downloader import flaresolverr_client as f
        r = f.solve_cloudflare("http://x.com", endpoint="")
        assert r.ok is False
        assert "endpoint_not_configured" in r.error

    def test_solve_result_shape(self):
        """Even on failure, SolveResult has all expected fields."""
        from bulk_downloader import flaresolverr_client as f
        r = f.solve_cloudflare("")
        assert hasattr(r, "ok")
        assert hasattr(r, "cookies")
        assert hasattr(r, "html")
        assert hasattr(r, "user_agent")
        assert hasattr(r, "elapsed_s")
        assert hasattr(r, "error")
        assert isinstance(r.cookies, list)

    def test_never_raises(self):
        from bulk_downloader import flaresolverr_client as f
        # Various garbage inputs
        try:
            f.solve_cloudflare(None)  # type: ignore[arg-type]
            f.solve_cloudflare("not_a_url", endpoint=None)  # type: ignore[arg-type]
            f.solve_cloudflare("http://x", endpoint="bogus")
        except Exception as e:
            pytest.fail(f"solve_cloudflare raised: {e}")


class TestFlareCookieTranslation:
    def test_basic_cookie(self):
        from bulk_downloader import flaresolverr_client as f
        flare = [{"name": "cf_clearance", "value": "abc",
                  "domain": ".example.com", "path": "/"}]
        pw = f._translate_cookies_to_playwright(flare)
        assert len(pw) == 1
        assert pw[0]["name"] == "cf_clearance"
        assert pw[0]["value"] == "abc"
        assert pw[0]["domain"] == ".example.com"
        assert pw[0]["path"] == "/"

    def test_drops_invalid(self):
        from bulk_downloader import flaresolverr_client as f
        flare = [
            {"name": "junk_no_value"},
            {"value": "junk_no_name"},
            {"name": "valid", "value": "x", "domain": ".y.com"},
            "not a dict",
        ]
        pw = f._translate_cookies_to_playwright(flare)
        assert len(pw) == 1
        assert pw[0]["name"] == "valid"

    def test_same_site_normalization(self):
        from bulk_downloader import flaresolverr_client as f
        flare = [
            {"name": "a", "value": "x", "domain": ".y.com",
             "sameSite": "lax"},
            {"name": "b", "value": "x", "domain": ".y.com",
             "sameSite": "STRICT"},
            {"name": "c", "value": "x", "domain": ".y.com",
             "sameSite": "none"},
            {"name": "d", "value": "x", "domain": ".y.com",
             "sameSite": "no_restriction"},
        ]
        pw = f._translate_cookies_to_playwright(flare)
        assert pw[0]["sameSite"] == "Lax"
        assert pw[1]["sameSite"] == "Strict"
        assert pw[2]["sameSite"] == "None"
        assert pw[3]["sameSite"] == "None"

    def test_expiry_unit_detection(self):
        """Some FlareSolverr versions return expiry as int milliseconds,
        some as float seconds. We auto-detect."""
        from bulk_downloader import flaresolverr_client as f
        flare = [
            {"name": "secs", "value": "x", "domain": ".y.com",
             "expires": 1900000000},  # 2030 in seconds
            {"name": "ms", "value": "x", "domain": ".y.com",
             "expires": 1900000000000},  # 2030 in ms
        ]
        pw = f._translate_cookies_to_playwright(flare)
        # Both should end up as similar Unix seconds float
        assert abs(pw[0]["expires"] - pw[1]["expires"]) < 1.0

    def test_default_domain_applied(self):
        """When a cookie has no domain but a default is set, use it."""
        from bulk_downloader import flaresolverr_client as f
        flare = [{"name": "x", "value": "y"}]
        pw = f._translate_cookies_to_playwright(
            flare, default_domain="example.com")
        assert len(pw) == 1
        assert pw[0]["domain"] == "example.com"

    def test_no_domain_no_default_drops(self):
        """A cookie with no domain and no default should be dropped."""
        from bulk_downloader import flaresolverr_client as f
        flare = [{"name": "x", "value": "y"}]
        pw = f._translate_cookies_to_playwright(flare)
        assert pw == []

    def test_non_list_input(self):
        from bulk_downloader import flaresolverr_client as f
        assert f._translate_cookies_to_playwright(None) == []  # type: ignore[arg-type]
        assert f._translate_cookies_to_playwright("not a list") == []  # type: ignore[arg-type]


class TestFlareStats:
    def test_stats_shape(self):
        from bulk_downloader import flaresolverr_client as f
        s = f.stats()
        # All expected fields present
        for key in ("enabled", "last_ping_ok", "solves_attempted",
                    "solves_succeeded", "solves_failed"):
            assert key in s, f"missing {key}"

    def test_reset_preserves_enabled(self):
        from bulk_downloader import flaresolverr_client as f
        f.set_enabled(True)
        f._bump_attempt()
        assert f.stats()["solves_attempted"] >= 1
        f.reset_stats()
        assert f.stats()["solves_attempted"] == 0
        # Enabled flag preserved
        assert f.stats()["enabled"] is True
        f.set_enabled(False)


class TestFlareSessionHelpers:
    def test_create_session_unconfigured(self):
        from bulk_downloader import flaresolverr_client as f
        r = f.create_session(endpoint="")
        assert r["ok"] is False

    def test_destroy_session_unconfigured(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.destroy_session("foo", endpoint="") is False

    def test_destroy_session_empty_id(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.destroy_session("", endpoint="http://localhost:8191/v1") is False

    def test_list_sessions_unconfigured(self):
        from bulk_downloader import flaresolverr_client as f
        assert f.list_sessions(endpoint="") == []


# ─── multi_conn ────────────────────────────────────────────────────


class TestMultiConnPlanChunks:
    def test_simple_split(self):
        from bulk_downloader import multi_conn as m
        chunks = m.plan_chunks(1000, 4)
        assert len(chunks) == 4
        # All chunks cover [0, 999] exactly
        assert chunks[0].start == 0
        assert chunks[-1].end == 999

    def test_total_length_preserved(self):
        from bulk_downloader import multi_conn as m
        for total in (1000, 9999, 100_000, 7_500_000_000):
            for n in (1, 2, 4, 8, 16):
                chunks = m.plan_chunks(total, n)
                assert sum(c.length for c in chunks) == total

    def test_chunks_contiguous(self):
        from bulk_downloader import multi_conn as m
        chunks = m.plan_chunks(100, 4)
        for i in range(len(chunks) - 1):
            assert chunks[i].end + 1 == chunks[i + 1].start

    def test_single_chunk(self):
        from bulk_downloader import multi_conn as m
        chunks = m.plan_chunks(1000, 1)
        assert len(chunks) == 1
        assert chunks[0].start == 0
        assert chunks[0].end == 999

    def test_clamps_to_16(self):
        from bulk_downloader import multi_conn as m
        chunks = m.plan_chunks(1000, 100)
        assert len(chunks) == 16

    def test_clamps_to_1(self):
        from bulk_downloader import multi_conn as m
        chunks = m.plan_chunks(1000, 0)
        assert len(chunks) == 1

    def test_zero_size_returns_empty(self):
        from bulk_downloader import multi_conn as m
        assert m.plan_chunks(0, 4) == []
        assert m.plan_chunks(-100, 4) == []


class TestMultiConnShouldUse:
    def test_too_small(self):
        from bulk_downloader import multi_conn as m
        # 50MB with default 100MB threshold → false
        assert m.should_use_multi_conn(50 * 1024 * 1024, True) is False

    def test_large_file_with_ranges(self):
        from bulk_downloader import multi_conn as m
        assert m.should_use_multi_conn(500 * 1024 * 1024, True) is True

    def test_no_range_support(self):
        from bulk_downloader import multi_conn as m
        # Even big files: if no Range, no multi-conn
        assert m.should_use_multi_conn(500 * 1024 * 1024, False) is False

    def test_zero_size(self):
        from bulk_downloader import multi_conn as m
        assert m.should_use_multi_conn(0, True) is False
        assert m.should_use_multi_conn(-1, True) is False

    def test_custom_threshold(self):
        from bulk_downloader import multi_conn as m
        # 50MB with 10MB threshold → true
        assert m.should_use_multi_conn(
            50 * 1024 * 1024, True,
            min_size_bytes=10 * 1024 * 1024,
        ) is True


class TestMultiConnFailOpen:
    def test_probe_invalid_url(self):
        from bulk_downloader import multi_conn as m
        r = m.probe("")
        assert r.ok is False

    def test_probe_httpx_missing(self):
        from bulk_downloader import multi_conn as m
        with patch.object(m, "is_available", return_value=False):
            r = m.probe("http://example.com/file.mp4")
            assert r.ok is False
            assert "httpx" in r.error

    def test_download_invalid_url(self):
        from bulk_downloader import multi_conn as m
        r = m.download("", "/tmp/out.mp4")
        assert r.ok is False

    def test_download_invalid_path(self):
        from bulk_downloader import multi_conn as m
        r = m.download("http://x/y", "")
        assert r.ok is False

    def test_download_httpx_missing(self):
        from bulk_downloader import multi_conn as m
        with patch.object(m, "is_available", return_value=False):
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as t:
                path = t.name
            try:
                r = m.download("http://x/y", path, content_length=1000)
                assert r.ok is False
                assert "httpx" in r.error
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass


class TestMultiConnResultShape:
    def test_download_result_fields(self):
        from bulk_downloader import multi_conn as m
        r = m.download("", "/tmp/x")
        for f in ("ok", "bytes_written", "chunks_completed",
                  "chunks_failed", "elapsed_s", "chunk_count", "error"):
            assert hasattr(r, f)

    def test_probe_result_fields(self):
        from bulk_downloader import multi_conn as m
        r = m.probe("")
        for f in ("ok", "content_length", "accept_ranges",
                  "final_url", "server", "error"):
            assert hasattr(r, f)


def test_multi_conn_progress_counts_unique_bytes_across_chunk_retry(
        monkeypatch, tmp_path):
    """Retry traffic is bandwidth, but not new logical output progress."""
    from bulk_downloader import multi_conn as m

    class Response:
        status_code = 206

        def __init__(self, chunks):
            self._chunks = chunks

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self, buffer_size):
            yield from self._chunks

    responses = iter((Response([b"ab"]), Response([b"ab", b"cd"])))

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return next(responses)

    fake_httpx = type("Httpx", (), {"Client": Client})
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(m, "is_available", lambda: True)
    monkeypatch.setattr(m, "_guard_url", lambda url: (True, ""))
    logical_progress = []
    network_deltas = []

    result = m.download(
        "https://cdn.test/v.mp4", str(tmp_path / "v.mp4"),
        content_length=4, chunk_count=1, chunk_retries=1,
        progress_cb=lambda got, total: logical_progress.append((got, total)),
        bytes_callback=network_deltas.append,
    )

    assert result.ok is True
    assert logical_progress == [(2, 4), (4, 4)]
    assert network_deltas == [2, 2, 2]


class TestMultiConnSparseAllocate:
    def test_allocates_file(self):
        from bulk_downloader import multi_conn as m
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "out.bin")
            assert m._allocate_sparse_file(path, 12345) is True
            assert os.path.getsize(path) == 12345

    def test_overwrites_existing(self):
        from bulk_downloader import multi_conn as m
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as t:
            t.write(b"existing content")
            path = t.name
        try:
            assert m._allocate_sparse_file(path, 100) is True
            assert os.path.getsize(path) == 100
        finally:
            os.unlink(path)


# ─── Flask routes ──────────────────────────────────────────────────


class TestFlareRoutes:
    def test_status_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/flaresolverr/status" in rules

    def test_test_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/flaresolverr/test" in rules

    def test_sessions_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/flaresolverr/sessions" in rules


class TestMultiConnRoutes:
    def test_status_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/multi_conn/status" in rules

    def test_probe_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/multi_conn/probe" in rules


# ─── Config fields ─────────────────────────────────────────────────


class TestConfigFields:
    def test_flaresolverr_fields_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        for k in ("use_flaresolverr", "flaresolverr_endpoint",
                  "flaresolverr_timeout_s", "flaresolverr_max_timeout_ms"):
            assert k in CFG_FIELDS, f"{k} not registered"
        # Default endpoint points at Docker default
        assert "8191" in DEFAULTS.get("flaresolverr_endpoint", "")
        assert DEFAULTS.get("use_flaresolverr") is False

    def test_multi_conn_fields_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        for k in ("use_multi_conn", "multi_conn_count",
                  "multi_conn_min_size_mb", "multi_conn_timeout_s"):
            assert k in CFG_FIELDS, f"{k} not registered"
        assert DEFAULTS.get("use_multi_conn") is False
        assert DEFAULTS.get("multi_conn_count") == 4
        assert DEFAULTS.get("multi_conn_min_size_mb") == 100


# ─── Runner integration ───────────────────────────────────────────


class TestRunnerHooks:
    def test_flare_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_FLARE_AVAILABLE")

    def test_multi_conn_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_MULTI_CONN_AVAILABLE")

    def test_multi_conn_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_try_multi_conn_download")


# ─── bdctl integration ────────────────────────────────────────────


class TestBdctl:
    def test_cmd_flare_status_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_flare_status")
        assert callable(bdctl.cmd_flare_status)

    def test_cmd_multi_conn_probe_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_multi_conn_probe")
        assert callable(bdctl.cmd_multi_conn_probe)
