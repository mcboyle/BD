"""v3.43.72: tests for perceptual dedup module.

Covers (without requiring videohash or ffmpeg to be installed):
  1. Availability functions return sane booleans
  2. hamming_distance: identical, off-by-N, bad length, garbage
  3. _canon_hash_hex normalizes various input shapes
  4. HashRegistry CRUD: add, lookup, remove, known_paths
  5. HashRegistry find_duplicates honors threshold + exclude_path
  6. HashRegistry.stats counts correctly
  7. compute_hash on missing file returns fail-open result
  8. apply_policy: keep_all, keep_largest, keep_first
  9. scan_folder with mocked files
 10. Flask routes registered
 11. Config fields registered
 12. bdctl commands exist
"""
from __future__ import annotations

import os
import tempfile
import time
from unittest.mock import patch

import pytest

from bulk_downloader import dedup as d


# ─── Availability ──────────────────────────────────────────────────


class TestAvailability:
    def test_is_videohash_available_returns_bool(self):
        assert isinstance(d.is_videohash_available(), bool)

    def test_is_ffmpeg_available_returns_bool(self):
        assert isinstance(d.is_ffmpeg_available(), bool)

    def test_is_available_returns_bool(self):
        assert isinstance(d.is_available(), bool)


# ─── hamming_distance ─────────────────────────────────────────────


class TestHammingDistance:
    def test_identical(self):
        assert d.hamming_distance("abcd", "abcd") == 0

    def test_one_bit_diff(self):
        # 0001 vs 0000: 1 bit differs
        assert d.hamming_distance("0001", "0000") == 1

    def test_all_bits_diff(self):
        # ffff (16 bits all set) vs 0000 (none)
        assert d.hamming_distance("ffff", "0000") == 16

    def test_64bit_hashes(self):
        a = "0123456789abcdef"
        b = "0123456789abcdee"  # differs by one bit in last nybble
        assert d.hamming_distance(a, b) == 1

    def test_bad_length_returns_negative(self):
        assert d.hamming_distance("abc", "abcd") == -1

    def test_non_hex_returns_negative(self):
        assert d.hamming_distance("zzzz", "abcd") == -1

    def test_empty_returns_negative(self):
        assert d.hamming_distance("", "") == -1
        assert d.hamming_distance("abcd", "") == -1

    def test_non_string_returns_negative(self):
        assert d.hamming_distance(None, "abcd") == -1
        assert d.hamming_distance("abcd", None) == -1
        assert d.hamming_distance(1234, "abcd") == -1


# ─── _canon_hash_hex ──────────────────────────────────────────────


class TestCanonHashHex:
    def test_already_canon(self):
        assert d._canon_hash_hex("0123456789abcdef") == "0123456789abcdef"

    def test_uppercase_normalized(self):
        assert d._canon_hash_hex("0123456789ABCDEF") == "0123456789abcdef"

    def test_strips_0x_prefix(self):
        assert d._canon_hash_hex("0x0123456789abcdef") == "0123456789abcdef"

    def test_pads_short_to_16(self):
        assert d._canon_hash_hex("abc") == "0000000000000abc"

    def test_truncates_long_to_16(self):
        assert d._canon_hash_hex("a" * 20) == "a" * 16

    def test_strips_non_hex(self):
        # Quotes, spaces, etc. get filtered
        assert d._canon_hash_hex("'0123-4567-89ab-cdef'") == "0123456789abcdef"

    def test_none_returns_empty(self):
        assert d._canon_hash_hex(None) == ""

    def test_integer_input(self):
        # If videohash returns an int, str() handles it
        result = d._canon_hash_hex(0x0123456789abcdef)
        assert len(result) == 16


# ─── HashRegistry ─────────────────────────────────────────────────


# Note: we use setup_method/teardown_method instead of a pytest fixture
# because the in-repo run_tests.py doesn't support custom fixtures
# (only the built-in tmp_path / monkeypatch shims). Real pytest would
# accept a @pytest.fixture too; this pattern works in both.

class _RegistryTestBase:
    """Shared setup_method/teardown_method for tests that need a fresh
    HashRegistry in a temp directory."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp(prefix="dedup_test_")
        self.tmp_registry = d.HashRegistry(
            os.path.join(self._tmpdir, "test.db"))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestRegistryCrud(_RegistryTestBase):
    def test_add_then_lookup(self):
        r = d.HashResult(
            ok=True, path="/tmp/a.mp4",
            hash_hex="0123456789abcdef",
            duration_sec=120.0, file_size_bytes=1000,
            codec="h264",
        )
        assert self.tmp_registry.add(r, notes="test") is True
        row = self.tmp_registry.lookup("/tmp/a.mp4")
        assert row is not None
        assert row["hash_hex"] == "0123456789abcdef"
        assert row["duration_sec"] == 120.0
        assert row["notes"] == "test"

    def test_add_invalid_result_returns_false(self):
        bad = d.HashResult(ok=False, path="/tmp/a.mp4", error="something")
        assert self.tmp_registry.add(bad) is False

    def test_lookup_missing_returns_none(self):
        assert self.tmp_registry.lookup("/nope.mp4") is None

    def test_add_replaces_existing(self):
        r1 = d.HashResult(ok=True, path="/x.mp4",
                          hash_hex="aaaaaaaaaaaaaaaa",
                          duration_sec=10.0, file_size_bytes=100)
        r2 = d.HashResult(ok=True, path="/x.mp4",
                          hash_hex="bbbbbbbbbbbbbbbb",
                          duration_sec=20.0, file_size_bytes=200)
        self.tmp_registry.add(r1)
        self.tmp_registry.add(r2)
        row = self.tmp_registry.lookup("/x.mp4")
        assert row["hash_hex"] == "bbbbbbbbbbbbbbbb"

    def test_known_paths(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4", hash_hex="a" * 16))
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/b.mp4", hash_hex="b" * 16))
        paths = self.tmp_registry.known_paths()
        assert paths == {"/a.mp4", "/b.mp4"}

    def test_remove(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4", hash_hex="a" * 16))
        assert self.tmp_registry.remove("/a.mp4") is True
        assert self.tmp_registry.lookup("/a.mp4") is None


class TestFindDuplicates(_RegistryTestBase):
    def test_finds_within_threshold(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4",
            hash_hex="0123456789abcdef"))
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/b.mp4",
            hash_hex="0123456789abcdee"))  # 1 bit diff
        dups = self.tmp_registry.find_duplicates(
            "0123456789abcdef", distance=4, exclude_path="/a.mp4")
        assert len(dups) == 1
        assert dups[0]["path"] == "/b.mp4"
        assert dups[0]["distance"] == 1

    def test_excludes_self(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4", hash_hex="abc" * 5 + "d"))
        dups = self.tmp_registry.find_duplicates(
            "abc" * 5 + "d", distance=0, exclude_path="/a.mp4")
        assert dups == []

    def test_threshold_zero_only_exact(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4", hash_hex="0123456789abcdef"))
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/b.mp4", hash_hex="0123456789abcdee"))
        dups = self.tmp_registry.find_duplicates(
            "0123456789abcdef", distance=0)
        # Only one entry matches exactly (and self isn't excluded here
        # because exclude_path is empty)
        assert len(dups) == 1
        assert dups[0]["path"] == "/a.mp4"

    def test_invalid_hash_length_returns_empty(self):
        assert self.tmp_registry.find_duplicates("short") == []

    def test_sorted_by_distance_then_size(self):
        # Three entries within threshold; verify sort
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/exact.mp4",
            hash_hex="0123456789abcdef", file_size_bytes=1000))
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/close.mp4",
            hash_hex="0123456789abcdee", file_size_bytes=5000))
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/big_far.mp4",
            hash_hex="0123456789abcdcc", file_size_bytes=9999))  # 2 bits
        dups = self.tmp_registry.find_duplicates(
            "0123456789abcdef", distance=4)
        # Sorted: exact (0), close (1), big_far (2)
        assert dups[0]["path"] == "/exact.mp4"
        assert dups[1]["path"] == "/close.mp4"
        assert dups[2]["path"] == "/big_far.mp4"


class TestRegistryStats(_RegistryTestBase):
    def test_empty(self):
        s = self.tmp_registry.stats()
        assert s["total"] == 0
        assert s["last_computed_at"] == 0.0

    def test_after_adds(self):
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/a.mp4", hash_hex="a" * 16))
        time.sleep(0.01)
        self.tmp_registry.add(d.HashResult(
            ok=True, path="/b.mp4", hash_hex="b" * 16))
        s = self.tmp_registry.stats()
        assert s["total"] == 2
        assert s["last_computed_at"] > 0


# ─── compute_hash ──────────────────────────────────────────────────


class TestComputeHash:
    def test_empty_path(self):
        r = d.compute_hash("")
        assert r.ok is False
        assert r.error == "empty_path"

    def test_missing_file(self):
        r = d.compute_hash("/totally/nonexistent/file.mp4")
        assert r.ok is False
        assert r.error == "file_not_found"

    def test_videohash_missing_returns_fail_open(self):
        """When videohash isn't installed, compute_hash returns
        ok=False with a clear error — never raises."""
        with patch.object(d, "is_videohash_available", return_value=False):
            with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
                f.write(b"\x00" * 4096)
                f.flush()
                r = d.compute_hash(f.name)
                assert r.ok is False
                assert r.error == "videohash_not_installed"

    def test_file_too_small(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            # Smaller than 1024 byte minimum
            f.write(b"\x00" * 100)
            f.flush()
            with patch.object(d, "is_videohash_available", return_value=True):
                with patch.object(d, "is_ffmpeg_available", return_value=True):
                    r = d.compute_hash(f.name)
                    assert r.ok is False
                    assert r.error == "file_too_small_for_hashing"


# ─── apply_policy ──────────────────────────────────────────────────


class TestApplyPolicy:
    def _group(self):
        return [
            {"path": "/a.mp4", "file_size_bytes": 1000, "computed_at": 1.0},
            {"path": "/b.mp4", "file_size_bytes": 2000, "computed_at": 2.0},
            {"path": "/c.mp4", "file_size_bytes":  500, "computed_at": 0.5},
        ]

    def test_keep_all(self):
        result = d.apply_policy(self._group(), "keep_all")
        assert len(result["keep"]) == 3
        assert result["remove_candidates"] == []

    def test_keep_largest(self):
        result = d.apply_policy(self._group(), "keep_largest")
        assert len(result["keep"]) == 1
        assert result["keep"][0]["path"] == "/b.mp4"
        assert len(result["remove_candidates"]) == 2

    def test_keep_first(self):
        """Earliest computed_at wins."""
        result = d.apply_policy(self._group(), "keep_first")
        assert result["keep"][0]["path"] == "/c.mp4"

    def test_single_file_no_op(self):
        single = [self._group()[0]]
        result = d.apply_policy(single, "keep_largest")
        assert len(result["keep"]) == 1
        assert result["remove_candidates"] == []

    def test_empty(self):
        result = d.apply_policy([], "keep_largest")
        assert result["keep"] == []
        assert result["remove_candidates"] == []

    def test_unknown_policy_falls_back_safe(self):
        """Unknown policy defaults to keep_all (safe)."""
        result = d.apply_policy(self._group(), "bogus_policy")
        assert len(result["keep"]) == 3
        assert result["remove_candidates"] == []


# ─── scan_folder ───────────────────────────────────────────────────


class TestScanFolder(_RegistryTestBase):
    def test_missing_dir(self):
        summary = self.tmp_registry.scan_folder("/totally/nonexistent")
        assert summary.get("error") == "not_a_directory"

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = self.tmp_registry.scan_folder(tmp)
            assert summary["scanned"] == 0
            assert summary["hashed"] == 0

    def test_skips_non_video_files(self):
        """Files with non-video extensions should not be scanned."""
        with tempfile.TemporaryDirectory() as tmp:
            for fn in ("a.txt", "b.jpg", "c.zip"):
                with open(os.path.join(tmp, fn), "wb") as f:
                    f.write(b"x" * 2048)
            summary = self.tmp_registry.scan_folder(tmp)
            assert summary["scanned"] == 0  # none matched

    def test_skip_known_files(self):
        """Files already in registry should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.mp4")
            with open(path, "wb") as f:
                f.write(b"x" * 4096)
            # Pre-register so it's "known"
            self.tmp_registry.add(d.HashResult(
                ok=True, path=path, hash_hex="a" * 16))
            summary = self.tmp_registry.scan_folder(tmp)
            assert summary["scanned"] == 1
            assert summary["skipped"] == 1
            assert summary["hashed"] == 0

    def test_progress_callback_invoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                with open(os.path.join(tmp, f"v{i}.mp4"), "wb") as f:
                    f.write(b"x" * 4096)
            calls = []
            def _cb(done, total, path):
                calls.append((done, total))
            self.tmp_registry.scan_folder(tmp, progress_cb=_cb)
            assert len(calls) == 3

    def test_cancel_check_aborts_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                with open(os.path.join(tmp, f"v{i}.mp4"), "wb") as f:
                    f.write(b"x" * 4096)
            cancelled = [False]
            def _cancel():
                if not cancelled[0]:
                    cancelled[0] = True
                    return False
                return True  # cancel on second check
            summary = self.tmp_registry.scan_folder(tmp, cancel_check=_cancel)
            assert summary["cancelled"] is True
            assert summary["scanned"] < 5


# ─── Module singleton ──────────────────────────────────────────────


class TestDefaultRegistry:
    def test_singleton(self):
        # First call sets the path; second returns same instance
        r1 = d.get_default_registry("test_singleton.db")
        r2 = d.get_default_registry()
        assert r1 is r2


# ─── Flask routes ──────────────────────────────────────────────────


class TestFlaskRoutes:
    def test_status_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/dedup/status" in rules

    def test_scan_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/dedup/scan" in rules

    def test_find_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/dedup/find" in rules

    def test_groups_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/dedup/groups" in rules

    def test_remove_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/dedup/remove" in rules


# ─── Config registration ──────────────────────────────────────────


class TestConfigFields:
    def test_dedup_hash_on_done_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "dedup_hash_on_done" in CFG_FIELDS
        assert DEFAULTS.get("dedup_hash_on_done") is True

    def test_dedup_distance_default(self):
        from bulk_downloader.app_kernel import DEFAULTS
        assert DEFAULTS.get("dedup_distance") == 4

    def test_dedup_db_path_default(self):
        from bulk_downloader.app_kernel import DEFAULTS
        assert DEFAULTS.get("dedup_db_path") == "video_hashes.db"

    def test_dedup_policy_default(self):
        from bulk_downloader.app_kernel import DEFAULTS
        assert DEFAULTS.get("dedup_policy") == "keep_all"


# ─── Runner integration ───────────────────────────────────────────


class TestRunnerHooks:
    def test_dedup_hash_worker_method_exists(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_dedup_hash_worker")

    def test_dedup_imports_in_runner(self):
        """The runner should soft-import the dedup module."""
        from bulk_downloader import runner
        # Either available or not, but the flag must be defined
        assert hasattr(runner, "_DEDUP_AVAILABLE")


# ─── bdctl integration ────────────────────────────────────────────


class TestBdctl:
    def test_cmd_dedup_status_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_dedup_status")

    def test_cmd_dedup_scan_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_dedup_scan")

    def test_cmd_dedup_find_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_dedup_find")
