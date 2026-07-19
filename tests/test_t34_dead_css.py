"""T34 (Tier 4) — D-107 dead-CSS finder.

Inspector that scans shipped CSS files and reports selectors not
referenced in templates or JS. Read-only.

Honours the existing repo_root resolver (LESSON L1) — accepts an
optional repo_root= for synthetic-tree tests.
"""
import json

from bulk_downloader import dev_suite as ds


# ── _extract_css_selectors ──────────────────────────────────────────

def test_extract_selectors_classes_and_ids():
    css = """
    .foo { color: red; }
    #bar { color: blue; }
    .baz, .qux { margin: 0; }
    """
    sel = ds._extract_css_selectors(css)
    assert "foo" in sel["classes"]
    assert "baz" in sel["classes"]
    assert "qux" in sel["classes"]
    assert "bar" in sel["ids"]


def test_extract_selectors_strips_comments():
    css = "/* .ignored { } */ .real { color: red; }"
    sel = ds._extract_css_selectors(css)
    assert "real" in sel["classes"]
    assert "ignored" not in sel["classes"]


def test_extract_selectors_handles_hyphenated_classes():
    css = ".widget-scope-bar .scope-tab.a { color: red; }"
    sel = ds._extract_css_selectors(css)
    # hyphenated classes preserved
    assert "widget-scope-bar" in sel["classes"]
    assert "scope-tab" in sel["classes"]
    assert "a" in sel["classes"]


def test_extract_selectors_skips_at_rules():
    css = """
    @keyframes spin { from { } to { } }
    .real { color: red; }
    """
    sel = ds._extract_css_selectors(css)
    # @keyframes head should not yield a class
    assert "real" in sel["classes"]
    assert "spin" not in sel["classes"]
    assert "from" not in sel["classes"]


# ── dead_css_finder integration with synthetic tree ─────────────────

def _make_synthetic_tree(root, *, css="", html="", js=""):
    """Build a tiny repo-shaped tree the finder will accept."""
    pkg = root / "bulk_downloader"
    pkg.mkdir(parents=True, exist_ok=True)
    static = pkg / "static"
    static.mkdir(parents=True, exist_ok=True)
    templates = pkg / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    (static / "test.css").write_text(css, encoding="utf-8")
    (templates / "index.html").write_text(html, encoding="utf-8")
    if js:
        (static / "test.js").write_text(js, encoding="utf-8")


def test_dead_css_reports_unused_class(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css=".used { color: red; } .unused { color: blue; }",
        html="<div class='used'>x</div>",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    assert r["ok"] is True
    assert "unused" in r["unused_classes"]
    assert "used" not in r["unused_classes"]


def test_dead_css_reports_unused_id(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css="#used { } #unused { }",
        html="<div id='used'>x</div>",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    assert "unused" in r["unused_ids"]
    assert "used" not in r["unused_ids"]


def test_dead_css_class_used_in_js_string(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css=".js-only { color: red; }",
        html="<div></div>",
        js="el.classList.add('js-only');",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    # Referenced from JS classList — should not be flagged dead
    assert "js-only" not in r["unused_classes"]


def test_dead_css_flags_truly_dead_class(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css=".widget-old { color: red; }",
        html="<div class='something-else'>x</div>",
        js="console.log('no reference');",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    assert "widget-old" in r["unused_classes"]


def test_dead_css_ambiguous_dynamic_class(clean_workdir, tmp_path):
    # JS constructs `widget-${kind}` — the class `widget-scoped` is
    # POSSIBLY built from this. The finder should flag as ambiguous,
    # not dead.
    _make_synthetic_tree(
        tmp_path,
        css=".widget-scoped { color: red; }",
        html="<div></div>",
        js="const c = `widget-${kind}`;",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    assert "widget-scoped" in r["ambiguous"]
    assert "widget-scoped" not in r["unused_classes"]


def test_dead_css_missing_static_dir(clean_workdir, tmp_path):
    # repo_root with no bulk_downloader/static → graceful fail
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    assert r["ok"] is False
    assert "static dir not found" in r["verdict"]


def test_dead_css_verdict_summary(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css=".a { } .b { } .c { } #d { } #e { }",
        html="<div class='a' id='d'>x</div>",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    # Verdict mentions counts
    assert r["total_classes"] == 3
    assert r["total_ids"] == 2
    assert "unused class" in r["verdict"]


def test_dead_css_is_json_serializable(clean_workdir, tmp_path):
    _make_synthetic_tree(
        tmp_path,
        css=".x { } .y { } #z { }",
        html="<div class='x' id='z'>x</div>",
    )
    r = ds.dead_css_finder(repo_root=str(tmp_path))
    # All output must round-trip JSON (no sets, no Path objects)
    json.dumps(r)
