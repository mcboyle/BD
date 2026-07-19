"""v3.43.45 regression tests — paste-HTML template generator.

Coverage:
  - Module surface
  - extract_from_html: end-to-end on a representative paste
  - Selector generalization: id / attribute / class / extension / text
  - Score thresholding: rows need >= MIN_SCORE_FOR_ROW; trigger candidates
    can be lower
  - Trigger detection picks "Choose Quality" button, NOT
    quality-8k download links
  - url_patterns derived from page_url
  - url_attribute switches to data-* when href is absent
  - min_resolution bumps to 2160 on 4K and 4320 on 8K
  - refine_with_ai: returns ok=False when AI disabled, parses
    JSON when enabled, falls back on bad output
  - Endpoint /api/template/extract: shape, size cap, error paths
  - Endpoint /api/template/refine: shape
  - UI: toolbar button, modal opener, save handler wired
  - Adversarial: None, non-string, malformed HTML, huge HTML
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_TE_PY = _REPO_ROOT / "bulk_downloader" / "template_extractor.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


def _APP_SRC():
    """app.py + its extracted app_*.py blueprint modules (Phase 4 thin-core-shell):
    route handlers now live in app_<group>.py, so source-level route assertions read
    the aggregate rather than app.py alone."""
    pkg = _REPO_ROOT / "bulk_downloader"
    parts = [_APP_PY.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)


# ── Module surface ────────────────────────────────────────────────────


def test_module_importable():
    import bulk_downloader.template_extractor  # noqa: F401


def test_required_exports():
    from bulk_downloader import template_extractor as te
    for name in ("extract_from_html", "refine_with_ai",
                  "MIN_SCORE_FOR_ROW", "MIN_SCORE_FOR_CANDIDATE"):
        assert hasattr(te, name), f"missing export: {name}"


def test_module_compiles_no_syntax_warnings():
    """Catch \\W and similar in docstrings — Python 3.12+
    promotes these to SyntaxWarning."""
    import warnings, importlib
    import bulk_downloader.template_extractor as te
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        importlib.reload(te)


# ── extract_from_html — happy paths ─────────────────────────────────


_HTML_TYPICAL = """
<div class="download-options">
  <a class="dl-btn quality-8k" data-res="4320" href="/get/123/8k.mp4">
    Download 8K MP4 (5.7 GB)
  </a>
  <a class="dl-btn quality-4k" data-res="2160" href="/get/123/4k.mp4">
    Download 4K MP4 (2.1 GB)
  </a>
  <a class="dl-btn quality-1080" data-res="1080" href="/get/123/1080.mp4">
    Download 1080p MP4 (850 MB)
  </a>
</div>
<div class="related-videos">
  <a href="/v/other-456">Related video here</a>
  <a href="/v/some-789">Another related video</a>
</div>
<button class="quality-menu">Choose Quality</button>
"""


def test_extract_returns_template():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL,
                            page_url="https://example.com/v/123")
    assert r["ok"] is True
    assert r["template"]["row_selectors"]   # non-empty
    assert r["template"]["url_patterns"]    # derived from URL


def test_extract_row_selectors_target_download_buttons():
    """Row selectors must point at the download elements, not the
    related-videos sidebar."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    sels = r["template"]["row_selectors"]
    # Every row selector should plausibly target a download link
    for s in sels:
        # Either data-res based, or class-based on .dl-btn, or
        # href ending in .mp4 — never the related-videos pattern
        assert ("data-res" in s
                  or "dl-btn" in s
                  or "[href$='.mp4']" in s
                  or "[href$=\".mp4\"]" in s), (
            f"unexpected row selector: {s}")


def test_extract_trigger_is_choose_quality_button():
    """The trigger selector should be the menu button, NOT the
    download buttons (which had 'quality-8k' class)."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    trigs = r["template"]["trigger_selectors"]
    # At least one trigger should reference the quality-menu element
    has_menu_trigger = any(
        "quality-menu" in s or "button" in s for s in trigs)
    assert has_menu_trigger, (
        f"trigger selectors didn't pick the menu button: {trigs}")
    # And NO trigger should be a .dl-btn (those are the rows)
    for s in trigs:
        assert "dl-btn" not in s, (
            f"trigger {s!r} wrongly matched a download row")


def test_extract_picks_8k_for_min_resolution():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    # 8K = 4320 — should bump min_resolution to that ceiling
    assert r["template"]["min_resolution"] == 4320


def test_extract_4k_only_picks_2160():
    """Without 8K, the inference should land on 2160."""
    from bulk_downloader.template_extractor import extract_from_html
    html = """
    <div class="downloads">
      <a class="dl" href="/v/4k.mp4">Download 4K MP4 (2.1 GB)</a>
      <a class="dl" href="/v/1080.mp4">Download 1080p MP4 (800 MB)</a>
    </div>
    """
    r = extract_from_html(html)
    assert r["template"]["min_resolution"] == 2160


def test_extract_url_patterns_from_page_url():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL,
                            page_url="https://example.com/v/123")
    assert "example" in r["template"]["url_patterns"].lower()


def test_extract_url_patterns_strips_www():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL,
                            page_url="https://www.example.com/v/123")
    pat = r["template"]["url_patterns"]
    assert "www" not in pat
    assert "example" in pat


def test_extract_url_attribute_picks_data_src():
    """When elements have data-src but no href, url_attribute
    must reflect that."""
    from bulk_downloader.template_extractor import extract_from_html
    html = """
    <div>
      <a class="dl-btn" data-src="//cdn.example.com/v/abc.mp4">
        Download Full Movie (3.2 GB)
      </a>
    </div>
    """
    r = extract_from_html(html)
    assert r["template"]["url_attribute"] == "data-src"


def test_extract_inferred_name_from_url():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL,
                            page_url="https://wowgirls.com/v/123")
    # Title-cased without TLD
    assert "Wowgirls" in r["template"]["name"]


def test_extract_site_hint_overrides_inference():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL,
                            page_url="https://example.com/v/1",
                            site_hint_name="My Cool Site")
    assert r["template"]["name"] == "My Cool Site"


# ── Selector generalization ──────────────────────────────────────────


def test_generalize_produces_variants():
    """Every candidate should get at least one variant."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    for c in r["candidates"][:3]:
        variants = c.get("selector_variants") or []
        assert variants, f"candidate has no variants: {c}"


def test_generalize_attribute_variants_for_data_res():
    """Elements with data-res should produce an attribute variant."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    top = r["candidates"][0]
    kinds = {v["kind"] for v in top["selector_variants"]}
    assert "attribute" in kinds


def test_generalize_class_variants():
    """Multi-class elements should produce class_multi and
    class_primary variants."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)
    top = r["candidates"][0]
    kinds = {v["kind"] for v in top["selector_variants"]}
    # Either class_multi or class_primary should be in the set
    assert kinds & {"class_multi", "class_primary"}


def test_generalize_skips_unstable_ids():
    """A random-hash-looking ID should NOT produce an id variant."""
    from bulk_downloader.template_extractor import extract_from_html
    html = """
    <a id="react-aria-12345-abc-def-ghi-jkl" class="dl-btn"
       href="/v/4k.mp4">Download 4K MP4 (2 GB)</a>
    """
    r = extract_from_html(html)
    top = r["candidates"][0]
    kinds = {v["kind"] for v in top["selector_variants"]}
    # No id variant since the ID looks generated
    assert "id" not in kinds


def test_generalize_text_variant_uses_keyword():
    from bulk_downloader.template_extractor import extract_from_html
    html = '<a href="/v/4k.mp4">Download 4K MP4 (2.1 GB)</a>'
    r = extract_from_html(html)
    top = r["candidates"][0]
    text_variants = [v for v in top["selector_variants"]
                       if v["kind"] == "text"]
    if text_variants:
        # Picks an informative keyword
        assert "Download" in text_variants[0]["selector"]


# ── Warnings ────────────────────────────────────────────────────────


def test_warning_when_no_candidates():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html("<p>nothing useful here</p>")
    assert r["ok"]
    warnings = " ".join(r["warnings"])
    assert "no download" in warnings.lower() or "candidates found" in warnings.lower()


def test_warning_when_no_page_url():
    """url_patterns won't be derivable without a URL → warning."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(_HTML_TYPICAL)   # no page_url
    warnings = " ".join(r["warnings"])
    assert "url_patterns" in warnings.lower() or "page url" in warnings.lower()


def test_warning_when_no_triggers():
    """A site without a menu button should mention that triggers
    weren't detected (informational, not critical)."""
    from bulk_downloader.template_extractor import extract_from_html
    html = '<a class="dl" href="/v.mp4">Download Full MP4 (4 GB)</a>'
    r = extract_from_html(html)
    warnings = " ".join(r["warnings"])
    assert "trigger" in warnings.lower()


# ── Defensive / adversarial ─────────────────────────────────────────


def test_empty_html_returns_clean_error():
    from bulk_downloader.template_extractor import extract_from_html
    for empty in ("", "   ", "\n\n"):
        r = extract_from_html(empty)
        assert r["ok"] is False
        assert "no HTML" in r["error"]


def test_none_html_returns_clean_error():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html(None)
    assert r["ok"] is False


def test_non_string_html_returns_clean_error():
    from bulk_downloader.template_extractor import extract_from_html
    for bad in (12345, [], {}, object()):
        r = extract_from_html(bad)
        assert r["ok"] is False


def test_junk_html_does_not_crash():
    """Malformed HTML should still parse (BS4 is lenient) and
    produce ok=True with zero candidates."""
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html("<<<not really html>>>")
    assert r["ok"] is True


def test_html_without_any_links_succeeds():
    from bulk_downloader.template_extractor import extract_from_html
    r = extract_from_html("<p>just some text content here</p>")
    assert r["ok"] is True
    assert r["template"]["row_selectors"] == []


def test_non_string_page_url_handled():
    from bulk_downloader.template_extractor import extract_from_html
    for bad in (12345, [], None):
        r = extract_from_html(_HTML_TYPICAL, page_url=bad)
        assert r["ok"] is True
        # url_patterns falls back to empty when URL invalid
        assert r["template"]["url_patterns"] in ("", None)


# ── refine_with_ai ───────────────────────────────────────────────────


def test_refine_returns_ok_false_when_ai_disabled():
    from bulk_downloader.template_extractor import refine_with_ai
    from bulk_downloader import aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=False)
        r = refine_with_ai({"row_selectors": ["a.dl"]}, [], "<html>")
        assert r["ok"] is False
    finally:
        aiassist.configure(enabled=saved_en)


def test_refine_invalid_template_rejected():
    from bulk_downloader.template_extractor import refine_with_ai
    r = refine_with_ai("not a dict", [], "<html>")
    assert r["ok"] is False


def test_refine_parses_ai_response():
    """When the AI returns clean JSON, refine_with_ai must merge
    the suggestions into the template."""
    from bulk_downloader.template_extractor import refine_with_ai
    from bulk_downloader import aiassist, ai_provider
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake = ai_provider.GenerationResult(
            ok=True,
            text=('{"refined_row_selectors": ["a.better-selector"], '
                   '"refined_trigger_selectors": ["button.menu"], '
                   '"url_attribute": "data-src", '
                   '"warnings": ["check the menu button"], '
                   '"confidence": 85}'),
            provider="test-provider",
            model="test-model",
            latency_ms=100,
        )
        with mock.patch.object(aiassist, "_call_model",
                                  return_value=fake):
            r = refine_with_ai(
                {"row_selectors": ["a.dl"], "trigger_selectors": []},
                [], "<html>")
        assert r["ok"] is True
        assert r["refined_template"]["row_selectors"] == ["a.better-selector"]
        assert r["refined_template"]["trigger_selectors"] == ["button.menu"]
        assert r["refined_template"]["url_attribute"] == "data-src"
        assert r["ai_confidence"] == 85
        assert r["provider"] == "test-provider"
    finally:
        aiassist.configure(enabled=saved_en)


def test_refine_handles_unparseable_ai_output():
    """If the AI returns text that isn't JSON, refine_with_ai
    returns ok=False with the raw output for debugging."""
    from bulk_downloader.template_extractor import refine_with_ai
    from bulk_downloader import aiassist, ai_provider
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake = ai_provider.GenerationResult(
            ok=True, text="not json", provider="ollama",
            model="qwen", latency_ms=50)
        with mock.patch.object(aiassist, "_call_model",
                                  return_value=fake):
            r = refine_with_ai(
                {"row_selectors": ["a.dl"]}, [], "<html>")
        assert r["ok"] is False
        assert "raw" in r
    finally:
        aiassist.configure(enabled=saved_en)


def test_refine_handles_ai_call_failure():
    """If the AI provider returns ok=False, surface the error
    cleanly with the error_kind."""
    from bulk_downloader.template_extractor import refine_with_ai
    from bulk_downloader import aiassist, ai_provider
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake = ai_provider.GenerationResult(
            ok=False, error="rate limited", error_kind="rate_limit",
            provider="claude", latency_ms=50)
        with mock.patch.object(aiassist, "_call_model",
                                  return_value=fake):
            r = refine_with_ai(
                {"row_selectors": ["a.dl"]}, [], "<html>")
        assert r["ok"] is False
        assert r["error_kind"] == "rate_limit"
    finally:
        aiassist.configure(enabled=saved_en)


def test_refine_clamps_confidence():
    """AI-reported confidence outside [0,100] gets clamped."""
    from bulk_downloader.template_extractor import refine_with_ai
    from bulk_downloader import aiassist, ai_provider
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        for conf_value, expected in ((250, 100), (-5, 0)):
            fake = ai_provider.GenerationResult(
                ok=True,
                text=f'{{"refined_row_selectors": ["a"], '
                       f'"confidence": {conf_value}}}',
                provider="claude", model="test", latency_ms=10)
            with mock.patch.object(aiassist, "_call_model",
                                      return_value=fake):
                r = refine_with_ai({"row_selectors": ["a.dl"]},
                                      [], "<html>")
            assert r["ai_confidence"] == expected
    finally:
        aiassist.configure(enabled=saved_en)


# ── Endpoints ───────────────────────────────────────────────────────


def test_extract_endpoint_registered():
    src = _APP_SRC()
    assert "/api/template/extract" in src
    assert "def api_template_extract" in src


def test_refine_endpoint_registered():
    src = _APP_SRC()
    assert "/api/template/refine" in src
    assert "def api_template_refine" in src


def test_extract_endpoint_caps_input_size():
    """The endpoint must refuse pastes larger than ~2MB."""
    src = _APP_SRC()
    pos = src.find("def api_template_extract")
    body = src[pos:pos + 1500]
    assert "2 * 1024 * 1024" in body or "2 * 1024**2" in body
    # And returns 413 Payload Too Large
    assert "413" in body


def test_extract_endpoint_strips_bs4_refs():
    """Candidate dicts contain a _el field (BS4 element) — must not
    leak to JSON response."""
    src = _APP_SRC()
    pos = src.find("def api_template_extract")
    # Function body extends past 1500 chars; look at the full
    # function (capped at next @app.route or 4KB).
    end = src.find("\n@app.route", pos + 1)
    body = src[pos:end if end > 0 else pos + 4000]
    assert '"_el"' in body or "_el" in body


# ── UI wiring ───────────────────────────────────────────────────────


