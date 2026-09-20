"""Row 917 -- AUTOMATED-EXTRACTION-SCHEMA-SYNTHESIS-FROM-USER-SESSIONS
(bd-teach-auto).

Authoring extraction templates by hand requires manual DOM inspection and
iterative syntax verification. bd-teach-auto instead synthesizes a typed,
executable Python extraction template class straight from a recorded teach
session (the same picks/raw_events shape teach_commit already consumes).

ACCEPTANCE (row917 register text):
  (1) generation of complete Python template classes from recorded traces
  (2) AST compilation validity
  (3) extractor execution against test DOM
"""
from __future__ import annotations

import ast
import importlib.util

import pytest

from bulk_downloader import app_sites_teach as teach

BD_GATE_SCOPE = "module"


# A realistic recorded click (production RECORDER_JS shape): the user
# clicked a resolution row inside the download modal; classify_download
# reads tag/text/href off records exactly like this one.
RAW_EVENTS_1080P_ROW = [
    {"tag": "a", "text": "1920 x 1080 Full HD", "href": "/dl/movie-1080.mp4",
     "id": "", "cls": "quality-row", "role": "", "testid": ""},
]

PICKS_EXPLICIT = {
    "row_selectors": ["a.quality-row"],
    "trigger_selectors": ["button.open-qualities"],
    "url_attribute": "href",
}

TEST_DOM = """
<html><body>
<div class="modal">
  <button class="open-qualities">Download</button>
  <a class="quality-row" href="https://cdn.example.test/movie-1080.mp4">1080p</a>
  <a class="quality-row" href="https://cdn.example.test/movie-720.mp4">720p</a>
  <a class="nav-link" href="/home">Home</a>
</div>
</body></html>
"""


# ---- selector derivation precedence ---------------------------------------

class TestSelectorDerivation:
    def test_picks_take_precedence_over_raw_events(self):
        sels = teach._derive_selectors_for_synthesis(
            picks={"row_selectors": ["a.explicit-pick"]},
            raw_events=RAW_EVENTS_1080P_ROW,
        )
        assert sels["row_selectors"] == ["a.explicit-pick"]

    def test_raw_events_alone_can_derive_a_row_selector(self):
        sels = teach._derive_selectors_for_synthesis(raw_events=RAW_EVENTS_1080P_ROW)
        assert sels.get("row_selectors"), "classify_download derived nothing from a realistic click record"

    def test_no_signal_derives_nothing(self):
        assert teach._derive_selectors_for_synthesis(picks={}, raw_events=[]) == {}


# ---- (1) generation of complete Python template classes -------------------

class TestSchemaSynthesis:
    def test_synthesizes_class_with_derived_fields(self):
        class_name, module_name, source = teach.synthesize_template_source(
            "acme_movies", picks=PICKS_EXPLICIT)
        assert class_name == "AcmeMoviesAutoTemplate"
        assert module_name == "_data_acme_movies_auto"
        assert "class AcmeMoviesAutoTemplate" in source
        assert "def extract(self, html: str)" in source
        assert "'a.quality-row'" in source
        assert "'button.open-qualities'" in source
        assert "ITEMS = [" in source

    def test_refuses_when_no_row_selectors_derivable(self):
        """Negative control: the refusal actually gates -- no row_selectors
        anywhere in the session means no file is ever written, not a
        silently-empty template that looks like a successful capture."""
        with pytest.raises(ValueError, match="no row_selectors"):
            teach.synthesize_template_source("acme_movies", picks={}, raw_events=[])

    def test_write_synthesized_template_refuses_before_touching_disk(self, tmp_path):
        with pytest.raises(ValueError):
            teach.write_synthesized_template("acme_movies", picks={}, raw_events=[],
                                              dest_dir=tmp_path)
        assert list(tmp_path.iterdir()) == [], "a file was written despite the refusal"

    def test_sid_with_hostile_characters_yields_a_safe_identifier(self):
        class_name, module_name, source = teach.synthesize_template_source(
            "9-weird.site!!", picks=PICKS_EXPLICIT)
        assert class_name.isidentifier()
        assert module_name.isidentifier()
        ast.parse(source)  # must still compile with a hostile sid


# ---- (2) AST compilation validity ------------------------------------------

class TestASTValidity:
    def test_generated_source_parses_as_valid_python(self):
        _cls, _mod, source = teach.synthesize_template_source(
            "acme_movies", picks=PICKS_EXPLICIT)
        tree = ast.parse(source)
        class_defs = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert any(c.name == "AcmeMoviesAutoTemplate" for c in class_defs)

    def test_generated_source_compiles(self):
        _cls, _mod, source = teach.synthesize_template_source(
            "acme_movies", picks=PICKS_EXPLICIT)
        compile(source, "<row917-generated>", "exec")

    def test_write_synthesized_template_validates_before_write(self, tmp_path, monkeypatch):
        """Deletion proof: if the AST validation call were removed, a broken
        generator would still write the file -- this proves the gate runs
        by making synthesize_template_source return unparsable text and
        checking write_synthesized_template refuses AND writes nothing."""
        monkeypatch.setattr(
            teach, "synthesize_template_source",
            lambda *a, **k: ("X", "_data_x_auto", "def broken(:\n"))
        with pytest.raises(SyntaxError):
            teach.write_synthesized_template("acme_movies", picks=PICKS_EXPLICIT,
                                              dest_dir=tmp_path)
        assert list(tmp_path.iterdir()) == [], "invalid source was written to disk"


# ---- (3) extractor execution against test DOM ------------------------------

def _load_generated_module(path, module_name):
    import sys
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass field resolution needs the module registered under its own
    # name (postponed annotations resolve via sys.modules[cls.__module__]).
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestExtractorExecution:
    def test_written_module_extracts_matching_rows_from_test_dom(self, tmp_path):
        path, class_name, _source = teach.write_synthesized_template(
            "acme_movies", picks=PICKS_EXPLICIT, dest_dir=tmp_path)
        assert path.exists()
        mod = _load_generated_module(path, "row917_generated_acme")
        cls = getattr(mod, class_name)
        instance = cls()
        rows = instance.extract(TEST_DOM)
        urls = {r["url"] for r in rows}
        assert urls == {"https://cdn.example.test/movie-1080.mp4",
                        "https://cdn.example.test/movie-720.mp4"}
        # the nav link must NOT match -- proves the extractor uses the
        # taught selector, not "every anchor on the page"
        assert not any("home" in (r["url"] or "") for r in rows)

    def test_written_module_items_list_matches_extractor_fields(self, tmp_path):
        """The ITEMS dict list (site_templates ingestion shape) and the
        dataclass the extractor runs both come from the SAME derived
        selectors -- they must never drift apart."""
        path, class_name, _source = teach.write_synthesized_template(
            "acme_movies", picks=PICKS_EXPLICIT, dest_dir=tmp_path)
        mod = _load_generated_module(path, "row917_generated_items")
        item = mod.ITEMS[0]
        cls = getattr(mod, class_name)
        instance = cls()
        assert item["learned"]["download"]["row_selectors"] == instance.row_selectors
        assert item["learned"]["download"]["url_attribute"] == instance.url_attribute

    def test_extractor_returns_empty_list_for_html_with_no_matches(self, tmp_path):
        path, class_name, _source = teach.write_synthesized_template(
            "acme_movies", picks=PICKS_EXPLICIT, dest_dir=tmp_path)
        mod = _load_generated_module(path, "row917_generated_empty")
        instance = getattr(mod, class_name)()
        assert instance.extract("<html><body>nothing here</body></html>") == []
