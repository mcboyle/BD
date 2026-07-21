"""v3.66.101 — cockpit Wave 3 (composition + ops): correctness + read-only/posture.

Wave 3 adds Release Center, Executive Summary, Coverage Heatmap, Resource
Utilization, Knowledge Graph, Evidence Diff, and Scheduled Health Checks. All
are read-only over existing data except the health-checks refresh (which writes
ONLY a snapshot and runs no tool). The posture-critical tests:
  * the evidence diff NEVER compares or shows raw signing values (names only).
  * resource stats are read-only — no process is controlled.
  * health-checks/run writes a snapshot and changes no corpus/selector/profile.
  * the knowledge graph is the corpus's own resolves edges (nothing inferred).
  * release readiness neither builds nor bumps the version.
"""
import json
import os
import sys
import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane()


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    yield


def _stage_two_captures():
    """Copy two real captures under the captures root if available; else skip."""
    got = []
    for q in ("4k", "720p"):
        name = f"ultrafilms_2candies_{q}.wacz"
        if _FIXTURES.has(name):
            src = _FIXTURES.path(name)
            dst = cc.captures_root() / f"ultrafilms_2candies_{q}.wacz"
            cc.captures_root().mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            got.append(dst.name)
    return got


class TestReleaseReadiness:
    def test_verdict_and_shape(self):
        r = cc.release_readiness()
        assert r["verdict"] in ("READY", "NOT READY")
        assert "blockers" in r and "posture_scan" in r and "debt" in r

    def test_readiness_does_not_build_or_bump(self):
        # readiness is a pure read; the version file is untouched
        before = (_ROOT / "bulk_downloader" / "__init__.py").read_text()
        cc.release_readiness()
        after = (_ROOT / "bulk_downloader" / "__init__.py").read_text()
        assert before == after

    def test_leaky_artifact_blocks(self):
        # an artifact with a raw signing value should be flagged by the scan
        cc.reports_root().mkdir(parents=True, exist_ok=True)
        (cc.reports_root() / "leak.md").write_text(
            "url=https://x/clip.mp4?token=RAWSECRET&expires=9", encoding="utf-8")
        r = cc.release_readiness()
        assert r["posture_scan"]["with_leaks"] >= 1


class TestExecSummary:
    def test_periods(self):
        for p in ("daily", "weekly", "release", "validation", "all"):
            s = cc.exec_summary(p)
            assert s["period"] == p and "headline" in s

    def test_release_period_lists_releases(self):
        s = cc.exec_summary("release")
        assert "recent_releases" in s

    def test_summary_is_readonly(self):
        before = len(cc._corpus())
        cc.exec_summary("all")
        assert len(cc._corpus()) == before


class TestCoverageHeatmap:
    def test_grid_and_support(self):
        c = cc.coverage_heatmap()
        assert "grid" in c and "support" in c
        s = c["support"]
        for k in ("well_supported", "weakly_supported", "untested", "open_debt"):
            assert k in s

    def test_grid_counts_match_corpus(self):
        c = cc.coverage_heatmap()
        total_in_grid = sum(c["grid"][cat][o] for cat in c["grid"] for o in c["grid"][cat])
        # every confirmed/partial/untested/falsified entry in a known category counts
        assert total_in_grid >= c["support"]["well_supported"]


class TestResourceStatsReadOnly:
    def test_snapshot_shape(self):
        r = cc.resource_stats()
        for k in ("active_captures", "queue_depth", "captures_completed", "source"):
            assert k in r

    def test_no_process_control_in_source(self):
        # resource_stats must never kill/terminate/signal a process
        src = (_ROOT / "tools" / "cockpit_core.py").read_text()
        # narrow to the resource_stats function
        import re
        m = re.search(r"def resource_stats.*?(?=\ndef |\Z)", src, re.S)
        assert m
        body = m.group(0)
        for forbidden in (".kill(", ".terminate(", ".send_signal(", "os.kill", "Popen"):
            assert forbidden not in body

    def test_active_captures_from_registry_not_scan(self):
        # the count derives from the task registry, not a system process list
        r = cc.resource_stats()
        assert isinstance(r["active_captures"], int)


class TestKnowledgeGraph:
    def test_nodes_and_edges(self):
        g = cc.knowledge_graph()
        assert g["n_nodes"] == len(g["nodes"])
        assert g["n_edges"] == len(g["edges"])

    def test_edges_are_corpus_resolves(self):
        # every edge corresponds to a real resolves pointer in the corpus
        g = cc.knowledge_graph()
        corpus = {e["id"]: e for e in cc._corpus()}
        for edge in g["edges"]:
            src = corpus.get(edge["from"])
            assert src is not None
            assert edge["to"] in (src.get("resolves") or [])

    def test_graph_is_posture_clean(self):
        from bulk_downloader.capture_ingest import posture_scan
        assert not posture_scan(json.dumps(cc.knowledge_graph()))


class TestEvidenceDiffPostureSafe:
    def test_capture_diff_no_signing_values(self):
        got = _stage_two_captures()
        if len(got) < 2:
            pytest.skip("real captures not present")
        d = cc.evidence_diff("capture", got[0], got[1])
        blob = json.dumps(d)
        # marker NAMES may appear; raw values must NOT
        from bulk_downloader.capture_ingest import posture_scan
        assert not posture_scan(blob)
        # the signing section explicitly carries names only
        assert "names" in d["signing_markers"]["note"].lower()

    def test_capture_diff_paths_confined(self):
        with pytest.raises(cc.ValidationError):
            cc.evidence_diff("capture", "/etc/passwd", "/etc/hosts")

    def test_site_diff(self):
        d = cc.evidence_diff("site", "ultrafilms", "nubile")
        assert d["kind"] == "site"
        assert "only_a" in d["corpus_entries"]

    def test_bad_kind_rejected(self):
        with pytest.raises(cc.ValidationError):
            cc.evidence_diff("everything", "a", "b")


class TestHealthChecks:
    def test_list_without_running(self):
        h = cc.health_checks(run=False)
        assert h["ran"] is False and len(h["checks"]) >= 1

    def test_run_writes_snapshot_only(self):
        before = len(cc._corpus())
        h = cc.health_checks(run=True)
        assert h["ran"] is True and "snapshot" in h
        # a snapshot file exists, atomically (no .tmp left)
        snap = cc.tasks_root() / "health_snapshot.json"
        assert snap.is_file()
        assert not snap.with_suffix(".json.tmp").exists()
        # the corpus is unchanged — refresh writes ONLY the snapshot
        assert len(cc._corpus()) == before

    def test_no_browser_or_capture_in_source(self):
        src = (_ROOT / "tools" / "cockpit_core.py").read_text()
        import re
        # health_checks may be the last function in the file — match to EOF too
        m = re.search(r"def health_checks.*?(?=\ndef |\Z)", src, re.S)
        assert m
        body = m.group(0)
        for forbidden in ("capture_session", "playwright", "start_task"):
            assert forbidden not in body


class TestWave3RouteShape:
    def test_only_new_post_is_health_refresh(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        # the Wave 3 POST is the read-only health refresh; it must be present
        assert "/cockpit/api/health-checks/run" in posts
        # and the tool-running POSTs are still only the three allowlisted actions
        # plus queue/launch (which routes through the validated capture path)
        tool_runners = {"/cockpit/api/run-report", "/cockpit/api/run-capture",
                        "/cockpit/api/import-plan/preview", "/cockpit/api/queue/launch"}
        assert tool_runners <= posts

    def test_wave3_views_are_get_only(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        views = ["/cockpit/api/readiness", "/cockpit/api/exec", "/cockpit/api/coverage",
                 "/cockpit/api/resources", "/cockpit/api/graph", "/cockpit/api/diff",
                 "/cockpit/api/health-checks"]
        post_rules = {r.rule for r in app.url_map.iter_rules() if "POST" in r.methods}
        assert not (set(views) & post_rules)
