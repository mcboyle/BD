from bulk_downloader.template_registry import find_template_for_url


def test_reptyle_template_loads():
    t = find_template_for_url("https://app.reptyle.com/")
    assert t is not None
    assert t["status"] == "enabled"
    assert t["host"] == "app.reptyle.com"


def test_reptyle_template_has_required_selectors():
    t = find_template_for_url("https://app.reptyle.com/")
    selectors = t.get("selectors", {})

    assert "login" in selectors
    assert "player" in selectors
    assert "quality" in selectors
    assert "download" in selectors


def test_reptyle_template_has_media_patterns_only():
    t = find_template_for_url("https://app.reptyle.com/")
    patterns = t.get("network_patterns", [])

    joined = "\n".join(patterns)

    assert "/movie/{id}/watch" in joined
    assert "/movie/{id}/download-resolution/{resolution}" in joined

    blocked = [
        "sentry",
        "cdnjs",
        "tsyndicate",
        "event-log",
        "exclusive-offers",
        "comments",
        "votes",
        "experiments",
        "banners",
    ]

    for bad in blocked:
        assert bad not in joined.lower()


# ── T1: most-specific host wins (added v3.66.194) ────────────────────────────
import json as _json          # noqa: E402
import tempfile as _tempfile   # noqa: E402
import os as _os              # noqa: E402


def _write_template(d, fname, host):
    fp = _os.path.join(d, fname)
    with open(fp, "w", encoding="utf-8") as fh:
        _json.dump({"host": host, "status": "enabled", "selectors": {}}, fh)
    return fp


def test_t1_specific_host_beats_generic_parent_domain():
    # generic parent-domain template sorts FIRST by filename; the site-specific
    # template sorts LAST. Pre-T1 code returned the first match (generic);
    # T1 must return the most-specific (shop.example.com).
    d = _tempfile.mkdtemp()
    _write_template(d, "00_generic.template.json", "example.com")
    _write_template(d, "99_specific.template.json", "shop.example.com")

    t = find_template_for_url("https://shop.example.com/item/5", template_dirs=[d])
    assert t is not None
    assert t["host"] == "shop.example.com"


def test_t1_specificity_independent_of_file_order():
    # reverse the filename sort order; the specific host must still win
    d = _tempfile.mkdtemp()
    _write_template(d, "00_specific.template.json", "shop.example.com")
    _write_template(d, "99_generic.template.json", "example.com")

    t = find_template_for_url("https://shop.example.com/item/5", template_dirs=[d])
    assert t["host"] == "shop.example.com"


def test_t1_parent_domain_still_matches_when_no_specific():
    # a generic parent-domain template still matches a subdomain URL when no
    # more-specific template exists (suffix match preserved)
    d = _tempfile.mkdtemp()
    _write_template(d, "generic.template.json", "example.com")

    t = find_template_for_url("https://blog.example.com/", template_dirs=[d])
    assert t is not None
    assert t["host"] == "example.com"


def test_t1_no_match_returns_none():
    d = _tempfile.mkdtemp()
    _write_template(d, "generic.template.json", "example.com")

    assert find_template_for_url("https://other.org/", template_dirs=[d]) is None
