"""Tests for the P1 developer-experience tooling (#15).

Covers tools/template_inventory.py, tools/check_doc_drift.py,
tools/check_version_consistency.py, and tools/verify_release.py.

Runs under the custom run_tests.py harness: zero-arg functions, no pytest
builtins (tempfile.mkdtemp instead of tmp_path), repo root derived from
__file__.
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))

import template_inventory as TI  # noqa: E402
import check_doc_drift as CDD  # noqa: E402
import check_version_consistency as CVC  # noqa: E402
import verify_release as VR  # noqa: E402


# ── template_inventory.assess (pure scoring/gating) ────────────────

def _complete_template():
    return {
        "host": "x.example.com", "status": "enabled",
        "schema": "review_candidate.v1",
        "api": {"base": "https://api.example.com/v1",
                "download_resolution": "/m/{id}/dl/{resolution}"},
        "resolutions": [1080, 720, 480],
        "network_patterns": ["https://api.example.com/v1/m/{id}/dl/{resolution}"],
        "selectors": {
            "login": {"email": "#e", "password": "#p", "submit": "#s"},
            "player": {"container": ".v", "play_button": ".play"},
            "quality": {"open_menu": ".q", "resolution_option": ".o"},
            "download": {"trigger": ".dl", "row_selectors": [".r1", ".r2"]},
        },
    }


def test_assess_complete_scores_100_and_ready():
    a = TI.assess(_complete_template())
    assert a["completeness_score"] == 100, a["completeness_score"]
    assert a["promotion_ready"] is True
    assert a["blocked_terms"] == []


def test_assess_incomplete_lower_and_not_ready():
    t = _complete_template()
    t["selectors"]["download"] = {}        # no trigger, no rows
    t["resolutions"] = []                  # empty ladder
    a = TI.assess(t)
    assert a["completeness_score"] < 100
    assert a["promotion_ready"] is False   # gate needs (trigger|rows) + resolutions


def test_assess_blocked_term_blocks_promotion():
    t = _complete_template()
    t["network_patterns"] = ["https://sentry.io/track?token=abc"]  # blocked + secret-ish
    a = TI.assess(t)
    assert a["blocked_terms"], "expected blocked terms to be detected"
    assert a["promotion_ready"] is False


# ── template_inventory.scan sanity rules (fixture tree) ────────────

def _make_tree():
    root = tempfile.mkdtemp(prefix="ti_")
    for d in ("reviewed", "enabled", "drafts", "review_candidates"):
        os.makedirs(os.path.join(root, "templates", d))
    return root


def test_scan_flags_enabled_in_drafts():
    root = _make_tree()
    try:
        t = _complete_template(); t["status"] = "enabled"
        with open(os.path.join(root, "templates", "drafts", "bad.json"), "w") as fh:
            json.dump(t, fh)
        data = TI.scan(root)
        assert any("NEVER be enabled" in v for v in data["sanity"]), data["sanity"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_scan_flags_reviewed_not_enabled():
    root = _make_tree()
    try:
        t = _complete_template(); t["status"] = "review_ready"   # wrong for reviewed/
        with open(os.path.join(root, "templates", "reviewed", "x.json"), "w") as fh:
            json.dump(t, fh)
        data = TI.scan(root)
        assert any("must be" in v and "enabled" in v for v in data["sanity"]), data["sanity"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_scan_accepts_both_draft_status_spellings():
    root = _make_tree()
    try:
        for i, st in enumerate(("draft_requires_review", "draft_review_required")):
            t = _complete_template(); t["status"] = st
            with open(os.path.join(root, "templates", "drafts", f"d{i}.json"), "w") as fh:
                json.dump(t, fh)
        data = TI.scan(root)
        assert data["sanity"] == [], data["sanity"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── check_doc_drift ────────────────────────────────────────────────

def test_doc_drift_required_present_in_real_tree():
    d = CDD.scan(str(_REPO))
    missing = [k for k, v in d["required"].items() if not v]
    assert missing == [], f"required docs missing from tree: {missing}"


def test_doc_drift_missing_required_flagged_in_fixture():
    root = tempfile.mkdtemp(prefix="dd_")
    try:
        os.makedirs(os.path.join(root, "bulk_downloader"))
        with open(os.path.join(root, "bulk_downloader", "__init__.py"), "w") as fh:
            fh.write('__version__ = "9.9.9"\n')
        d = CDD.scan(root)
        assert d["required"]["CHANGELOG.md"] is False
        assert d["version"] == "9.9.9"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── check_version_consistency (composes dev_suite) ─────────────────

def test_version_consistency_returns_structured_report():
    code, report = CVC.run(str(_REPO))
    assert report is not None, "dev_suite import failed"
    vc = report["version_consistency"]
    assert "version" in vc and isinstance(vc["mismatches"], list)
    assert report["changelog_lint"]["version"] == vc["version"]


# ── verify_release helpers (classifier + summary parse) ────────────

def test_summary_regex_parses_runner_line():
    line = "  Total: 49 | Passed: 48 | Failed: 1 | Skipped: 0"
    m = VR._SUMMARY_RE.search(line)
    assert m and tuple(map(int, m.groups())) == (49, 48, 1, 0)


def test_harness_signature_classifies_gtk_failure():
    out = "AssertionError: Module import failures: ['tray_app: Namespace Gtk not available']"
    assert any(sig in out for sig in VR._HARNESS_SIGNATURES)


def test_harness_signature_does_not_match_real_failure():
    out = "AssertionError: expected 5 rows, got 4"
    assert not any(sig in out for sig in VR._HARNESS_SIGNATURES)
