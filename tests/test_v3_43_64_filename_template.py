"""v3.43.64: tests for filename-template variable extensions.

Covers:
  1. KNOWN_VARIABLES catalog is well-formed
  2. available_variable_names() returns all entries
  3. format_duration_for_filename handles edge cases
  4. New variables ({performer}, {studio}, {year}, etc.) resolve
     correctly when populated
  5. Empty placeholder values get their decorative brackets stripped
  6. Existing fname tests still pass (covered by test_v3_43_60_coverage_gaps)
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader.fname import (
    KNOWN_VARIABLES,
    available_variable_names,
    format_duration_for_filename,
    resolve_filename_template,
)


# ─── KNOWN_VARIABLES catalog ────────────────────────────────────────


class TestKnownVariables:
    def test_catalog_nonempty(self):
        assert len(KNOWN_VARIABLES) >= 15  # at least the v3.0 + v3.43.64 set

    def test_every_entry_is_a_quadruple(self):
        for entry in KNOWN_VARIABLES:
            assert isinstance(entry, tuple) and len(entry) == 4, \
                f"entry must be (name, desc, example, since): {entry!r}"
            name, desc, ex, since = entry
            assert isinstance(name, str) and name
            assert isinstance(desc, str) and desc
            assert isinstance(ex, str)  # examples may be empty
            assert isinstance(since, str) and since.startswith("v")

    def test_no_duplicate_names(self):
        names = [e[0] for e in KNOWN_VARIABLES]
        assert len(names) == len(set(names)), \
            f"duplicate names: {[n for n in names if names.count(n) > 1]}"

    def test_contains_v3_43_64_additions(self):
        """The headline new variables for this release must be listed."""
        names = available_variable_names()
        for required in ("performer", "studio", "year", "duration",
                         "quality", "extractor", "upload_date"):
            assert required in names, f"{required} missing from KNOWN_VARIABLES"

    def test_contains_legacy_variables(self):
        names = available_variable_names()
        for legacy in ("title", "site", "filename", "ext",
                       "date", "time", "resolution"):
            assert legacy in names, f"{legacy} missing — regression"

    def test_available_variable_names_matches_catalog(self):
        catalog = [e[0] for e in KNOWN_VARIABLES]
        assert available_variable_names() == catalog


# ─── format_duration_for_filename ───────────────────────────────────


class TestFormatDurationForFilename:
    def test_zero_returns_empty(self):
        assert format_duration_for_filename(0) == ""

    def test_negative_returns_empty(self):
        assert format_duration_for_filename(-5) == ""

    def test_none_returns_empty(self):
        assert format_duration_for_filename(None) == ""

    def test_nonnumeric_returns_empty(self):
        assert format_duration_for_filename("not a number") == ""

    def test_under_one_minute(self):
        assert format_duration_for_filename(45) == "00:00:45"

    def test_one_hour(self):
        assert format_duration_for_filename(3600) == "01:00:00"

    def test_complex_duration(self):
        assert format_duration_for_filename(3 * 3600 + 25 * 60 + 17) \
            == "03:25:17"

    def test_over_one_day(self):
        """Hours can exceed 24 — long streams should render naturally."""
        assert format_duration_for_filename(36 * 3600) == "36:00:00"

    def test_huge_duration_doesnt_overflow(self):
        # 200 hour stream
        assert format_duration_for_filename(200 * 3600) == "200:00:00"

    def test_float_input_truncates(self):
        assert format_duration_for_filename(45.7) == "00:00:45"


# ─── resolve_filename_template — new variables ──────────────────────


class TestNewVariableSubstitution:
    def test_performer_substitutes(self):
        out = resolve_filename_template(
            "{performer} - {title}{ext}",
            {"performer": "Jane Doe", "title": "Scene 1", "ext": ".mp4"})
        assert out == "Jane Doe - Scene 1.mp4"

    def test_studio_substitutes(self):
        out = resolve_filename_template(
            "{studio}/{title}{ext}",
            {"studio": "Brazzers", "title": "Scene 1", "ext": ".mp4"})
        assert out == "Brazzers/Scene 1.mp4"

    def test_year_substitutes(self):
        out = resolve_filename_template(
            "{title} ({year}){ext}",
            {"title": "Scene", "year": "2024", "ext": ".mp4"})
        assert out == "Scene (2024).mp4"

    def test_quality_substitutes(self):
        out = resolve_filename_template(
            "{title} [{quality}]{ext}",
            {"title": "Scene", "quality": "1080p", "ext": ".mp4"})
        assert out == "Scene [1080p].mp4"

    def test_duration_substitutes(self):
        out = resolve_filename_template(
            "{title} ({duration}){ext}",
            {"title": "Scene", "duration": "00:24:17", "ext": ".mp4"})
        # Colons are Windows-forbidden in filenames; _sanitize_filename_var
        # replaces them with underscores. So "00:24:17" → "00_24_17".
        assert out == "Scene (00_24_17).mp4"

    def test_extractor_substitutes(self):
        out = resolve_filename_template(
            "{title}.{extractor}{ext}",
            {"title": "Scene", "extractor": "phub", "ext": ".mp4"})
        assert out == "Scene.phub.mp4"

    def test_all_new_variables_in_one_template(self):
        ctx = {
            "performer": "Jane Doe",
            "studio": "Brazzers",
            "year": "2024",
            "duration": "00:24:17",
            "quality": "1080p",
            "extractor": "phub",
            "upload_date": "2024-08-14",
            "title": "Scene 1",
            "ext": ".mp4",
        }
        tpl = "{studio}/{performer}/{title} [{quality}] ({year}){ext}"
        out = resolve_filename_template(tpl, ctx)
        assert out == "Brazzers/Jane Doe/Scene 1 [1080p] (2024).mp4"


# ─── Empty-value bracket cleanup (new v3.43.64 behavior) ────────────


class TestEmptyBracketCleanup:
    def test_empty_brackets_stripped(self):
        """`[{quality}]` with empty quality should not leave `[]`."""
        out = resolve_filename_template(
            "{title} [{quality}]{ext}",
            {"title": "Scene", "quality": "", "ext": ".mp4"})
        assert out == "Scene .mp4" or out == "Scene.mp4"  # whitespace collapse
        assert "[]" not in out

    def test_empty_parens_stripped(self):
        """`({year})` with empty year should not leave `()`."""
        out = resolve_filename_template(
            "{title} ({year}){ext}",
            {"title": "Scene", "year": "", "ext": ".mp4"})
        assert "()" not in out

    def test_nonempty_brackets_preserved(self):
        """Brackets containing real text should NOT be stripped — many
        scene names legitimately contain '[Director Cut]' / '(2024)'."""
        out = resolve_filename_template(
            "{title}{ext}",
            {"title": "Scene [Director Cut]", "ext": ".mp4"})
        assert "[Director Cut]" in out

    def test_partial_template_no_orphan_separators(self):
        """Template starts with {studio}/ — if studio is empty we
        shouldn't end up with a leading slash."""
        out = resolve_filename_template(
            "{studio}/{title}{ext}",
            {"studio": "", "title": "Scene", "ext": ".mp4"})
        assert not out.startswith("/")
        assert out == "Scene.mp4"

    def test_double_slashes_still_collapsed(self):
        """Existing behavior — middle empty path components collapse."""
        out = resolve_filename_template(
            "{site}/{performer}/{title}{ext}",
            {"site": "Brazzers", "performer": "", "title": "Scene",
             "ext": ".mp4"})
        assert "//" not in out


# ─── Backward compat — old templates with empty new vars ────────────


class TestBackwardCompat:
    def test_v3_pre_64_template_still_works(self):
        """A template written before v3.43.64 with NO new variables
        should produce the same output as before."""
        out = resolve_filename_template(
            "{site}/{date}/{title}{ext}",
            {"site": "wow", "date": "2024-08-14",
             "title": "Scene 1", "ext": ".mp4"})
        assert out == "wow/2024-08-14/Scene 1.mp4"

    def test_missing_new_vars_dont_crash(self):
        """Passing the old context (no performer/studio/etc) shouldn't
        affect rendering of a v3-era template."""
        out = resolve_filename_template(
            "{title}{ext}",
            {"title": "Scene", "ext": ".mp4"})
        assert out == "Scene.mp4"
