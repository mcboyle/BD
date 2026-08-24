"""Cut 8: live tests state current contracts and skip evidence is exact."""

import os
import subprocess
import sys
from pathlib import Path

from tools import check_skip_baseline as SB

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "repo-wide"

RETIRED = {
    "docs/LEGACY_MIGRATION_PLAN.md",
    "reports/legacy_parity_baseline.json",
    "tools/legacy_parity.py",
    "tools/legacy_pin_scan.py",
    "tests/test_legacy_parity.py",
    "tests/test_legacy_pin_scan.py",
    "tests/test_p01_csrf_bootstrap.py",
    "tests/test_phase1_root_flip.py",
    "tests/test_phase4_retired.py",
    "tests/test_p4_cockpit_home.py",
    "tests/test_cut35_csrf_meta_contract_retired.py",
    "tests/test_cut35_csrf_meta_premise_retired_in_tools.py",
    "tests/SKIP_BASELINE.txt",
}

DIRECT = {
    "tests/test_t1_dashboard_wired.py",
    "tests/test_t2_history_wired.py",
    "tests/test_t3_t4_wired.py",
    "tests/test_t5_t6_wired.py",
    "tests/test_t7_notifications_wired.py",
    "tests/test_t8_cluster_wired.py",
    "tests/test_t9a_live_stream_wired.py",
    "tests/test_t9b_push_wired.py",
    "tests/test_t10_devtools_wired.py",
    "tests/test_t11_approval_wired.py",
    "tests/test_csrf_session_bootstrap.py",
    "tests/test_csrf_contract_reachability.py",
    "tests/test_csrf_tool_contracts.py",
    "tests/test_spa_root_routing_contract.py",
    "tests/test_cockpit_route_contract.py",
    "tests/test_cockpit_navigation_contract.py",
    "tests/test_skip_baseline.py",
}


def _tracked() -> set[str]:
    run = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = {p.decode() for p in run.stdout.split(b"\0") if p}
    assert len(paths) > 1000
    return paths


def test_historical_ratchets_and_old_contract_names_are_physically_absent():
    tracked = _tracked()
    bad = sorted(path for path in RETIRED
                 if path in tracked or os.path.lexists(ROOT / path))
    assert not bad, f"retired test authority returned: {bad}"


def _suites_in(text: str) -> set[str]:
    """Suites CI will ACTUALLY RUN, read line-aware from the workflow.

    WHY NOT `workflow.count(path)`. That counted a COMMENTED mention. Measured
    2026-08-24: turning
        `              tests/test_t1_dashboard_wired.py`
    into
        `              # tests/test_t1_dashboard_wired.py`
    -- two characters -- de-wires a required live-contract test from CI while
    this gate, whose entire job is proving it IS wired, stays green. CLAUDE.md
    A5's "a gate CI does not run does not exist", defeated by the gate written
    to prevent it.

    WHY NOT yaml.safe_load EITHER, which was this fix's first draft. `suites`
    is a FOLDED scalar (`>-`), and inside a folded scalar `#` is ordinary text,
    not a comment -- so the loader returns the path plus a stray `#` token and
    the evasion survives structural parsing. The evasion fixture below caught
    that draft, which is precisely why the fixture ships with the fix.

    So: find each `suites:` block, take its indented continuation lines, and
    drop what a shell/YAML reader would treat as commented on EACH LINE before
    tokenising. Line structure is the thing that matters here, and folding
    destroys it -- so it is read before the fold.
    """
    lines = text.splitlines()
    suites: set[str] = set()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("suites:"):
            base = len(lines[i]) - len(lines[i].lstrip())
            j = i + 1
            while j < len(lines):
                raw = lines[j]
                if not raw.strip():
                    j += 1
                    continue
                if (len(raw) - len(raw.lstrip())) <= base:
                    break
                live = raw.split("#", 1)[0]
                suites.update(tok for tok in live.split() if tok.endswith(".py"))
                j += 1
            i = j
            continue
        i += 1
    return suites


def _ci_wired_suites() -> set[str]:
    return _suites_in((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_current_behavior_contracts_are_tracked_and_directly_ci_wired():
    tracked = _tracked()
    assert DIRECT <= tracked
    wired = _ci_wired_suites()
    # PRECONDITION: the parser must have found a real workflow, or "nothing is
    # missing" would be vacuously true over an empty set.
    assert len(wired) > 50, (
        f"structural CI parse produced only {len(wired)} suites; the workflow "
        "shape changed and this gate is judging an empty denominator")
    missing = sorted(DIRECT - wired)
    assert not missing, f"current contract not wired in CI: {missing}"


def test_a_commented_out_ci_wiring_line_does_not_count_as_wired():
    """EVASION FIXTURE for the two-character de-wiring.

    The original textual gate passed on this input, and so did the first
    structural draft of the fix. It ships so that any future edit which
    reintroduces either shape goes RED here."""
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    victim = sorted(DIRECT)[0]
    assert victim in _suites_in(source), (
        f"{victim} is not wired to begin with; this fixture has no subject")
    evaded = source.replace("              " + victim,
                            "              # " + victim, 1)
    assert evaded != source, "could not build the evasion fixture"
    assert evaded.count(victim) >= 1, (
        "the commented form no longer contains the path, so this fixture is "
        "not reproducing the evasion it exists to pin")
    assert victim not in _suites_in(evaded), (
        f"a commented-out CI wiring line still reads as wired for {victim}")


def test_skip_baseline_is_exact_identity_reason_data_not_a_count():
    path = ROOT / "tests/SKIP_BASELINE.json"
    ordinary, collection = SB._read_baseline(path)

    assert (len(ordinary), len(collection)) == (39, 2)
    assert len(ordinary | collection) == 41
    assert set(ordinary).isdisjoint(collection)
    assert all(identity.startswith("<collection>::") for identity in collection)


def test_config_parity_parking_is_visible_as_skip_not_pass():
    """The parked ratchet must REPORT as skipped, observed by running it.

    WHY NOT A SOURCE SCAN. The old form asserted
    `source.count("pytest.skip(") == 2` and banned one exact comment spelling,
    `"return  # ratchet parked"`. Measured 2026-08-24: inserting
    `return  # parked by operator` after each docstring leaves both
    `pytest.skip(` occurrences in the file and uses a DIFFERENT comment, so the
    scan stays green while both parked tests launder from SKIP into PASS --
    defeating the exact property the gate is named for. Banning one spelling
    bans one spelling.

    So the outcome is OBSERVED. pytest is asked what these tests actually
    report, and a parked test that returns early reports `passed`, not
    `skipped`, no matter how it is written."""
    import json
    import tempfile
    target = ROOT / "tests/test_config_parity_ratchet.py"
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "r.json"
        subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-p", "no:randomly",
             "-q", "--timeout=120", "--json-report" if False else "-rA",
             "--tb=no", f"--junitxml={report.with_suffix('.xml')}"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "BD_DISABLE_KEEPALIVE": "1", "LC_ALL": "C"},
            timeout=300)
        import xml.etree.ElementTree as ET
        root = ET.parse(report.with_suffix(".xml")).getroot()
        suite = root.find("testsuite") if root.tag == "testsuites" else root
        cases = list(suite.iter("testcase"))

    # PRECONDITION: a zero-length or unparsed run would make every claim below
    # vacuously true.
    assert len(cases) >= 2, f"parked ratchet produced {len(cases)} cases"
    skipped = {c.get("name") for c in cases if c.find("skipped") is not None}
    assert len(skipped) == 2, (
        "the parked ratchet tests no longer REPORT as skipped -- they may have "
        f"laundered into passes: skipped={sorted(skipped)} of "
        f"{sorted(c.get('name') for c in cases)}")
    failed = [c.get("name") for c in cases
              if c.find("failure") is not None or c.find("error") is not None]
    assert not failed, failed


def test_a_parked_test_that_returns_early_is_not_mistaken_for_skipped():
    """EVASION FIXTURE. The old scan passed on this input.

    Proves the ban-one-spelling shape is genuinely defeated, so nobody
    reintroduces it: the evaded source still contains both `pytest.skip(`
    occurrences and does not contain the one banned comment."""
    source = (ROOT / "tests/test_config_parity_ratchet.py").read_text(
        encoding="utf-8")
    evaded = source.replace('"""\n', '"""\n    return  # parked by operator\n', 2)
    assert evaded.count("pytest.skip(") == source.count("pytest.skip(") == 2, (
        "the evasion changed the skip count, so it is not reproducing the "
        "shape that defeated the old gate")
    assert "return  # ratchet parked" not in evaded, (
        "the evasion used the one banned spelling; it must use a different one")
    assert "return  # parked by operator" in evaded
