"""T38 (Tier 4) — D-108 i18n string-coverage.

Extracts translatable strings from templates and diffs against shipped
locale files. Read-only. Uses i18n.extract_from_files() (existing
machinery).
"""
import json

from bulk_downloader import dev_suite as ds


# ── Synthetic-tree helpers ──────────────────────────────────────────

def _make_synthetic_tree(root, *, templates=None, locales=None):
    """Build a tiny repo-shaped tree.

    templates: {filename: html_content}
    locales:   {lang: {key: value}} — the dict is JSON-dumped.
    """
    pkg = root / "bulk_downloader"
    pkg.mkdir(parents=True, exist_ok=True)
    tdir = pkg / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    ldir = pkg / "locales"
    ldir.mkdir(parents=True, exist_ok=True)
    if templates:
        for fname, content in templates.items():
            (tdir / fname).write_text(content, encoding="utf-8")
    if locales is not None:
        for lang, data in locales.items():
            (ldir / f"{lang}.json").write_text(
                json.dumps(data), encoding="utf-8")


# ── Basic shape ─────────────────────────────────────────────────────

def test_i18n_coverage_no_templates(clean_workdir, tmp_path):
    # Empty tree → graceful fail
    _make_synthetic_tree(tmp_path)  # makes empty dirs only
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    assert r["ok"] is False
    assert "no template sources" in r["verdict"]


def test_i18n_coverage_extracts_strings_from_template(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html":
                   "<button>Save</button><button>Cancel</button>"},
        locales={"en": {"_meta": {"lang": "en"},
                        "btn.save": "Save",
                        "btn.cancel": "Cancel"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    assert r["ok"] is True
    assert r["extracted_strings"] >= 2


def test_i18n_coverage_reports_full_coverage(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={"en": {"_meta": {"lang": "en"}, "btn.save": "Save"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    locales = r["locales"]
    assert len(locales) == 1
    assert locales[0]["uncovered_count"] == 0
    assert locales[0]["coverage_pct"] == 100.0


def test_i18n_coverage_reports_uncovered(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html":
                   "<button>Save</button><button>NotTranslated</button>"},
        locales={"en": {"_meta": {"lang": "en"}, "btn.save": "Save"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    locales = r["locales"]
    assert locales[0]["uncovered_count"] >= 1
    assert "NotTranslated" in locales[0]["uncovered_strings"]


def test_i18n_coverage_reports_stale_entries(clean_workdir, tmp_path):
    # Locale has entries that no longer appear in source
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={"en": {"_meta": {"lang": "en"},
                        "btn.save": "Save",
                        "btn.gone": "ButtonThatNoLongerExists"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    locales = r["locales"]
    assert locales[0]["stale_count"] >= 1
    assert "ButtonThatNoLongerExists" in locales[0]["stale_entries"]


def test_i18n_coverage_meta_keys_not_counted_as_translations(
        clean_workdir, tmp_path):
    # _meta block is not translation content; should not be flagged as stale
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={"en": {"_meta": {"lang": "en",
                                    "description": "Some metadata"},
                        "btn.save": "Save"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    # "Some metadata" came from a _meta key and must not be reported
    # as a translation entry / stale value.
    locales = r["locales"]
    stale_values = locales[0]["stale_entries"]
    assert "Some metadata" not in stale_values


def test_i18n_coverage_multiple_locales(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={
            "en": {"_meta": {"lang": "en"}, "btn.save": "Save"},
            "es": {"_meta": {"lang": "es"}, "btn.save": "Guardar"},
        },
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    # Two locale files; en is full coverage (Save is in catalog),
    # es is uncovered (Guardar != Save)
    assert len(r["locales"]) == 2
    files = {l["file"]: l for l in r["locales"]}
    assert files["en.json"]["uncovered_count"] == 0
    assert files["es.json"]["uncovered_count"] >= 1


def test_i18n_coverage_malformed_locale_file(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
    )
    # Hand-write a malformed locale
    (tmp_path / "bulk_downloader" / "locales" / "broken.json").write_text(
        "{ not valid json", encoding="utf-8")
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    files = {l["file"]: l for l in r["locales"]}
    assert files["broken.json"]["ok"] is False


def test_i18n_coverage_verdict_summary(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={"en": {"_meta": {"lang": "en"}, "btn.save": "Save"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    assert "template(s)" in r["verdict"]
    assert "en.json" in r["verdict"]


def test_i18n_coverage_is_json_serializable(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        templates={"index.html": "<button>Save</button>"},
        locales={"en": {"_meta": {"lang": "en"}, "btn.save": "Save"}},
    )
    r = ds.i18n_coverage(repo_root=str(tmp_path))
    json.dumps(r)
