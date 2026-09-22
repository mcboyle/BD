"""Row 1045: Canonical Record Attribution & Metadata Normalization.

Maps heterogeneous upstream media metadata (creator, publisher, series, title,
dates, tags) to canonical identities and filesystem-safe catalog structures,
stripping noisy artifacts and preserving attribution provenance.

RED provenance: Every test node asserts concrete behavioral outputs that fail
on base with wrong-answer AssertionErrors rather than unhandled ImportErrors.
Includes positive control baseline probe proving the test runner can say YES.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from bulk_downloader import metadata_normalizer as mn
from bulk_downloader.fname import resolve_filename_template

BD_GATE_SCOPE = "module"


def _get_normalizer():
    """Returns RecordAttributionNormalizer if implemented, else None."""
    try:
        from bulk_downloader.record_attribution import RecordAttributionNormalizer
        return RecordAttributionNormalizer()
    except ImportError:
        return None


def _normalize_record(rec):
    """Normalize record via metadata_normalizer.normalize_record if present; else return base input."""
    fn = getattr(mn, "normalize_record", None)
    if fn is not None:
        return fn(rec)
    return rec


def test_positive_control_existing_metadata_normalizer_baseline():
    """Positive control (Rule 7): proves harness and base metadata_normalizer can say YES."""
    from bulk_downloader.metadata_normalizer import to_filesystem_component, AliasTable
    assert callable(to_filesystem_component), "to_filesystem_component missing on base"
    assert issubclass(AliasTable, object)


def test_record_attribution_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented on metadata_normalizer."""
    assert getattr(mn, "normalize_record", None) is not None, (
        "Row 1045 capability missing: normalize_record on bulk_downloader.metadata_normalizer"
    )
    from bulk_downloader.record_attribution import (
        RecordAttributionNormalizer,
        AttributionRole,
        CanonicalAttribution,
        NormalizedRecord,
        normalize_record_attribution,
    )
    assert issubclass(AttributionRole, object)
    assert issubclass(CanonicalAttribution, object)
    assert issubclass(NormalizedRecord, object)
    assert callable(normalize_record_attribution)
    norm = RecordAttributionNormalizer()
    assert hasattr(norm, "normalize_attribution")
    assert hasattr(norm, "normalize_record")


def test_tag_normalization_deduplicates_lowercases_and_sorts():
    """Verify E1 / M3: tags are deduplicated, stripped, lowercased, and sorted alphabetically."""
    raw_tags = ["Zebra", "4k", "apple", "ZEBRA", "  4k  ", "Apple", "beta", ""]
    rec = _normalize_record({"title": "Test Title", "tags": raw_tags})
    tags = getattr(rec, "tags", rec.get("tags") if isinstance(rec, dict) else [])
    assert tags == ["4k", "apple", "beta", "zebra"], (
        f"Tags were not deduplicated, lowercased, and sorted: got {tags!r}"
    )


def test_full_record_metadata_normalization():
    """Verify complete record normalization strips noisy title brackets and cleans components."""
    raw_record = {
        "title": "Planet Earth II: Mountains [Official 4K 60FPS Video] (HQ)",
        "creator": "uploaded by David Attenborough",
        "publisher": "Nature Documentaries Ltd.",
        "series": "Planet Earth II",
        "date": "2023-04-15T14:30:00Z",
        "tags": ["Nature", "4k", "mountains", "NATURE", " 4k ", "wildlife", ""],
    }
    rec = _normalize_record(raw_record)
    clean_title = getattr(rec, "canonical_title", rec.get("title") if isinstance(rec, dict) else "")
    assert clean_title == "Planet Earth II: Mountains", (
        f"Title noise not stripped: expected 'Planet Earth II: Mountains', got {clean_title!r}"
    )


def test_clean_date_extraction():
    """Verify ISO datetimes are parsed into clean YYYY-MM-DD date components."""
    ctx = {"title": "Doc", "date": "2023-04-15T14:30:00Z"}
    rec = _normalize_record(ctx)
    clean_date = getattr(rec, "clean_date", rec.get("date") if isinstance(rec, dict) else "")
    assert clean_date == "2023-04-15", (
        f"Date not normalized to YYYY-MM-DD: expected '2023-04-15', got {clean_date!r}"
    )


def test_catalog_path_synthesis():
    """Verify structured catalog path is synthesized from creator, series, and title."""
    ctx = {
        "creator": "David Attenborough",
        "series": "Planet Earth II",
        "title": "Mountains",
    }
    rec = _normalize_record(ctx)
    cat_path = getattr(rec, "catalog_path", rec.get("catalog_path", "") if isinstance(rec, dict) else "")
    assert cat_path == "David Attenborough/Planet Earth II/Mountains", (
        f"Catalog path not synthesized correctly: expected 'David Attenborough/Planet Earth II/Mountains', got {cat_path!r}"
    )


def test_noise_prefix_and_decorations_stripping():
    """Verify normalizer strips common web noise prefixes like 'Uploaded by'."""
    raw_creator = "Uploaded by Jane Doe"
    rec = _normalize_record({"title": "Sample", "creator": raw_creator})
    creator_attr = getattr(rec, "primary_attribution", None)
    clean_name = creator_attr.canonical_name if creator_attr else (rec.get("creator") if isinstance(rec, dict) else "")
    assert clean_name == "Jane Doe", (
        f"Creator noise prefix not stripped: expected 'Jane Doe', got {clean_name!r}"
    )


def test_attribution_role_taxonomy_and_alias_mapping():
    """Verify attribution normalization maps raw variants to canonical identities with roles."""
    norm = _get_normalizer()
    if norm is not None:
        from bulk_downloader.record_attribution import AttributionRole
        norm.register_alias("OReilly", "O'Reilly Media", role=AttributionRole.PUBLISHER)
        res1 = norm.normalize_attribution("  o'reilly   ", role=AttributionRole.PUBLISHER)
        canonical = res1.canonical_name
        conf = res1.confidence
        # Fuzzy match for minor typo (confidence 0.85)
        res2 = norm.normalize_attribution("OReily", role=AttributionRole.PUBLISHER)
        assert res2.canonical_name == "O'Reilly Media"
        assert res2.fuzzy is True
        assert res2.confidence == 0.85
    else:
        canonical = mn.to_filesystem_component("  o'reilly   ")
        conf = 0.0

    assert canonical == "O'Reilly Media", (
        f"Alias not mapped to canonical name: expected 'O\\'Reilly Media', got {canonical!r}"
    )
    assert conf == 1.0


def test_alias_tables_caller_dict_isolation():
    """Verify RecordAttributionNormalizer copies alias_tables dict and does not mutate caller dict."""
    norm = _get_normalizer()
    caller_dict = {}
    if norm is not None:
        from bulk_downloader.record_attribution import RecordAttributionNormalizer, AttributionRole
        n = RecordAttributionNormalizer(alias_tables=caller_dict)
        n.register_alias("Alias", "Canonical", role=AttributionRole.CREATOR)
        n.normalize_attribution("Alias", role=AttributionRole.CREATOR)
        mutated = bool(caller_dict)
    else:
        mutated = True
    assert mutated is False, f"Caller-supplied dict was mutated: {caller_dict}"


def test_fname_resolve_filename_template_catalog_path_integration():
    """Verify resolve_filename_template synthesizes canonical catalog_path from record metadata."""
    ctx = {
        "title": "Mountains [Official 4K 60FPS Video] (HQ)",
        "creator": "uploaded by David Attenborough Official",
        "series": "Planet Earth II",
        "ext": ".mp4",
    }
    rendered = resolve_filename_template("{catalog_path}{ext}", ctx)
    assert rendered == "David Attenborough/Planet Earth II/Mountains.mp4", (
        f"Wrong output from resolve_filename_template: got {rendered!r}"
    )


def test_fname_resolve_filename_template_noise_stripping_integration():
    """Canonical (noise-stripped) values are reachable through their own variables."""
    ctx = {
        "title": "Wild Safari (HQ) [Official 1080p]",
        "performer": "by Jane Doe Official",
        "creator": "Jane Doe",
        "ext": ".mp4",
    }
    rendered = resolve_filename_template("{canonical_creator} - {canonical_title}{ext}", ctx)
    assert rendered == "Jane Doe - Wild Safari.mp4", (
        f"Wrong output from resolve_filename_template: got {rendered!r}"
    )


# RULING-2148 (row1045 BOUNCE): existing variables are never rewritten by the
# record normalizer. Each case fails on tree 14617f05 (performer replaced by
# creator; bracketed title stripped / collapsed to 'untitled').
@pytest.mark.parametrize("template, ctx, expected", [
    ("{performer} - {title}{ext}",
     {"performer": "Alice", "creator": "Bob Studio", "title": "Song", "ext": ".mp4"},
     "Alice - Song.mp4"),
    ("{performer}{ext}",
     {"performer": "O'Reilly & Sons: Vol.1", "creator": "X", "ext": ".mp4"},
     "O'Reilly & Sons_ Vol.1.mp4"),
    ("{title}{ext}",
     {"title": "Clip [Official Video] (HD)", "tags": ["a"], "ext": ".mp4"},
     "Clip [Official Video] (HD).mp4"),
    ("{title}{ext}",
     {"title": "[Official Video]", "tags": ["a"], "ext": ".mp4"},
     "[Official Video].mp4"),
    ("{artist} - {creator} - {title}{ext}",
     {"artist": "by Ann Official", "creator": "uploaded by Bob Official",
      "series": "S", "title": "T (HQ)", "ext": ".mp4"},
     "by Ann Official - uploaded by Bob Official - T (HQ).mp4"),
])
def test_fname_existing_variables_never_rewritten(template, ctx, expected):
    before = dict(ctx)
    rendered = resolve_filename_template(template, ctx)
    assert rendered == expected, f"row1045 rewrote an existing variable: got {rendered!r}"
    assert ctx == before, f"caller context mutated: {ctx!r}"


def test_fname_canonical_and_existing_variables_coexist():
    """Negative control for the test above: the same record DOES normalize when
    the template asks for canonical values, while {performer} stays verbatim."""
    ctx = {"performer": "Alice", "creator": "uploaded by Bob Studio Official",
           "title": "Song [Official Video]", "ext": ".mp4"}
    rendered = resolve_filename_template("{performer} - {canonical_creator} - {canonical_title}{ext}", ctx)
    assert rendered == "Alice - Bob Studio - Song.mp4", f"got {rendered!r}"


def test_fname_integration_strict_contract_does_not_swallow_errors():
    """Verify E2 / M6: fname does not hide normalizer failures behind a bare except-pass."""
    with patch("bulk_downloader.metadata_normalizer.normalize_record", side_effect=RuntimeError("Normalizer critical error")):
        with pytest.raises(RuntimeError, match="Normalizer critical error"):
            resolve_filename_template("{catalog_path}{ext}", {"creator": "Jane Doe", "ext": ".mp4"})
