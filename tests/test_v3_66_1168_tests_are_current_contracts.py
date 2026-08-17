"""Cut 8: live tests state current contracts and skip evidence is exact."""

import json
import os
import subprocess
from pathlib import Path

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


def test_current_behavior_contracts_are_tracked_and_directly_ci_wired():
    tracked = _tracked()
    assert DIRECT <= tracked
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    missing = sorted(path for path in DIRECT if workflow.count(path) != 1)
    assert not missing, f"current contract not wired exactly once in CI: {missing}"


def test_skip_baseline_is_exact_identity_reason_data_not_a_count():
    path = ROOT / "tests/SKIP_BASELINE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("schema") == "bd-skip-baseline/1"
    rows = payload.get("skips")
    assert isinstance(rows, list) and rows
    identities = [row.get("identity") for row in rows]
    assert len(identities) == len(set(identities))
    assert all(isinstance(row.get("reason"), str) and row["reason"].strip()
               for row in rows)


def test_config_parity_parking_is_visible_as_skip_not_pass():
    source = (ROOT / "tests/test_config_parity_ratchet.py").read_text(
        encoding="utf-8")
    assert source.count("pytest.skip(") == 2
    assert "return  # ratchet parked" not in source
