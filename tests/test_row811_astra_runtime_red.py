"""Row811: the real body-contract cases must agree under both runtimes."""
from pathlib import Path
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]
SUBJECT = ROOT / "tests/test_v3_66_726_body_contract.py"
# FF-E (body-contract r1) added two subject cases (the query-placeholder UNKNOWN and its
# typed keyless positive control): 12 -> 14. The test keeps its row811 name so its node id
# and every receipt that cites it stay valid.
_SUBJECT_CASES = 14


# Each leg runs the 12 subject cases in a fresh interpreter: 40-48 s idle; in a loaded
# -n 12 battery on a 64-core host (load 28-36) the pytest leg took 64-159 s and the
# runner leg 43-88 s, against 75 s each. Each leg gets 2x its loaded worst, rounded up
# to a minute; the item bound sits above both, so a hung leg is named by its own
# TimeoutExpired.
_LEG_TIMEOUT_S = {"pytest": 360, "runner": 180}


@pytest.mark.timeout(sum(_LEG_TIMEOUT_S.values()) + 60)
def test_the_twelve_body_contract_cases_agree_under_both_runtimes(tmp_path):
    environment = dict(os.environ)
    for key in ("PYTEST_CURRENT_TEST", "BD_INSTALL_DIR", "BD_HOME"):
        environment.pop(key, None)
    environment["BD_DISABLE_KEEPALIVE"] = "1"
    junit = tmp_path / "pytest.xml"
    runner_json = tmp_path / "runner.json"
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
                   "-p", "no:cacheprovider", str(SUBJECT), f"--junitxml={junit}"],
        "runner": [sys.executable, str(ROOT / "run_tests.py"), str(SUBJECT),
                   f"--json={runner_json}"],
    }
    outcomes = {}
    for name, command in commands.items():
        scratch = tmp_path / name
        scratch.mkdir()
        result = subprocess.run(command, cwd=scratch,
                                env={**environment, "BD_HOME": str(scratch)},
                                text=True, capture_output=True,
                                timeout=_LEG_TIMEOUT_S[name])
        outcomes[name] = {"rc": result.returncode, "stdout": result.stdout,
                          "stderr": result.stderr}
    assert junit.is_file() and runner_json.is_file(), outcomes
    raw_pytest_cases = ET.parse(junit).findall(".//testcase")
    pytest_cases = {
        case.attrib["name"]: "fail" if case.find("failure") is not None
        or case.find("error") is not None else "skip" if case.find("skipped")
        is not None else "pass"
        for case in raw_pytest_cases
    }
    runner = json.loads(runner_json.read_text())
    runner_cases = {case["test"]: case["status"] for case in runner["tests"]}
    evidence = {"pytest_cases": pytest_cases, "runner_cases": runner_cases,
                "runner_failures": runner["failures"], "outputs": outcomes}
    print("ROW811_RUNTIME_EVIDENCE=" + json.dumps(evidence))
    assert len(raw_pytest_cases) == len(runner["tests"]) == runner["total"] == _SUBJECT_CASES, evidence
    assert len(pytest_cases) == len(runner_cases) == _SUBJECT_CASES, evidence
    assert set(pytest_cases) == set(runner_cases), evidence
    assert outcomes["pytest"]["rc"] == 0, outcomes["pytest"]
    assert outcomes["runner"]["rc"] == 0, outcomes["runner"]
    assert all(status == "pass" for status in pytest_cases.values()), pytest_cases
    assert runner_cases == pytest_cases, {
        name: {"pytest": pytest_cases[name], "runner": runner_cases[name]}
        for name in pytest_cases if runner_cases[name] != pytest_cases[name]
    }
