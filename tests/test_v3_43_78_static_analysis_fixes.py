"""v3.43.78: regression tests for static-analysis findings.

  F1 — app_live_recorder.py uses sys.stderr.write, not print()
  F2 — runner.login_async pauses session keepers before do_login
  F3 — CHANGELOG.md no longer contains emoji in the v3.43.71 section
  F4 — confirmed-dead imports actually gone
  F5 — detect.res_label handles 1200p / 900p / 353p tiers correctly
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import ast
import os
import re


# Resolve paths relative to the repo root (where this file's parent
# parent lives). Run-tests.py launches from the repo root so cwd-
# relative paths work there, but pytest invocations from elsewhere
# would break — anchor explicitly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_REPO_ROOT, "bulk_downloader")

def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))

_CHANGELOG = os.path.join(_REPO_ROOT, "CHANGELOG.md")
# v3.47.0: pre-v3.46 release notes moved to CHANGELOG_archive.md.
# The F3 emoji rule applies to those releases, so the lookup here
# falls back to the archive when the current CHANGELOG no longer
# contains the historical section.
_CHANGELOG_ARCHIVE = os.path.join(_REPO_ROOT, "CHANGELOG_archive.md")


# ─── F1: print() in library code ──────────────────────────────────


class TestF1NoStrayPrintInLibrary:
    """Library-code rule: use sys.stderr.write, not print()."""

    def test_app_live_recorder_uses_stderr(self):
        """Specifically the one print() the static analysis caught."""
        with open(os.path.join(_PKG, "app_live_recorder.py"), encoding="utf-8") as f:
            src = f.read()
        # No top-level print() calls in library code
        # (regex: print( at start-of-line indented level)
        matches = re.findall(r"^\s*print\(", src, re.MULTILINE)
        assert matches == [], \
            f"found print() in app_live_recorder.py: {matches}"

    def test_no_print_in_any_library_module(self):
        """Sweep: every bulk_downloader/ module."""
        offenders = []
        for f in sorted(os.listdir(_PKG)):
            if not f.endswith(".py"):
                continue
            path = os.path.join(_PKG, f)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r"^\s*print\(", src, re.MULTILINE):
                # Get the line number for context
                lineno = src[: m.start()].count("\n") + 1
                offenders.append(f"{path}:{lineno}")
        assert offenders == [], (
            f"library-code print() forbidden (use sys.stderr.write); "
            f"offenders: {offenders}"
        )


# ─── F2: login_async pauses keepers ──────────────────────────────


class TestF2LoginAsyncPausesKeepers:
    """v3.43.52 collision rule: any sync_playwright spawn must
    call pause_site_keepers first. The worker-initiated
    login_async path was the gap the F2 finding caught."""

    def test_login_async_run_calls_pause(self):
        """Read runner.py source and verify the _run() body inside
        login_async() contains pause_site_keepers BEFORE do_login()."""
        src = _bd_runner_src()
        # Find login_async definition
        m = re.search(
            r"def login_async\(.*?\n(.*?)(?=\n    def |\Z)",
            src, re.DOTALL,
        )
        assert m is not None, "couldn't locate login_async"
        body = m.group(1)
        # _run nested function should appear
        run_match = re.search(
            r"def _run\(\):(.*?)(?=\n        def |\n    def |\Z)",
            body, re.DOTALL,
        )
        assert run_match is not None, "couldn't locate _run inside login_async"
        run_body = run_match.group(1)
        # Verify pause_site_keepers appears BEFORE do_login
        pause_pos = run_body.find("pause_site_keepers")
        login_pos = run_body.find("do_login(")
        assert pause_pos >= 0, \
            "pause_site_keepers missing from login_async._run()"
        assert login_pos >= 0, "do_login call missing"
        assert pause_pos < login_pos, (
            "pause_site_keepers must come BEFORE do_login in login_async._run() "
            f"(pause at {pause_pos}, do_login at {login_pos})"
        )


# ─── F3: emoji-free CHANGELOG (v3.43.71 onward) ───────────────────


class TestF3NoEmojiInRecentChangelog:
    """The rule says no emoji in CHANGELOG. The fix scoped to the
    releases that were shipped under the rule (v3.43.66+)."""

    def _read_section(self, version: str) -> str:
        # Check current CHANGELOG first, then the archive (which holds
        # historical pre-v3.46 release notes). The archive headings
        # historically omit the leading "v" (e.g. "## 3.43.71"), while
        # the active CHANGELOG uses "## v3.43.71" — so try both forms.
        # NOTE (v3.66.30 policy): CHANGELOG_archive.md is KB-only and is
        # NOT present in a release-zip extract. A test that needs the
        # archive must skip-not-fail when it's absent; see test below.
        for path in (_CHANGELOG, _CHANGELOG_ARCHIVE):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for ver_form in (f"v{version}", version):
                m = re.search(
                    rf"^## {re.escape(ver_form)}\b.*?(?=^## |\Z)",
                    text, re.MULTILINE | re.DOTALL,
                )
                if m:
                    return m.group(0)
        return ""

    def _emoji_count(self, s: str) -> int:
        return len(re.findall(
            r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", s
        ))

    def test_current_release_emoji_free(self):
        """The current release's CHANGELOG entry (the topmost `## v…`
        heading) must be emoji-free.

        Replaces the old v3.43.71-specific check: that section moved to
        the KB-only CHANGELOG_archive.md (v3.66.30 policy) and is absent
        from release-zip extracts, so it could only ever skip here. The
        no-emoji rule is now enforced against the entry that's actually
        shipping; the historical range stays covered by
        test_post_roadmap_releases_emoji_free (which includes v3.43.71).
        """
        with open(_CHANGELOG, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^## v.+?(?=^## |\Z)", text,
                      re.MULTILINE | re.DOTALL)
        assert m, "no release section found in CHANGELOG.md"
        assert self._emoji_count(m.group(0)) == 0, \
            "current release CHANGELOG entry contains emoji"

    def test_post_roadmap_releases_emoji_free(self):
        """v3.43.66 through v3.43.78 should all be clean."""
        for v in ("3.43.66", "3.43.67", "3.43.68", "3.43.69",
                  "3.43.70", "3.43.71", "3.43.72", "3.43.73",
                  "3.43.74", "3.43.75", "3.43.76", "3.43.77",
                  "3.43.78"):
            section = self._read_section(v)
            if not section:
                continue  # release may not be present (e.g. unused)
            count = self._emoji_count(section)
            assert count == 0, f"v{v} CHANGELOG has {count} emoji"


# ─── F4: dead imports gone ────────────────────────────────────────


class TestF4UnusedImportsRemoved:
    """The four confirmed-unused imports the static analysis identified."""

    def test_selftest_no_unused_os(self):
        with open(os.path.join(_PKG, "selftest.py"), encoding="utf-8") as f:
            src = f.read()
        # `import os` must not appear at module top (unless actually used).
        # Check: if 'import os' is present, 'os.' must also be present.
        if re.search(r"^import os\b", src, re.MULTILINE):
            assert "os." in src, "import os present but unused"

    def test_selftest_no_unused_any(self):
        with open(os.path.join(_PKG, "selftest.py"), encoding="utf-8") as f:
            src = f.read()
        # Same check for typing.Any
        if "from typing import Any" in src:
            # Verify it's actually used (in annotations, references, etc.)
            # Count occurrences excluding the import line itself
            count = src.count("Any") - 1
            assert count > 0, "typing.Any imported but unused"

    def test_runner_no_unused_parse_size_bytes(self):
        with open(os.path.join(_PKG, "runner.py"), encoding="utf-8") as f:
            src = f.read()
        # parse_size_bytes occurrences (excluding the import line)
        count = src.count("parse_size_bytes")
        if "parse_size_bytes" in src and count == 1:
            # Only the import line — that's the bug
            assert False, \
                "parse_size_bytes imported but never used in runner.py"

    def test_runner_no_unused_queue_delete_site(self):
        with open(os.path.join(_PKG, "runner.py"), encoding="utf-8") as f:
            src = f.read()
        count = src.count("queue_delete_site")
        if "queue_delete_site" in src and count == 1:
            assert False, \
                "queue_delete_site imported but never used"

    def test_app_no_unused_db_log(self):
        with open(os.path.join(_PKG, "app.py"), encoding="utf-8") as f:
            src = f.read()
        # db_log should be either fully removed, or actually used
        count = src.count("db_log")
        if count > 0:
            # Either 0 occurrences (removed) or 2+ (imported + used)
            assert count != 1, \
                "db_log imported but never used in app.py"


# ─── F5: resolution-label sync ────────────────────────────────────


class TestF5ResolutionLabelsCoverAllTiers:
    """detect.res_label must handle the same height tiers that
    heuristic_scoring.RESOLUTION_TIERS produces — specifically the
    non-power-of-two ones (1200p, 900p, 353p) the F5 finding
    caught."""

    def test_1200p_labeled_correctly(self):
        from bulk_downloader import detect
        # 1200p shouldn't fall through to "1080p" or "1440p"
        assert detect.res_label(1200) == "1200p"

    def test_1200p_not_misreported_as_1080(self):
        from bulk_downloader import detect
        # Specifically: 1200 must NOT label as 1080p (the bug)
        assert detect.res_label(1200) != "1080p"

    def test_900p_labeled_correctly(self):
        from bulk_downloader import detect
        assert detect.res_label(900) == "900p"

    def test_900p_not_misreported_as_720(self):
        from bulk_downloader import detect
        assert detect.res_label(900) != "720p"

    def test_353p_labeled_correctly(self):
        from bulk_downloader import detect
        # 353p is a preview-stream tier
        label = detect.res_label(353)
        assert "353" in label, f"353 should label as 353p-ish, got: {label}"

    def test_standard_tiers_still_work(self):
        """Regression: don't break the existing tiers."""
        from bulk_downloader import detect
        assert detect.res_label(4320) == "8K"
        assert detect.res_label(3160) == "6K"
        assert detect.res_label(2880) == "5K"
        assert detect.res_label(2160) == "4K"
        assert detect.res_label(1440) == "1440p"
        assert detect.res_label(1080) == "1080p"
        assert detect.res_label(720) == "720p"
        assert detect.res_label(540) == "540p"
        assert detect.res_label(480) == "480p"
        assert detect.res_label(360) == "360p"
        assert detect.res_label(240) == "240p"

    def test_above_tier_rounds_to_lower_label(self):
        """A score of 1280 (between 1200 and 1440) should label as
        1200p (the nearest tier at or below)."""
        from bulk_downloader import detect
        assert detect.res_label(1280) == "1200p"

    def test_above_top_tier_labels_as_8k(self):
        from bulk_downloader import detect
        assert detect.res_label(5000) == "8K"

    def test_zero_or_negative_labels_as_auto(self):
        from bulk_downloader import detect
        assert detect.res_label(0) == "auto"
        assert detect.res_label(-1) == "auto"

    def test_res_score_handles_explicit_1200p(self):
        """res_score reads explicit '1200p' from text via the digit-
        pattern path (separate from the _RES_LABEL_PATTERNS table).
        Verify the round-trip res_score→res_label gives 1200p."""
        from bulk_downloader import detect
        score = detect.res_score("video_1200p.mp4")
        assert score == 1200
        assert detect.res_label(score) == "1200p"

    def test_res_score_handles_explicit_900p(self):
        from bulk_downloader import detect
        # NB: avoid `_X` after the height — _ is a word char in
        # Python's default \w, so `\b` between `p` and `_` doesn't
        # match. v3.43.65 documented this gotcha; the URL-shape
        # patterns use lookbehind/lookahead instead. The regex here
        # uses \b, so use a `.` suffix.
        score = detect.res_score("clip-900p.mp4")
        assert score == 900
        assert detect.res_label(score) == "900p"


# ─── Sweep: every res_label boundary tier ────────────────────────


class TestF5AllTierBoundaries:
    """For every tier present in heuristic_scoring.RESOLUTION_TIERS,
    detect.res_label should produce a non-empty, non-'auto' label
    that matches the expected pattern."""

    def test_all_heights_in_heuristic_have_label_coverage(self):
        from bulk_downloader import detect
        # The heights that heuristic_scoring tags (from the F5 analysis)
        heights = [4320, 3160, 2880, 2160, 1440, 1200, 1080, 900,
                   720, 540, 480, 360, 353, 240]
        for h in heights:
            label = detect.res_label(h)
            assert label not in ("auto", ""), \
                f"height {h} produced no label: {label!r}"
            # The label must reference the height in a recognizable way
            # (allow class names like "8K" / "6K" / "4K" / "5K" for the top tiers)
            if h >= 2160:
                # Class label
                assert label in ("8K", "6K", "5K", "4K"), \
                    f"top-tier {h} got: {label!r}"
            else:
                # p-suffixed
                assert str(h) in label or label.endswith("p"), \
                    f"sub-2160 height {h} got: {label!r}"
