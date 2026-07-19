"""Gate tests for tools/legacy_parity.py (legacy-UI migration ratchet).

Zero-arg test functions per run_tests.py conventions; repo root derived from
__file__; baseline-mutation tests use a private tempdir copy so the shipped
reports/legacy_parity_baseline.json is never touched.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_lp_"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_measure_reports_empty_legacy_surface():
    """Phase 4 (v3.66.334): the legacy shell (templates/index.html +
    static/*.js|css) was DELETED, so the legacy API surface is now EMPTY.
    measure() must report legacy_total == 0 and legacy_only_count == 0
    (the prior >0 floor + the /api/{x} P4 tombstone are gone) while the SPA
    surface stays healthy. _scan_files tolerates the now-missing files."""
    lp = _load_tool()
    res = lp.measure()
    assert res["legacy_total"] == 0, res["legacy_total"]
    assert res["spa_total"] > 50, res["spa_total"]
    assert res["legacy_only_count"] == 0, res["legacy_only_count"]
    assert res["legacy_only"] == [], res["legacy_only"]
    # family map is a partition of legacy_only (both empty now)
    fam_total = sum(len(v) for v in res["families"].values())
    assert fam_total == res["legacy_only_count"] == 0


def test_check_semantics_red_on_regression_green_on_ratchet():
    """--check must FAIL when current has an endpoint the baseline lacks
    (regression) and PASS when the baseline is a superset (migration
    progress). Post-Phase-4 the real legacy surface is EMPTY, so we drive
    the semantics with a SYNTHETIC current set (monkeypatched measure()),
    exercised against a tempdir baseline — never the shipped one."""
    lp = _load_tool()
    # Synthetic legacy-only surface — the real one is empty after Phase 4,
    # so we inject one to exercise the red/green/grow logic end to end.
    current = ["/api/__synthetic_a__", "/api/__synthetic_b__"]
    orig_measure = lp.measure
    orig = lp.BASELINE_PATH
    tmp = Path(tempfile.mkdtemp(prefix="bd_lp_base_"))
    try:
        lp.measure = lambda: {
            "legacy_only": list(current),
            "legacy_only_count": len(current),
            "legacy_total": len(current),
            "spa_total": 999,
            "family_count": 1,
            "families": {"__synthetic__": list(current)},
        }
        lp.BASELINE_PATH = tmp / "legacy_parity_baseline.json"
        # GREEN: baseline == current
        lp.BASELINE_PATH.write_text(json.dumps(
            {"legacy_only": current, "legacy_only_count": len(current)}))
        assert lp.main(["--check"]) == 0
        # GREEN: baseline is a strict SUPERSET (one endpoint since migrated)
        lp.BASELINE_PATH.write_text(json.dumps(
            {"legacy_only": current + ["/api/__since_migrated__"],
             "legacy_only_count": len(current) + 1}))
        assert lp.main(["--check"]) == 0
        # RED: baseline MISSING one current endpoint -> regression
        lp.BASELINE_PATH.write_text(json.dumps(
            {"legacy_only": current[1:],
             "legacy_only_count": len(current) - 1}))
        assert lp.main(["--check"]) == 1
        # RED: --write-baseline refuses to grow without --allow-grow
        assert lp.main(["--write-baseline"]) == 1
        # GREEN: explicit operator override grows it
        assert lp.main(["--write-baseline", "--allow-grow"]) == 0
        rewritten = json.loads(lp.BASELINE_PATH.read_text())
        assert rewritten["legacy_only_count"] == len(current)
    finally:
        lp.measure = orig_measure
        lp.BASELINE_PATH = orig


def test_survivor_sw_js_not_scanned_as_legacy():
    """sw.js is a SPA survivor (push/PWA), retained after Phase 4 — it must not
    be scanned as a legacy file, or after the deletion cut the static/*.js glob
    would match only sw.js and mis-report it as the entire 'legacy' surface."""
    lp = _load_tool()
    names = [p.name for p in lp._legacy_files()]
    assert "sw.js" not in names, f"sw.js scanned as legacy: {names}"
    assert "manifest.json" not in names


def test_shipped_baseline_gates_this_tree():
    """The committed baseline must exist and --check must PASS against the
    shipped tree -- this is the actual in-band ratchet gate."""
    lp = _load_tool()
    assert lp.BASELINE_PATH.exists(), (
        "reports/legacy_parity_baseline.json missing -- run "
        "tools/legacy_parity.py --write-baseline and commit it")
    assert lp.main(["--check"]) == 0, (
        "legacy_parity --check FAILED: a new legacy-only endpoint was added "
        "without SPA wiring (see docs/LEGACY_MIGRATION_PLAN.md)")
