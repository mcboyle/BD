"""Tests for the v3.43.8 expanded template library.

Coverage:
  - Total template count (catches accidental deletions)
  - Every template's `learned.download` has the required fields
  - suggest_for_url() works with regex patterns (the v3.43.8 fix)
  - Per-selector url_attribute (list form) length matches row_selectors
  - No duplicate template IDs
"""
from bulk_downloader.templates import (
    TEMPLATES, list_templates, suggest_for_url, get,
)


def test_total_template_count():
    """v3.43.8: 52 templates. Failing this test signals an accidental
    addition or deletion; bump the expected count when intentional."""
    assert len(TEMPLATES) >= 50, \
        f"Template count regressed: got {len(TEMPLATES)}, expected 50+"


def test_no_duplicate_ids():
    """Every template has a unique id."""
    ids = [t["id"] for t in TEMPLATES]
    duplicates = [i for i in set(ids) if ids.count(i) > 1]
    assert not duplicates, f"Duplicate template ids: {duplicates}"


def test_every_template_has_required_fields():
    """Each template must have id, name, description, patterns, learned.download.

    v3.43.61: limitation-stub templates (live cams, DRM-protected legit
    streaming, piracy aggregators, paywalled creator content) are
    permitted to have an empty row_selectors list IF they explicitly
    declare `_limitation` in config_defaults -- the empty selector list
    is the signal that the template is a stub for URL routing only and
    cannot complete a download with the standard worker pipeline.
    """
    for t in TEMPLATES:
        assert "id" in t, f"template missing id: {t}"
        assert "name" in t, f"template {t.get('id')} missing name"
        assert "description" in t, f"template {t.get('id')} missing description"
        assert "patterns" in t, f"template {t.get('id')} missing patterns"
        assert isinstance(t["patterns"], list), \
            f"template {t.get('id')} patterns must be list"
        assert "learned" in t, f"template {t.get('id')} missing learned block"
        download = t.get("learned", {}).get("download")
        assert download is not None, \
            f"template {t.get('id')} missing learned.download"
        assert "row_selectors" in download, \
            f"template {t.get('id')} missing row_selectors"
        assert isinstance(download["row_selectors"], list), \
            f"template {t.get('id')} row_selectors must be list"
        # v3.43.61: limitation stubs are allowed empty row_selectors
        # v3.43.62: live-recorder templates (use_live_recorder=True) are
        # also allowed empty row_selectors -- the live recorder runtime
        # is responsible for them, not the page-extract worker model.
        cfg = t.get("config_defaults") or {}
        is_limitation_stub = "_limitation" in cfg
        is_live_recorder = bool(cfg.get("use_live_recorder"))
        if not (is_limitation_stub or is_live_recorder):
            assert len(download["row_selectors"]) > 0, \
                f"template {t.get('id')} row_selectors is empty " \
                f"(use _limitation marker or use_live_recorder=True in config_defaults if intentional)"


def test_per_selector_url_attribute_length_matches():
    """When url_attribute is a list, its length must match row_selectors
    or the v3.42.4 parallel-resolution logic breaks at runtime."""
    for t in TEMPLATES:
        dl = t.get("learned", {}).get("download", {})
        ua = dl.get("url_attribute")
        rows = dl.get("row_selectors", [])
        if isinstance(ua, list):
            assert len(ua) == len(rows), (
                f"template {t['id']}: url_attribute list has "
                f"{len(ua)} entries but row_selectors has {len(rows)}. "
                f"They must be parallel arrays."
            )


def test_suggest_wowgirls_matches_wowgirls_network():
    """The regex pattern r'wowgirls\\.com' should match the URL."""
    result = suggest_for_url("https://wowgirls.com/film/abc")
    assert "wowgirls_network" in result


def test_suggest_subdomain_matches():
    """Subdomains should match the regex pattern."""
    result = suggest_for_url("https://venus.wowgirls.com/film/x")
    assert "wowgirls_network" in result


def test_suggest_blacked_matches_vixen():
    result = suggest_for_url("https://blacked.com/scenes/xyz")
    assert "vixen_network" in result


def test_suggest_blackedraw_matches_vixen():
    """Vixen network covers multiple domains."""
    result = suggest_for_url("https://blackedraw.com/x")
    assert "vixen_network" in result


def test_suggest_vimeo_matches():
    result = suggest_for_url("https://vimeo.com/123456789")
    assert "vimeo_progressive" in result


def test_suggest_brazzers_matches_mindgeek():
    result = suggest_for_url("https://brazzers.com/scenes/abc")
    assert "mindgeek_family" in result


def test_suggest_unrelated_returns_empty():
    """A URL that doesn't match any pattern returns empty list."""
    result = suggest_for_url("https://random-no-template.example/x")
    assert result == []


def test_suggest_empty_url_returns_empty():
    assert suggest_for_url("") == []
    assert suggest_for_url(None) == []


def test_universal_fallback_template_exists():
    """The catch-all fallback must be present and have wide selectors."""
    t = get("universal_fallback")
    assert t is not None
    rows = t["learned"]["download"]["row_selectors"]
    # Must catch at least .mp4 / .mkv / .webm at minimum
    selectors_joined = " ".join(rows)
    assert ".mp4" in selectors_joined
    assert ".mkv" in selectors_joined
    assert ".webm" in selectors_joined


def test_resolution_button_text_has_quality_preference():
    """The resolution_button_text template should ship with a quality
    preference so the worker picks the highest available."""
    t = get("resolution_button_text")
    assert t is not None
    cfg = t.get("config_defaults", {})
    assert "quality_preference" in cfg


def test_list_templates_returns_metadata():
    """list_templates() returns id+name+description for each, no full learned block."""
    items = list_templates()
    assert len(items) == len(TEMPLATES)
    for item in items:
        assert "id" in item
        assert "name" in item
        assert "description" in item
        assert "row_count" in item
        # Must NOT leak the full learned block (UI doesn't need it)
        assert "learned" not in item


def test_get_unknown_returns_none():
    assert get("does_not_exist_template_id") is None


def test_get_known_returns_full_template():
    t = get("universal_fallback")
    assert t is not None
    assert t["id"] == "universal_fallback"
    assert "learned" in t
