"""Consolidated tests for the second-wave backlog tooling.

Covers the analyze/build functions on the real tree (shape-level) and the two new
additive blueprints (data layer O, report center C). Runs under run_tests.py.
"""
import sys
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO))

# cluster tool imports
import template_completeness_score as TCS  # noqa: E402
import template_warning_catalog as TWC  # noqa: E402
import template_health_report as THR  # noqa: E402
import capture_quality_report as CQR  # noqa: E402
import capture_statistics as CS  # noqa: E402
import changelog_analyzer as CL  # noqa: E402
import release_diff_summary as RDS  # noqa: E402
import function_inventory as FI  # noqa: E402
import module_inventory as MI  # noqa: E402
import dependency_inventory as DI  # noqa: E402
import test_inventory as TINV  # noqa: E402
import test_coverage_catalog as TCC  # noqa: E402
import technical_debt_report as TD  # noqa: E402
import compat_shim_audit as CSA  # noqa: E402
import offline_pack_report as OPR  # noqa: E402
import environment_report as ENV  # noqa: E402
import validate_templates as VT  # noqa: E402
import kb_audit as KB  # noqa: E402
import kb_link_validator as KL  # noqa: E402
import search_index_builder as SIB  # noqa: E402

_ROOT = str(_REPO)


import contextlib  # noqa: E402
import json as _json  # noqa: E402


@contextlib.contextmanager
def _seeded_reptyle_draft():
    """Self-seed a reptyle draft from the shipped reviewed gold into the real
    templates/drafts when absent, then remove ONLY what we seeded (never a real
    pre-existing draft). The clean zip / sandbox ships ONLY the reviewed gold, so
    draft-dependent analytics (total>=2, draft<->gold drift) are otherwise an
    env-coupled sandbox-RED / stash-GREEN divergence. The 515 pattern."""
    fname = "app.reptyle.com.template-draft.json"
    gold = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
    drafts = _REPO / "templates" / "drafts"
    cands = _REPO / "templates" / "review_candidates"
    pre_existing = (cands / fname).is_file() or (drafts / fname).is_file()
    seeded = None
    if not pre_existing:
        assert gold.is_file(), f"shipped reviewed gold missing: {gold}"
        cand = _json.loads(gold.read_text("utf-8"))
        cand["host"] = "app.reptyle.com"
        cand["status"] = "draft"  # a draft, never 'enabled' in a non-reviewed dir
        drafts.mkdir(parents=True, exist_ok=True)
        seeded = drafts / fname
        seeded.write_text(_json.dumps(cand), "utf-8")
    try:
        yield
    finally:
        if seeded is not None and seeded.is_file():
            seeded.unlink()


# ── A: template health ─────────────────────────────────────────────
def test_completeness_aggregate():
    with _seeded_reptyle_draft():
        d = TCS.score_tree(_ROOT)
        assert d["aggregate"]["n"] >= 2 and d["aggregate"]["mean"] is not None

def test_warning_catalog_counts():
    c = TWC.catalog(_ROOT)
    assert c["total"] >= 0 and isinstance(c["by_warning"], dict)

def test_template_health_build_shape():
    with _seeded_reptyle_draft():
        d = THR.build(_ROOT)
        assert "summary" in d and "per_template" in d and "warnings" in d
        assert d["summary"]["total"] >= 2


# ── B: capture ─────────────────────────────────────────────────────
def test_capture_quality_build():
    d = CQR.build(_ROOT)
    assert "artifact_metrics" in d and "template_generation_success_rate" in d

def test_capture_statistics():
    s = CS.statistics(_ROOT)
    assert "drafts" in s and "candidates" in s


# ── E: changelog ───────────────────────────────────────────────────
def test_changelog_parse():
    d = CL.parse(_ROOT)
    assert d["count"] > 50
    assert d["totals"]["features"] > 0

def test_release_diff():
    d = RDS.diff(_ROOT)
    assert "features" in d and "fixes" in d


# ── F: codebase ────────────────────────────────────────────────────
def test_function_inventory():
    d = FI.inventory(_ROOT)
    assert d["totals"]["functions"] > 100

def test_module_inventory():
    d = MI.inventory(_ROOT)
    assert d["count"] > 50 and d["total_loc"] > 1000

def test_dependency_graph():
    g = DI.graph(_ROOT)
    assert g["files_scanned"] > 0 and isinstance(g["most_imported"], dict)


# ── G: tests ───────────────────────────────────────────────────────
def test_test_inventory():
    d = TINV.inventory(_ROOT)
    assert d["total_tests"] > 1000

def test_coverage_catalog():
    d = TCC.catalog(_ROOT)
    assert d["modules"] > 0


# ── H/I/L/M/N ──────────────────────────────────────────────────────
def test_validate_templates_tree_not_hard_fail():
    d = VT.validate(_ROOT)
    assert d["hard_fail"] is False  # reviewed gold is complete

def test_technical_debt_scan():
    d = TD.scan(_ROOT)
    assert "markers_by_kind" in d and d["marker_total"] >= 0

def test_compat_shim_audit_targets():
    d = CSA.audit(_ROOT)
    for t in ("_browser_backend", "_use_cloakbrowser", "_feature_enabled"):
        assert t in d["targets"]

def test_offline_pack_report():
    d = OPR.report(_ROOT)
    assert "summary" in d and "installers" in d

def test_environment_report():
    d = ENV.report(_ROOT)
    assert d["python_version"]


# ── D: KB ──────────────────────────────────────────────────────────
def test_kb_audit_shape():
    d = KB.audit(_ROOT)
    assert "links" in d and "duplicates" in d and "staleness" in d

def test_kb_link_validator():
    d = KL.validate(_ROOT)
    assert d["docs_scanned"] > 0


# ── K: search index ────────────────────────────────────────────────
def test_search_index_build():
    idx = SIB.build(_ROOT)
    assert idx["entry_count"] > 0
    assert set(idx["by_type"]) == {"template", "report", "endpoint"}


# ── O: data layer blueprint ────────────────────────────────────────
def test_data_layer_registers_five():
    from bulk_downloader.app_data_layer import register_routes
    app = Flask(__name__)
    assert register_routes(app) == 15

def test_data_layer_release_and_kb_endpoints():
    from bulk_downloader.app_data_layer import register_routes
    app = Flask(__name__); register_routes(app); c = app.test_client()
    r = c.get("/api/data/release_analytics")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r2 = c.get("/api/data/template_health")
    assert r2.status_code == 200 and r2.get_json()["ok"] is True


# ── C: report center blueprint ─────────────────────────────────────
def test_report_center_page_and_sections():
    from bulk_downloader.app_report_center import register_routes
    app = Flask(__name__); register_routes(app); c = app.test_client()
    assert register_routes(Flask(__name__)) == 9
    body = c.get("/cockpit/reports").get_data(as_text=True)
    assert "Report Center" in body
    assert "Needs operator click-through validation" in body
    for title in ("Template Reports", "Capture Reports", "Queue Reports",
                  "Release Reports", "Health Reports"):
        assert title in body, title
    secs = c.get("/api/report_center/sections").get_json()
    assert len(secs["sections"]) == 12

def test_report_center_read_only():
    from bulk_downloader.app_report_center import register_routes
    app = Flask(__name__); register_routes(app)
    body = app.test_client().get("/cockpit/reports").get_data(as_text=True).lower()
    assert "<form" not in body and "<button" not in body
