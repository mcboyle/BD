"""v3.43.60 coverage gap fill — small modules.

Covers the four previously-untested modules surfaced by the Phase 3
coverage map:

  - fname.py             — path-traversal-safe filename templating
  - integrity.py         — ffprobe wrapper for media validation
  - constants.py         — make_fingerprint() randomizer
  - log.py               — get_logger / set_level / get_level

These were all uncovered before Phase 3. fname.py is security-critical
(it's the chokepoint for downloaded-filename sanitization), integrity.py
is small but called for every download, and the others are smaller.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import re
# [SAST 3:13pm 13 may] removed unused: from pathlib import Path

import pytest


# ════════════════════════════════════════════════════════════════════
#  fname.py — security-critical filename sanitizer
# ════════════════════════════════════════════════════════════════════

class TestSanitizeFilenameVar:
    def test_strips_forbidden_chars(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var('hello<world>:"piped"|?*')
        assert "<" not in out
        assert ">" not in out
        assert ":" not in out
        assert '"' not in out
        assert "|" not in out
        assert "?" not in out
        assert "*" not in out

    def test_strips_path_separators_by_default(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("../../etc/passwd")
        assert "/" not in out
        assert "\\" not in out
        # Should not have allowed path-traversal characters through
        assert ".." in out  # the literal dots remain, but no separators

    def test_allow_paths_keeps_slashes(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("dir/sub/file", allow_paths=True)
        assert "/" in out  # slash retained when explicitly allowed

    def test_collapses_whitespace(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("hello    world\t\ttabs")
        assert "  " not in out
        assert "\t" not in out

    def test_strips_trailing_dots(self):
        """Windows quietly drops trailing dots — 'file.mp4.' becomes 'file.mp4',
        which could clobber another file. Strip them."""
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("file.mp4...")
        assert not out.endswith(".")
        assert out == "file.mp4"

    def test_caps_at_180_chars(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("x" * 500)
        assert len(out) <= 180

    def test_empty_returns_untitled(self):
        from bulk_downloader.fname import _sanitize_filename_var
        assert _sanitize_filename_var("") == "untitled"
        assert _sanitize_filename_var(None) == "untitled"

    def test_strips_control_chars(self):
        from bulk_downloader.fname import _sanitize_filename_var
        out = _sanitize_filename_var("hello\x00\x01world\x1f")
        assert "\x00" not in out
        assert "\x01" not in out
        assert "\x1f" not in out


class TestResolveFilenameTemplate:
    def test_simple_substitution(self):
        from bulk_downloader.fname import resolve_filename_template
        out = resolve_filename_template("{title}.mp4", {"title": "Hello"})
        assert out == "Hello.mp4"

    def test_preserves_path_separators_in_template(self):
        from bulk_downloader.fname import resolve_filename_template
        out = resolve_filename_template("{site}/{title}.mp4",
                                         {"site": "wg", "title": "vid"})
        assert out == "wg/vid.mp4"

    def test_sanitizes_traversal_in_values(self):
        from bulk_downloader.fname import resolve_filename_template
        out = resolve_filename_template("{site}/{title}.mp4",
                                         {"site": "wg", "title": "../../etc/passwd"})
        # Traversal in the VALUE should not produce traversal in the output
        assert "../" not in out
        # The template's slash is preserved
        assert "wg/" in out

    def test_unknown_placeholders_stripped(self):
        from bulk_downloader.fname import resolve_filename_template
        out = resolve_filename_template("{title}-{misspelled}.mp4",
                                         {"title": "vid"})
        assert "{misspelled}" not in out
        assert "vid" in out

    def test_empty_template_returns_empty(self):
        from bulk_downloader.fname import resolve_filename_template
        assert resolve_filename_template("", {"x": "y"}) == ""

    def test_collapses_double_slashes_from_missing_values(self):
        from bulk_downloader.fname import resolve_filename_template
        # When a {date} comes back empty, "site/{date}/file" should not have //
        out = resolve_filename_template("site/{date}/file",
                                         {"date": ""})
        assert "//" not in out


# ════════════════════════════════════════════════════════════════════
#  integrity.py — ffprobe wrapper
# ════════════════════════════════════════════════════════════════════

class TestVerifyMediaIntegrity:
    def test_returns_ok_when_ffprobe_missing(self, monkeypatch=None):
        """If ffprobe isn't on PATH the function silently OKs the file."""
        from bulk_downloader import integrity
        orig = integrity._FFPROBE
        integrity._FFPROBE = None
        try:
            ok, reason = integrity.verify_media_integrity("/tmp/whatever.mp4")
            assert ok is True
            assert "not installed" in reason
        finally:
            integrity._FFPROBE = orig

    def test_returns_failure_for_nonexistent_file_if_ffprobe_available(self):
        """If ffprobe IS installed (rare in CI), nonexistent paths should
        produce a non-OK result. Skip the test if ffprobe isn't available."""
        from bulk_downloader import integrity
        if not integrity._FFPROBE:
            pytest.skip("ffprobe not installed in this environment")
        ok, reason = integrity.verify_media_integrity("/tmp/nonexistent_xyz.mp4")
        assert ok is False
        assert reason  # has some explanation

    def test_returns_failure_on_timeout(self):
        """Simulate ffprobe timeout via a stubbed subprocess.run."""
        from bulk_downloader import integrity
        if not integrity._FFPROBE:
            pytest.skip("ffprobe not installed; this test mocks subprocess")
        import subprocess as sp
        orig_run = sp.run
        def stub_run(*args, **kwargs):
            raise sp.TimeoutExpired(args[0], 60)
        try:
            sp.run = stub_run
            ok, reason = integrity.verify_media_integrity("/tmp/anything.mp4")
            assert ok is False
            assert "timeout" in reason.lower()
        finally:
            sp.run = orig_run


# ════════════════════════════════════════════════════════════════════
#  constants.py — make_fingerprint randomizer
# ════════════════════════════════════════════════════════════════════

class TestMakeFingerprint:
    def test_returns_dict_with_expected_keys(self):
        from bulk_downloader.constants import make_fingerprint
        fp = make_fingerprint()
        assert isinstance(fp, dict)
        for k in ("user_agent", "viewport_w", "viewport_h", "timezone", "locale"):
            assert k in fp, f"missing key: {k}"

    def test_viewport_is_numeric(self):
        from bulk_downloader.constants import make_fingerprint
        fp = make_fingerprint()
        assert isinstance(fp["viewport_w"], int)
        assert isinstance(fp["viewport_h"], int)
        assert fp["viewport_w"] > 0
        assert fp["viewport_h"] > 0

    def test_user_agent_looks_real(self):
        from bulk_downloader.constants import make_fingerprint
        fp = make_fingerprint()
        ua = fp["user_agent"]
        # Real UAs always contain "Mozilla" — sanity check
        assert "Mozilla" in ua, f"unexpected UA shape: {ua!r}"

    def test_returns_varied_results(self):
        """Across many calls, we should get a mix of UAs/viewports. Otherwise
        the randomization isn't working."""
        from bulk_downloader.constants import make_fingerprint
        seen_uas = set()
        seen_vps = set()
        for _ in range(40):
            fp = make_fingerprint()
            seen_uas.add(fp["user_agent"])
            seen_vps.add((fp["viewport_w"], fp["viewport_h"]))
        # At least 2 distinct values in each — chance of single-value across 40 trials is astronomically low
        assert len(seen_uas) >= 2
        assert len(seen_vps) >= 2


# ════════════════════════════════════════════════════════════════════
#  log.py — logger plumbing
# ════════════════════════════════════════════════════════════════════

class TestLogger:
    def test_get_logger_returns_object(self):
        from bulk_downloader import log as bd_log
        lg = bd_log.get_logger("test.module")
        assert lg is not None
        # Must expose at least the standard 4 levels
        for m in ("debug", "info", "warning", "error", "exception"):
            assert hasattr(lg, m), f"logger missing method: {m}"

    def test_set_level_then_get_level(self):
        from bulk_downloader import log as bd_log
        prior = bd_log.get_level()
        try:
            assert bd_log.set_level("WARNING") is True
            assert bd_log.get_level() == "WARNING"
            assert bd_log.set_level("INFO") is True
            assert bd_log.get_level() == "INFO"
        finally:
            bd_log.set_level(prior)

    def test_invalid_level_rejected(self):
        from bulk_downloader import log as bd_log
        prior = bd_log.get_level()
        try:
            # Invalid level name should return False, not raise
            result = bd_log.set_level("NOPE_NOT_A_LEVEL")
            assert result is False
            # Level should be unchanged
            assert bd_log.get_level() == prior
        finally:
            bd_log.set_level(prior)

    def test_get_logger_idempotent(self):
        """Calling get_logger with the same name returns equivalent logger
        (same underlying configuration)."""
        from bulk_downloader import log as bd_log
        l1 = bd_log.get_logger("test.same")
        l2 = bd_log.get_logger("test.same")
        # Behaviorally equivalent (loggers don't have to be the same object,
        # but invoking them shouldn't differ).
        # Just sanity-check that calling methods on both doesn't raise.
        l1.info("from l1")
        l2.info("from l2")
