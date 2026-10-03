"""O1698 W3 P4: pools codex and kimi become `--host` capable (bd-dispatch-ops operations.py + the two provider launchers).

The candidates live outside the repo (harness-work/FIX/o1698-w3-p4-provider-receipts) and are opted in with
BD_O1698_W3_P4_PROVIDER_RECEIPTS_CANDIDATE=<that dir>: scripts/operations.py, tests/checks.py, bd-launch-codex-role.sh,
bd-launch-kimi.sh. The plugin's own checks (tests/checks.py --host-placement) carry the fixtures: launch() with doubles
for hub/quota/premise, and the two REAL launchers run end to end against tmux/ssh/codex stubs with a real sleeping child
as the pane process. This runs them on the candidate set in a scratch plugin tree and pins the P4 check labels.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_W3_P4_PROVIDER_RECEIPTS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

P4 = [
    "host route codex",
    "host route kimi",
    "auto pool codex routes to the picked host",
    "auto pool kimi routes to the picked host",
    "provider RED codex --host .80 routes to the codex launcher, INTENT host=.80",
    "provider RED kimi --host .80 routes to the kimi launcher, INTENT host=.80",
    "provider RED codex --host, launcher prints no RESULT -> PLACEMENT-NO-PID, RESULT rc=in-doubt",
    "receipt RED codex launcher carries the Claude receipt block verbatim",
    "receipt RED kimi launcher carries the Claude receipt block verbatim",
    "receipt RED codex launcher (local seat) RESULT parses to pid/pid_start/rc=0",
    "receipt RED codex launcher (remote seat via ssh) RESULT parses to pid/pid_start/rc=0",
    "receipt RED kimi launcher (local seat) RESULT parses to pid/pid_start/rc=0",
    "receipt RED kimi launcher (remote seat via ssh) RESULT parses to pid/pid_start/rc=0",
]
PARITY = [
    "host route agy refused",
    "host route agy-claude refused",
    "host route grok refused",
    "provider agy --host refusal byte-identical to BASE",
    "provider agy-claude --host refusal byte-identical to BASE",
    "auto pool agy keeps the existing refusal",
    "auto pool agy-claude keeps the existing refusal",
    "auto pool grok keeps the existing refusal",
    "base parity plain launch worker codex",
    "base parity plain launch worker kimi",
    "base parity plain launch (no --host)",
    "base parity --host <ip> launch argv+journal",
]


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    src = Path(CANDIDATE)
    names = (
        "scripts/operations.py",
        "tests/checks.py",
        "bd-launch-codex-role.sh",
        "bd-launch-kimi.sh",
    )
    assert all((src / n).is_file() for n in names), [
        n for n in names if not (src / n).is_file()
    ]
    root = tmp_path_factory.mktemp("provider-receipts-plugin")
    for n in names:
        (root / n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / n, root / n)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update(
        LC_ALL="C",
        BD_RECEIPT_CODEX_LAUNCHER=str(root / "bd-launch-codex-role.sh"),
        BD_RECEIPT_KIMI_LAUNCHER=str(root / "bd-launch-kimi.sh"),
    )
    proc = subprocess.run(
        [sys.executable, str(root / "tests" / "checks.py"), "--host-placement"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = proc.stdout.splitlines()
    return {
        "rc": proc.returncode,
        "pass": {line[5:] for line in lines if line.startswith("PASS ")},
        "fail": [line for line in lines if line.startswith("FAIL ")],
        "summary": [line for line in lines if line.startswith("HOST-PLACEMENT checks")],
        "stderr": proc.stderr[-2000:],
    }


@pytest.mark.parametrize("label", P4)
def test_provider_receipt_check(results, label):
    assert label in results["pass"], (label, results["fail"], results["stderr"])


@pytest.mark.parametrize("label", PARITY)
def test_base_parity_check(results, label):
    assert label in results["pass"], (label, results["fail"], results["stderr"])


def test_whole_host_placement_population_passes(results):
    assert results["fail"] == [] and results["rc"] == 0, (
        results["fail"],
        results["stderr"],
    )
    assert len(results["summary"]) == 1, results["summary"]
    total = int(results["summary"][0].split("total=")[1].split(";")[0])
    assert total >= 57 and len(results["pass"]) == total, results["summary"]
