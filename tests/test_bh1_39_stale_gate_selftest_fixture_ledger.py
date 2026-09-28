"""BH1-39 (findings/FINDING-BH-bd-kimi-audit-2-035-worktree-ledger-test-pins-live-file.md).

`bd-stale-gate-cron.sh --selftest` pins clones under its mktemp dir and registers each one through
bd-worktree-ledger.sh without a BD_WT_LEDGER_FILE override, so every selftest run (the test lanes run it) added
phantom "active" rows to the LIVE WORKTREE-LEDGER.tsv (336 /var/tmp/bd-test-lane rows on 2026-09-28).

The candidate lives outside the repo (harness-work/FIX/bh1-39-bd-worker-B3-B/bd-stale-gate-cron.sh) and is opted in
with BD_BH1_39_CANDIDATE. The ledger tool is replaced (BD_WT_LEDGER_SH) by a stub that logs which ledger each call
would write and REFUSES to forward a call aimed at the live ledger, so neither the RED nor the GREEN run can add a
row to the live file. A call with a fixture ledger is forwarded to the real tool, so the selftest's own
fixture-ledger check runs against real ledger behaviour.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH1_39_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

LEDGER_TOOL = os.environ.get(
    "BD_BH1_39_LEDGER_TOOL", "/home/mboyle/bd-persist/harness/bd-worktree-ledger.sh"
)

LEDGER_STUB = """#!/bin/bash
echo "${BD_WT_LEDGER_FILE:-LIVE} $*" >> "$STUB_LOG"
[ -n "${BD_WT_LEDGER_FILE:-}" ] || exit 0   # never forward a write aimed at the live ledger
exec "$LEDGER_TOOL" "$@"
"""


@pytest.fixture(scope="module")
def selftest(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"BD_BH1_39_CANDIDATE={CANDIDATE} is not a file"
    assert os.access(LEDGER_TOOL, os.X_OK), f"ledger tool {LEDGER_TOOL} not executable"
    d = tmp_path_factory.mktemp("bh1_39")
    stub = d / "ledger-stub.sh"
    stub.write_text(LEDGER_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    log = d / "ledger-calls.log"
    env = dict(os.environ)
    env.pop(
        "BD_WT_LEDGER_FILE", None
    )  # the caller's environment must not mask the defect
    env.update(
        BD_WT_LEDGER_SH=str(stub),
        STUB_LOG=str(log),
        LEDGER_TOOL=LEDGER_TOOL,
        TMPDIR=str(d),
    )
    r = subprocess.run(
        ["bash", str(cand), "--selftest"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return r, calls


def test_selftest_registers_its_pins(
    selftest: tuple[subprocess.CompletedProcess[str], list[str]],
) -> None:
    """Positive control: the probe sees register calls at all (a zero would prove nothing)."""
    _, calls = selftest
    assert any(" register " in c and "/pins/stalegate-main-" in c for c in calls), calls


def test_no_register_call_targets_the_live_ledger(
    selftest: tuple[subprocess.CompletedProcess[str], list[str]],
) -> None:
    _, calls = selftest
    live = [c for c in calls if c.startswith("LIVE ")]
    assert live == [], (
        f"{len(live)} ledger call(s) aimed at the live WORKTREE-LEDGER.tsv: {live[:2]}"
    )


def test_selftest_passes_and_checks_the_fixture_ledger(
    selftest: tuple[subprocess.CompletedProcess[str], list[str]],
) -> None:
    r, _ = selftest
    assert (
        "ok   BH1-39: the pinned clone is registered in the FIXTURE ledger" in r.stdout
    ), r.stdout[-2000:]
    assert "SELFTEST PASS" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
    assert r.returncode == 0
