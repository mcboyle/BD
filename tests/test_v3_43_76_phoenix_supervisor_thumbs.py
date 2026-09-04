"""v3.43.76: tests for PhoenixAdult catalog + bandwidth supervisor +
thumbnail generation.

Three modules tested:
  1. phoenix_catalog.py     — embedded brand catalog + smart routing
  2. download_supervisor.py — token-bucket bandwidth throttling
  3. thumbnail_gen.py       — ffmpeg-based thumbnail generation

All modules fail-open; tests use no network.
"""
from __future__ import annotations

import os
import inspect
import tempfile
# [SAST 3:13pm 13 may] removed unused: import threading
import time
from unittest.mock import patch  # [SAST 3:13pm 13 may] dropped: MagicMock

# [SAST 3:13pm 13 may] removed unused: import pytest


# ─── Phoenix catalog ──────────────────────────────────────────────


class TestPhoenixLookup:
    def test_exact_host_match(self):
        from bulk_downloader import phoenix_catalog as pc
        m = pc.lookup_url("https://www.brazzers.com/scene/12345")
        assert m is not None
        assert m.name == "Brazzers"
        assert m.network == "aylo"
        assert m.confidence >= 1.0  # exact host + scene hint = 1.0+

    def test_subdomain_match(self):
        """A subdomain on a catalog brand should still match."""
        from bulk_downloader import phoenix_catalog as pc
        m = pc.lookup_url("https://cdn.deeper.com/videos/abc")
        assert m is not None
        assert m.name == "Deeper"

    def test_unknown_url_returns_none(self):
        from bulk_downloader import phoenix_catalog as pc
        assert pc.lookup_url("https://totally-random-domain.example/path") is None

    def test_empty_url_returns_none(self):
        from bulk_downloader import phoenix_catalog as pc
        assert pc.lookup_url("") is None
        assert pc.lookup_url(None) is None  # type: ignore[arg-type]

    def test_scene_hint_boosts_confidence(self):
        from bulk_downloader import phoenix_catalog as pc
        # Brazzers has /scene/ as a hint
        with_hint = pc.lookup_url("https://brazzers.com/scene/foo")
        without_hint = pc.lookup_url("https://brazzers.com/contact")
        assert with_hint is not None
        assert without_hint is not None
        assert with_hint.confidence >= without_hint.confidence

    def test_normalized_host(self):
        from bulk_downloader import phoenix_catalog as pc
        # WWW prefix should be stripped
        m1 = pc.lookup_url("https://www.brazzers.com/scene/123")
        m2 = pc.lookup_url("https://brazzers.com/scene/123")
        assert m1 is not None and m2 is not None
        assert m1.name == m2.name


class TestPhoenixSuggest:
    def test_route_to_existing(self):
        from bulk_downloader import phoenix_catalog as pc
        existing = {"my_brazzers": {"name": "Brazzers"}}
        s = pc.suggest_site_for_url("https://brazzers.com/scene/x", existing)
        assert s is not None
        assert s.action == "route_to"
        assert s.target_site_id == "my_brazzers"

    def test_create_new_when_no_match(self):
        from bulk_downloader import phoenix_catalog as pc
        existing = {"other": {"name": "Some Other Site"}}
        s = pc.suggest_site_for_url("https://tushy.com/videos/x", existing)
        assert s is not None
        assert s.action == "create_new"
        assert s.suggested_site_name == "Tushy"

    def test_unknown_url_returns_none(self):
        from bulk_downloader import phoenix_catalog as pc
        assert pc.suggest_site_for_url("https://unknown.example", {}) is None

    def test_name_normalization_for_matching(self):
        """Site names with spaces/punctuation should match canonical
        names case-insensitively."""
        from bulk_downloader import phoenix_catalog as pc
        existing = {"site1": {"name": "B R A Z Z E R S"}}
        s = pc.suggest_site_for_url("https://brazzers.com/scene/x", existing)
        # "BRAZZERS" with spaces vs "Brazzers" — both normalize to
        # "brazzers" so should match
        assert s is not None
        assert s.action == "route_to"


class TestPhoenixListings:
    def test_list_networks(self):
        from bulk_downloader import phoenix_catalog as pc
        networks = pc.list_networks()
        assert len(networks) > 0
        # Each entry has required fields
        for n in networks:
            assert "id" in n
            assert "display_name" in n
            assert "entry_count" in n

    def test_list_brands_in_network(self):
        from bulk_downloader import phoenix_catalog as pc
        brands = pc.list_brands_in_network("aylo")
        assert len(brands) > 0
        # All entries are aylo
        assert all("brand_id" in b for b in brands)

    def test_list_brands_unknown_network(self):
        from bulk_downloader import phoenix_catalog as pc
        assert pc.list_brands_in_network("nonexistent_network") == []
        assert pc.list_brands_in_network("") == []

    def test_stats_shape(self):
        from bulk_downloader import phoenix_catalog as pc
        s = pc.catalog_stats()
        for k in ("total_brands", "embedded_brands",
                  "user_extension_brands", "total_networks"):
            assert k in s
        assert s["embedded_brands"] > 50    # we ship ~100 entries
        assert s["total_networks"] >= 14


class TestPhoenixExtensionMechanism:
    def setup_method(self):
        from bulk_downloader import phoenix_catalog as pc
        # Force reload before each test
        pc.reload_extra()

    def teardown_method(self):
        from bulk_downloader import phoenix_catalog as pc
        # Clean up any extra file we created
        if os.path.exists(pc._EXTRA_CATALOG_FILENAME):
            os.unlink(pc._EXTRA_CATALOG_FILENAME)
        pc.reload_extra()

    def test_no_extension_file_returns_empty(self):
        from bulk_downloader import phoenix_catalog as pc
        assert pc._load_extra() == {}

    def test_bad_json_returns_empty(self):
        from bulk_downloader import phoenix_catalog as pc
        with open(pc._EXTRA_CATALOG_FILENAME, "w", encoding="utf-8") as f:
            f.write("not valid json {")
        assert pc._load_extra() == {}


# ─── Download supervisor ──────────────────────────────────────────


class TestSupervisorDisabledFastPath:
    def setup_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def test_disabled_acquire_returns_zero(self):
        from bulk_downloader import download_supervisor as ds
        # Default state: disabled
        waited = ds.acquire("site1", 1_000_000)
        assert waited == 0.0

    def test_is_enabled_false_by_default(self):
        from bulk_downloader import download_supervisor as ds
        assert ds.is_enabled() is False


class TestTokenBucketRate:
    def setup_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def test_unlimited_bucket_no_wait(self):
        from bulk_downloader.download_supervisor import TokenBucket
        b = TokenBucket("test", 0)   # unlimited
        assert b.acquire(100_000_000) == 0.0

    def test_limited_bucket_waits_when_drained(self):
        from bulk_downloader.download_supervisor import TokenBucket
        b = TokenBucket("test", 1_000_000)  # 1 MB/s
        b.acquire(1_000_000)  # drain
        t0 = time.monotonic()
        b.acquire(500_000)    # need 0.5s of refill
        elapsed = time.monotonic() - t0
        # Allow generous bounds for CI timing variance
        assert 0.3 < elapsed < 0.8

    def test_set_rate_unlimited_to_limited_fills_bucket(self):
        from bulk_downloader.download_supervisor import TokenBucket
        b = TokenBucket("test", 0)  # start unlimited
        b.set_rate(1_000_000)
        # Should now have a full bucket of capacity (1 MB)
        # Acquire 100KB should be instant
        t0 = time.monotonic()
        b.acquire(100_000)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05

    def test_set_rate_decrease_caps_tokens(self):
        from bulk_downloader.download_supervisor import TokenBucket
        b = TokenBucket("test", 10_000_000)  # 10 MB/s
        # Bucket starts at 10MB capacity, full
        b.set_rate(1_000_000)  # 1 MB/s
        # Tokens should be capped at new 1 MB capacity
        stats = b.stats()
        assert stats["tokens_available"] <= 1_000_000

    def test_stats_tracks_bytes_passed(self):
        from bulk_downloader.download_supervisor import TokenBucket
        b = TokenBucket("test", 10_000_000)
        b.acquire(50_000)
        b.acquire(75_000)
        assert b.stats()["bytes_passed"] == 125_000
        assert b.stats()["acquires"] == 2


class TestSupervisorConfigure:
    def setup_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def teardown_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def test_enable_with_global_cap(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(enabled=True, global_bps=5_000_000)
        assert ds.is_enabled() is True
        stats = ds.stats()
        assert stats["global"]["rate_bps"] == 5_000_000

    def test_per_site_cap(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(
            enabled=True, global_bps=100_000_000,
            per_site_bps={"slow_site": 500_000},
        )
        # Drain slow_site bucket
        ds.acquire("slow_site", 500_000)
        t0 = time.monotonic()
        ds.acquire("slow_site", 250_000)
        elapsed = time.monotonic() - t0
        assert 0.3 < elapsed < 0.8

    def test_other_site_unconstrained(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(
            enabled=True, global_bps=100_000_000,
            per_site_bps={"slow_site": 500_000},
        )
        # Different site shouldn't wait
        t0 = time.monotonic()
        ds.acquire("fast_site", 1_000_000)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05

    def test_hot_reload(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(enabled=True, global_bps=1_000_000)
        ds.configure(enabled=True, global_bps=100_000_000)
        assert ds.stats()["global"]["rate_bps"] == 100_000_000


class TestSupervisorAcquireSafety:
    def setup_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def teardown_method(self):
        from bulk_downloader import download_supervisor as ds
        ds.reset()

    def test_empty_site_id_safe(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(enabled=True, global_bps=1_000_000)
        # Should fall through global bucket only, not crash. Fresh
        # bucket is full so the 100-byte acquire is essentially
        # instant (microsecond-scale; acquire() returns the elapsed
        # since-start, which includes a few microseconds of function-
        # call overhead even when no actual sleep happened).
        assert ds.acquire("", 100) < 0.01
        # None gets coerced to "" inside acquire
        assert ds.acquire(None, 100) < 0.01  # type: ignore[arg-type]

    def test_zero_bytes_no_op(self):
        from bulk_downloader import download_supervisor as ds
        ds.configure(enabled=True, global_bps=1)  # very tight
        assert ds.acquire("any", 0) == 0.0
        assert ds.acquire("any", -1) == 0.0


def _supervisor_configuring_classes():
    """Return every module class with a test that enables the supervisor."""
    def _configures(method):
        source = inspect.getsource(method)
        return "ds.configure(" in source and "enabled=True" in source

    found = []
    for candidate in globals().values():
        if not isinstance(candidate, type):
            continue
        methods = inspect.getmembers(candidate, inspect.isfunction)
        if any(_configures(method)
                   for name, method in methods if name.startswith("test_")):
            found.append(candidate)
    return found


def test_supervisor_acquire_safety_restores_module_state_after_each_test():
    from bulk_downloader import download_supervisor as ds

    configuring_classes = _supervisor_configuring_classes()
    assert len(configuring_classes) == 2, configuring_classes
    def _configures(method):
        source = inspect.getsource(method)
        return "ds.configure(" in source and "enabled=True" in source

    configuring_methods = [
        (cls, method)
        for cls in configuring_classes
        for name, method in inspect.getmembers(cls, inspect.isfunction)
        if name.startswith("test_")
        and _configures(method)
    ]
    assert len(configuring_methods) == 6, configuring_methods

    for cls, method in configuring_methods:
        case = cls()
        case.setup_method()
        try:
            method(case)
            before = ds.stats()
            assert ds.is_enabled() is True
            assert before["global"]["rate_bps"] != ds.UNLIMITED
        finally:
            teardown = getattr(case, "teardown_method", None)
            if callable(teardown):
                teardown()
        after = ds.stats()
        assert ds.is_enabled() is False
        assert after["global"]["rate_bps"] == ds.UNLIMITED
        assert after["per_site"] == {}


def test_row657_transform_control_imports_supervisor_without_judging_cleanup():
    from bulk_downloader import download_supervisor as ds

    assert callable(ds.reset)


# ─── Thumbnail generation ─────────────────────────────────────────


class TestThumbnailAvailability:
    def test_returns_booleans(self):
        from bulk_downloader import thumbnail_gen as tg
        assert isinstance(tg.is_ffmpeg_available(), bool)
        assert isinstance(tg.is_ffprobe_available(), bool)
        assert isinstance(tg.is_available(), bool)


class TestResolveOutputPath:
    def test_sidecar_default(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.resolve_output_path("/tmp/foo/bar.mp4")
        # resolve_output_path runs the input through os.path.abspath,
        # so the result is OS-native (C:\... on Windows). Compare on
        # the basename rather than a hardcoded "/"-path.
        assert os.path.basename(result) == "bar.jpg"
        assert result.endswith(os.path.join("foo", "bar.jpg"))

    def test_sidecar_explicit(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.resolve_output_path(
            "/tmp/foo/bar.mp4", mode="sidecar")
        assert os.path.basename(result) == "bar.jpg"
        assert result.endswith(os.path.join("foo", "bar.jpg"))

    def test_parallel_with_dl_dir(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.resolve_output_path(
            "/dl/Brazzers/scene.mp4",
            mode="parallel",
            download_dir="/dl",
        )
        assert result.endswith(
            os.path.join("thumbs", "Brazzers", "scene.jpg"))

    def test_parallel_no_dl_dir_falls_back_to_sidecar(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.resolve_output_path(
            "/dl/scene.mp4", mode="parallel", download_dir="")
        # Falls back to sidecar when dl_dir is empty
        assert os.path.basename(result) == "scene.jpg"
        assert result.endswith(os.path.join("dl", "scene.jpg"))

    def test_parallel_source_outside_dl_dir(self):
        from bulk_downloader import thumbnail_gen as tg
        # Source not under download_dir → fall back to sidecar
        result = tg.resolve_output_path(
            "/other/scene.mp4", mode="parallel",
            download_dir="/dl",
        )
        assert os.path.basename(result) == "scene.jpg"
        assert result.endswith(os.path.join("other", "scene.jpg"))

    def test_empty_source(self):
        from bulk_downloader import thumbnail_gen as tg
        assert tg.resolve_output_path("") == ""

    def test_custom_subdir(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.resolve_output_path(
            "/dl/Brazzers/scene.mp4",
            mode="parallel",
            download_dir="/dl",
            parallel_subdir="my_thumbs",
        )
        assert "my_thumbs" in result


class TestGenerateFailOpen:
    def test_empty_source(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.generate_thumbnail("")
        assert result.ok is False
        assert result.error == "empty_source"

    def test_missing_source(self):
        from bulk_downloader import thumbnail_gen as tg
        result = tg.generate_thumbnail("/missing/file.mp4")
        assert result.ok is False
        assert "not_found" in result.error or "missing" in result.error

    def test_ffmpeg_missing_returns_clean_error(self):
        from bulk_downloader import thumbnail_gen as tg
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"x" * 4096)
            f.flush()
            with patch.object(tg, "is_ffmpeg_available", return_value=False):
                result = tg.generate_thumbnail(f.name)
                assert result.ok is False
                assert "ffmpeg" in result.error.lower() or \
                       "ffprobe" in result.error.lower()


class TestThumbnailSkipExisting:
    def test_skip_existing_returns_ok_no_work(self):
        from bulk_downloader import thumbnail_gen as tg
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "video.mp4")
            with open(src, "wb") as f:
                f.write(b"x" * 4096)
            # Pre-create the "thumb" so generate sees it exists
            thumb = os.path.join(tmp, "video.jpg")
            with open(thumb, "wb") as f:
                f.write(b"fake thumb")
            result = tg.generate_thumbnail(
                src, skip_existing=True,
                output_path=thumb,
            )
            assert result.ok is True
            assert result.elapsed_s == 0.0   # no real work


class TestThumbnailWorker:
    def test_lifecycle(self):
        from bulk_downloader import thumbnail_gen as tg
        worker = tg.ThumbnailWorker()
        worker.start()
        assert worker.stats()["running"] is True
        worker.stop(timeout_s=2.0)
        # After stop, the thread should have exited
        time.sleep(0.2)
        assert worker._thread is None or not worker._thread.is_alive()

    def test_submit_empty_path_ignored(self):
        from bulk_downloader import thumbnail_gen as tg
        worker = tg.ThumbnailWorker()
        worker.submit("")  # should be a no-op
        assert worker.queue_depth() == 0

    def test_submit_processes_via_queue(self):
        from bulk_downloader import thumbnail_gen as tg
        worker = tg.ThumbnailWorker()
        worker.start()
        try:
            worker.submit("/missing/file.mp4")
            # Wait up to 3s for the worker to process
            for _ in range(30):
                if worker.stats()["failed"] >= 1:
                    break
                time.sleep(0.1)
            assert worker.stats()["failed"] >= 1
        finally:
            worker.stop(timeout_s=2.0)

    def test_stats_shape(self):
        from bulk_downloader import thumbnail_gen as tg
        worker = tg.ThumbnailWorker()
        s = worker.stats()
        for k in ("completed", "failed", "queue_depth",
                  "total_elapsed_s", "running", "last_error"):
            assert k in s


# ─── Flask routes ─────────────────────────────────────────────────


class TestFlaskRoutes:
    def _rules(self):
        from bulk_downloader.app import app
        return {r.rule for r in app.url_map.iter_rules()}

    def test_phoenix_lookup(self):
        assert "/api/phoenix/lookup" in self._rules()

    def test_phoenix_suggest(self):
        assert "/api/phoenix/suggest" in self._rules()

    def test_phoenix_networks(self):
        assert "/api/phoenix/networks" in self._rules()

    def test_supervisor_status(self):
        assert "/api/supervisor/status" in self._rules()

    def test_supervisor_configure(self):
        assert "/api/supervisor/configure" in self._rules()

    def test_thumbnails_status(self):
        assert "/api/thumbnails/status" in self._rules()

    def test_thumbnails_regenerate(self):
        assert "/api/thumbnails/regenerate" in self._rules()


# ─── Config fields ────────────────────────────────────────────────


class TestConfigFields:
    def test_phoenix_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        for k in ("use_phoenix_catalog", "use_smart_routing"):
            assert k in CFG_FIELDS
        assert DEFAULTS.get("use_phoenix_catalog") is True
        assert DEFAULTS.get("use_smart_routing") is True

    def test_supervisor_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        for k in ("use_download_supervisor", "supervisor_global_bps",
                  "supervisor_per_site_bps"):
            assert k in CFG_FIELDS
        assert DEFAULTS.get("use_download_supervisor") is False
        assert DEFAULTS.get("supervisor_global_bps") == 0

    def test_thumbnail_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        for k in ("use_thumbnails", "thumbnail_mode",
                  "thumbnail_dir_mode", "thumbnail_sheet_rows",
                  "thumbnail_sheet_cols"):
            assert k in CFG_FIELDS
        assert DEFAULTS.get("use_thumbnails") is False
        assert DEFAULTS.get("thumbnail_mode") == "single"
        assert DEFAULTS.get("thumbnail_dir_mode") == "sidecar"


# ─── Runner integration ───────────────────────────────────────────


class TestRunnerHooks:
    def test_phoenix_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_PHOENIX_AVAILABLE")

    def test_supervisor_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_SUPERVISOR_AVAILABLE")

    def test_thumb_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_THUMB_AVAILABLE")


# ─── multi_conn integration ───────────────────────────────────────


class TestMultiConnBytesCallback:
    """Verify the v3.43.76 bytes_callback param exists in
    multi_conn.download() signature so supervisor wiring works."""

    def test_download_signature(self):
        from bulk_downloader import multi_conn
        import inspect
        sig = inspect.signature(multi_conn.download)
        assert "bytes_callback" in sig.parameters


# ─── bdctl integration ───────────────────────────────────────────


class TestBdctl:
    def test_cmd_phoenix_lookup(self):
        import bdctl
        assert hasattr(bdctl, "cmd_phoenix_lookup")
        assert callable(bdctl.cmd_phoenix_lookup)

    def test_cmd_supervisor_status(self):
        import bdctl
        assert hasattr(bdctl, "cmd_supervisor_status")
        assert callable(bdctl.cmd_supervisor_status)

    def test_cmd_thumbnails_regenerate(self):
        import bdctl
        assert hasattr(bdctl, "cmd_thumbnails_regenerate")
        assert callable(bdctl.cmd_thumbnails_regenerate)
