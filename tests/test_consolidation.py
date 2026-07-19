"""Regression tests for the cores-first consolidation.

Proves: (1) the thin wrappers delegate to the shared cores with identical output,
(2) template health does ONE scan, (3) kb_audit does ONE docs walk, (4) report_core
write helpers work. Runs under run_tests.py (zero-arg fns, repo root from __file__).
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO))

import template_core as TC  # noqa: E402
import template_completeness_score as TCS  # noqa: E402
import template_warning_catalog as TWC  # noqa: E402
import template_analytics as TA  # noqa: E402
import template_health_report as THR  # noqa: E402
import template_inventory as TI  # noqa: E402
import kb_core as KC  # noqa: E402
import kb_link_validator as KL  # noqa: E402
import kb_duplicate_detector as KD  # noqa: E402
import kb_staleness_report as KS  # noqa: E402
import kb_audit as KB  # noqa: E402
import report_core as RC  # noqa: E402

_ROOT = str(_REPO)


# ── A: wrappers delegate to template_core with identical output ────
def test_completeness_wrapper_equals_core():
    scan = TC.scan(_ROOT)
    assert TCS.score_tree(_ROOT) == TC.completeness(scan)

def test_warnings_wrapper_equals_core():
    scan = TC.scan(_ROOT)
    assert TWC.catalog(_ROOT) == TC.warnings(scan)

def test_analytics_wrapper_equals_core():
    assert TA.analyze(_ROOT) == TC.analytics(_ROOT)

def test_health_wrapper_equals_core():
    assert THR.build(_ROOT) == TC.health(_ROOT)


# ── A: template health performs exactly ONE scan ──────────────────
def test_health_build_single_scan():
    calls = {"n": 0}
    orig = TI.scan
    try:
        def counting(root="."):
            calls["n"] += 1
            return orig(root)
        TI.scan = counting
        THR.build(_ROOT)
        assert calls["n"] == 1, calls["n"]
    finally:
        TI.scan = orig


# ── D: kb wrappers delegate; kb_audit walks docs ONCE ─────────────
def test_kb_wrappers_equal_core():
    c = KC.collect(_ROOT)
    assert KL.validate(_ROOT) == KC.links(c)
    assert KD.detect(_ROOT) == KC.duplicates(c)

def test_kb_audit_single_walk():
    calls = {"n": 0}
    orig = KC.collect
    try:
        def counting(root="."):
            calls["n"] += 1
            return orig(root)
        KC.collect = counting
        KB.audit(_ROOT)
        assert calls["n"] == 1, calls["n"]
    finally:
        KC.collect = orig


# ── O/P2: monitoring consumed the data-layer providers — RETIRED ──
# The app_monitoring dashboard was empty since v3.66.345 and the module was
# removed in v3.66.353, so the monitoring->data-layer delegation no longer
# exists. The surviving provider (app_data_layer.collect_template_analytics)
# is exercised by the data-layer tests directly.


# ── report_core write helpers ─────────────────────────────────────
def test_report_core_write_helpers():
    d = tempfile.mkdtemp(prefix="rc_")
    p_md = RC.write_report(d, "x.md", "# hello\n")
    assert open(p_md).read() == "# hello\n"
    p_js = RC.write_json(os.path.join(d, "x.json"), {"a": 1})
    assert '"a": 1' in open(p_js).read()
    assert RC.yn(True) == "✓" and RC.yn(False) == "·"
