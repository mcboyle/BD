"""T12 (Tier 3) — D-40 filename-template previewer. Read-only view
over the real fname.resolve_filename_template engine. Distinct from
template_audit (which validates LOGIN templates).
"""
import os

from bulk_downloader import dev_suite as ds


# ── D-40 filename_template_preview ─────────────────────────────────

def test_template_preview_catalog_mode(clean_workdir):
    # no template -> catalog of known variables + per-variable demo
    r = ds.filename_template_preview()
    assert r["ok"] is True
    assert r["tool"] == "filename_template_preview"
    assert r["mode"] == "catalog"
    assert r["known_variable_count"] > 0
    names = [v["name"] for v in r["known_variables"]]
    for expected in ("title", "site", "resolution", "ext", "date"):
        assert expected in names
    # per-variable demo renders each placeholder against its sample
    demo = {d["placeholder"]: d for d in r["per_variable_demo"]}
    assert demo["title"]["rendered"] == "Hot Yoga Session"
    assert demo["site"]["rendered"] == "Pornhub"


def test_template_preview_renders_a_template(clean_workdir):
    r = ds.filename_template_preview(
        template="{site} - {title} [{resolution}]{ext}")
    assert r["ok"] is True
    assert r["mode"] == "preview"
    # canonical sample values produce the expected canonical output
    assert r["rendered"] == "Pornhub - Hot Yoga Session [1080p].mp4"
    assert set(r["placeholders_used"]) == {
        "site", "title", "resolution", "ext"}
    assert r["unknown_placeholders"] == []
    assert r["empty_placeholders"] == []


def test_template_preview_flags_unknown_placeholder(clean_workdir):
    # {titel} is misspelled; the engine strips it from the output
    r = ds.filename_template_preview(template="{titel} - {site}{ext}")
    assert r["unknown_placeholders"] == ["titel"]
    # the rendered output should not still have a literal {titel}
    assert "{titel}" not in r["rendered"]
    # the differs-from-naive flag should be True because naive
    # substitution would have left {titel} verbatim
    assert r["differs_from_naive_substitution"] is True


def test_template_preview_caller_context_overrides_samples(
        clean_workdir):
    r = ds.filename_template_preview(
        template="{site} - {title}{ext}",
        context={"site": "MySite", "title": "MyTitle"})
    assert r["rendered"] == "MySite - MyTitle.mp4"
    assert r["context_used"]["site"] == "MySite"
    assert r["context_used"]["title"] == "MyTitle"


def test_template_preview_empty_placeholder_flagged(clean_workdir):
    # explicitly empty out a placeholder -> reported in
    # empty_placeholders; the resolver trims the surrounding []
    r = ds.filename_template_preview(
        template="{title} [{quality}]{ext}",
        context={"quality": ""})
    assert "quality" in r["empty_placeholders"]
    # v3.43.64 behaviour: empty optional in brackets gets trimmed
    assert "[]" not in r["rendered"]


def test_template_preview_truncates_huge_template(clean_workdir):
    r = ds.filename_template_preview(template="x" * 5000)
    # the engine should not crash; the tool clamps the input length
    assert r["ok"] is True
    assert len(r["template"]) <= 500


def test_template_preview_strips_path_traversal_chars(clean_workdir):
    # a malicious title with .. and / must be sanitized by fname,
    # not pass through to the rendered output as a path-escape
    r = ds.filename_template_preview(
        template="{title}{ext}",
        context={"title": "../../etc/passwd"})
    # the rendered output must not contain '../' — fname sanitizes
    # path separators inside variable values
    assert "../" not in r["rendered"]


# ── dev route ──────────────────────────────────────────────────────

def test_filename_template_route_catalog_mode(fresh_app):
    j = fresh_app.get("/api/dev/filename_template").get_json()
    assert j["ok"] is True
    assert j["mode"] == "catalog"
    assert j["known_variable_count"] > 0


def test_filename_template_route_preview_mode(fresh_app):
    j = fresh_app.get(
        "/api/dev/filename_template?template={site}-{title}{ext}"
    ).get_json()
    assert j["ok"] is True
    assert j["mode"] == "preview"
    assert "rendered" in j


def test_t12_route_is_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        assert c.get(
            "/api/dev/filename_template").status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
