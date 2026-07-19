"""v3.66.106 — Band C (scoring models) + Band D (UX layer) + #127 (E survivor).

Band C: maturity_score, complexity_score, org_health_index — TRANSPARENT
composites (every input observable, weights explicit, fully decomposable, clearly
labeled as defined-not-objective). Band D: operational_narrative (deterministic
prose, no model call) + front-end command palette / keyboard shortcuts / focus
mode / responsive layout. E survivor: portfolio_opportunity (#127, per-site
rollup — the one DUP that adds real computation).

All read-only; no new POST surface (the UX layer is pure front-end).
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    yield


class TestMaturityScore:
    def test_decomposable_and_in_range(self):
        d = cc.maturity_score()
        assert 0 <= d["score"] <= 100
        assert set(d["components"]) == {"validation_coverage", "debt_cleanliness",
                                        "resolution_activity"}
        # score must equal the mean of components × 100 (fully transparent)
        mean = sum(d["components"].values()) / len(d["components"]) * 100
        assert abs(d["score"] - round(mean)) <= 1

    def test_band_label(self):
        assert cc.maturity_score()["band"] in ("nascent", "developing", "mature")

    def test_weights_and_inputs_exposed(self):
        d = cc.maturity_score()
        assert "weights" in d and "inputs" in d  # the formula is fully visible


class TestComplexityScore:
    def test_drivers_are_real_counts(self):
        d = cc.complexity_score()
        assert d["drivers"]["sites"] >= 1
        assert "references" in d  # references documented + adjustable
        assert 0 <= d["complexity_index"] <= 100

    def test_index_is_transparent(self):
        d = cc.complexity_score()
        # index = mean of min(driver/ref,1) × 100
        drv, ref = d["drivers"], d["references"]
        expect = round(sum(min(drv[k] / ref[k], 1.0) for k in drv) / len(drv) * 100)
        assert d["complexity_index"] == expect


class TestOrgHealthIndex:
    def test_composite_of_three(self):
        d = cc.org_health_index()
        assert set(d["components"]) == {"maturity", "concern_freedom", "evidence_freshness"}
        assert 0 <= d["score"] <= 100

    def test_band(self):
        assert cc.org_health_index()["band"] in ("healthy", "watch", "at_risk")


class TestScoringReadOnlyAndClean:
    def test_no_mutation(self):
        before = len(cc._corpus())
        cc.maturity_score(); cc.complexity_score(); cc.org_health_index()
        assert len(cc._corpus()) == before

    def test_posture_clean(self):
        from bulk_downloader.capture_ingest import posture_scan
        for fn in (cc.maturity_score, cc.complexity_score, cc.org_health_index):
            assert not posture_scan(json.dumps(fn()))

    def test_labeled_as_defined_not_objective(self):
        # the honesty contract: each score's note must flag it as a DEFINED composite
        for fn in (cc.maturity_score, cc.org_health_index):
            assert "DEFINED" in fn()["_note"]
        assert "DEFINED" in cc.complexity_score()["_note"]


class TestNarrative:
    def test_prose_from_real_numbers(self):
        d = cc.operational_narrative()
        assert len(d["paragraphs"]) >= 3
        # mentions the actual corpus size (deterministic, from data)
        assert str(len(cc._corpus())) in " ".join(d["paragraphs"])

    def test_no_model_call_in_source(self):
        # narrative must be deterministic templating, not an LLM/network call
        src = (_ROOT / "tools" / "cockpit_core.py").read_text()
        import re
        m = re.search(r"def operational_narrative.*?(?=\ndef |\Z)", src, re.S)
        assert m
        body = m.group(0)
        for forbidden in ("requests.", "urllib", "http", "openai", "anthropic", "ollama"):
            assert forbidden not in body.lower()


class TestPortfolioOpportunityE127:
    def test_rolls_up_by_site(self):
        d = cc.portfolio_opportunity()
        assert "by_site" in d
        # it's a per-site aggregation NOT exposed by capture_opportunity (flat list)
        for r in d["by_site"]:
            assert "opportunity_score" in r and {"p1", "p2", "p3"} <= set(r)

    def test_score_weights_p1_heaviest(self):
        for r in cc.portfolio_opportunity()["by_site"]:
            assert r["opportunity_score"] == r["p1"] * 3 + r["p2"] * 2 + r["p3"]


class TestBandDFrontEnd:
    def test_palette_and_shortcuts_present(self):
        from tools import cockpit_console as cc2
        src = Path(cc2.__file__).read_text()
        assert 'id="cmdk"' in src               # palette DOM
        assert "cmdkOpen" in src                 # palette logic
        assert "GMAP" in src                     # g-then-key shortcuts
        assert "metaKey||e.ctrlKey" in src.replace(" ", "")  # ⌘K/Ctrl+K
        assert "if(e.key==='f')" in src and "setCollapsed(" in src  # focus/collapse shortcut (f)

    def test_responsive_css_present(self):
        from tools import cockpit_console as cc2
        src = Path(cc2.__file__).read_text()
        assert "@media (max-width:820px)" in src   # mobile/tablet layout
        assert "navopen" in src

    def test_ux_adds_no_routes_or_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # no palette/shortcut/narrative endpoint — all front-end or GET
        assert not any("cmdk" in p or "palette" in p or "shortcut" in p for p in posts)


class TestBandCDPagesWired:
    def test_pages_have_nav_and_renderer(self):
        from tools import cockpit_console as cc2
        src = Path(cc2.__file__).read_text()
        # portfolioopp + narrative remain their own nav entries
        for page in ("portfolioopp", "narrative"):
            assert (f'data-p="{page}"' in src or f"{page}:[" in src) and f"PAGES.{page}=" in src
        # maturity/complexity/orghealth were CONSOLIDATED into the "scores" page
        # (v3.66.107): their renderers are kept intact (deep-link reachable) but
        # their individual nav entries were replaced by a single "scores" entry.
        assert ('data-p="scores"' in src or "scores:[" in src) and "PAGES.scores=" in src
        for sub in ("maturity", "complexity", "orghealth"):
            assert f"PAGES.{sub}=" in src  # renderer kept for deep-links / direct go()

    def test_endpoints_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        for ep in ("/cockpit/api/maturity", "/cockpit/api/complexity",
                   "/cockpit/api/org-health", "/cockpit/api/portfolio-opportunity",
                   "/cockpit/api/narrative"):
            assert c.get(ep).status_code == 200
