"""tests/test_bh_drain_harness.py -- Regression tests for BH-DRAIN harness cut bundle (AMEND1).

Covers:
- FINDING-BH-bd-agy-audit-1-020 (MED): dead tool paths in bd-drain.sh (bd-row-audit.py, bd-row-chain.sh)
- FINDING-BH-bd-agy-audit-1-021 (MED): dead tool path in bd-bd4-drain.sh (bd-codex-session.sh)
- Fleet Rule 7: Positive and negative controls verifying probe can say YES and NO.
- Hermetic isolation: shims for ssh, git, and python3 tools; env overrides for BD_BD4_DRAIN_LOG and BD_DRAIN_REPO.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_DRAIN_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def candidate_dir() -> Path:
    p = Path(CANDIDATE)
    assert p.is_dir(), f"candidate directory does not exist: {p}"
    for script in ["bd-drain.sh", "bd-bd4-drain.sh"]:
        target = p / script
        assert target.is_file() and os.access(target, os.X_OK), f"{script} not executable in {p}"
    return p


def _setup_mock_git(bin_dir: Path) -> Path:
    mock_git = bin_dir / "git"
    mock_git.write_text("""#!/bin/sh
case "$*" in
  *"show origin/main:bulk_downloader/__init__.py"*)
    echo '__version__ = "3.66.1550"'
    exit 0
    ;;
  *"fetch origin main"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""")
    mock_git.chmod(0o755)
    return mock_git


def _setup_mock_ssh(bin_dir: Path) -> Path:
    mock_ssh = bin_dir / "ssh"
    mock_ssh.write_text("""#!/bin/sh
exit 0
""")
    mock_ssh.chmod(0o755)
    return mock_ssh


def test_bh_020_drain_sh_tool_paths(candidate_dir: Path, tmp_path: Path) -> None:
    """BH-020: bd-drain.sh must invoke row-audit and row-chain from authoritative harness location."""
    harness_mock = tmp_path / "mock_harness"
    harness_mock.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _setup_mock_git(bin_dir)

    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    audit_canary = tmp_path / "audit.ran"
    chain_canary = tmp_path / "chain.ran"

    mock_audit = harness_mock / "bd-row-audit.py"
    mock_audit.write_text(f"""#!/usr/bin/env python3
import sys
with open("{audit_canary}", "w") as f:
    f.write("AUDIT_RAN\\n")
sys.exit(0)
""")
    mock_audit.chmod(0o755)

    mock_chain = harness_mock / "bd-row-chain.sh"
    mock_chain.write_text(f"""#!/bin/bash
echo "CHAIN_RAN" > "{chain_canary}"
exit 0
""")
    mock_chain.chmod(0o755)

    script = candidate_dir / "bd-drain.sh"
    assert "BD_DRAIN_REPO" in script.read_text(), (
        "Script does not support BD_DRAIN_REPO; refusing to run to prevent git operations on live repo"
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BD_HARNESS_DIR"] = str(harness_mock)
    env["BD_DRAIN_ARTIFACTS"] = str(tmp_path)
    env["BD_DRAIN_REPO"] = str(mock_repo)
    (tmp_path / "codex-cuts").mkdir(exist_ok=True)
    (tmp_path / "inflight").mkdir(exist_ok=True)

    res = subprocess.run(
        [str(script), "999|slug-test|Title Test"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert audit_canary.exists(), f"Row audit canary was not created. Output:\n{res.stdout}\n{res.stderr}"
    assert chain_canary.exists(), f"Row chain canary was not created. Output:\n{res.stdout}\n{res.stderr}"
    assert "OK 999 merged" in res.stdout


def test_bh_021_bd4_drain_tool_path(candidate_dir: Path, tmp_path: Path) -> None:
    """BH-021: bd-bd4-drain.sh must invoke bd-codex-session from harness location without touching live state."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Mock sleep to avoid 20s delays
    mock_sleep = bin_dir / "sleep"
    mock_sleep.write_text("#!/bin/sh\nexit 0\n")
    mock_sleep.chmod(0o755)

    # Mock ssh to avoid network / live session inspection
    _setup_mock_ssh(bin_dir)

    session_canary = tmp_path / "session.ran"
    mock_session = tmp_path / "bd-codex-session.sh"
    mock_session.write_text(f"""#!/bin/bash
echo "SESSION_INVOKED" >> "{session_canary}"
echo "session=none"
exit 0
""")
    mock_session.chmod(0o755)

    test_log = tmp_path / "integrator.log"
    script = candidate_dir / "bd-bd4-drain.sh"
    assert "BD_BD4_DRAIN_LOG" in script.read_text(), (
        "Script does not support BD_BD4_DRAIN_LOG; refusing to run to prevent writing to live integrator.log"
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BD_CODEX_SESSION_SH"] = str(mock_session)
    env["BD_BD4_DRAIN_LOG"] = str(test_log)

    res = subprocess.run(
        [str(script), "1"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert session_canary.exists(), f"Session tool canary was not created. Output:\n{res.stdout}\n{res.stderr}"
    assert res.returncode == 0, f"bd-bd4-drain.sh failed with rc={res.returncode}:\n{res.stderr}"
    assert test_log.is_file(), f"test log {test_log} was not created"
    assert "BD4-RELEASED" in test_log.read_text()


def test_negative_controls_rule7(candidate_dir: Path, tmp_path: Path) -> None:
    """Rule 7: Negative control verifies probe can say NO; hermetic negative control with stub audit."""
    orig_drain = candidate_dir / "orig" / "bd-drain.sh"
    cand_drain = candidate_dir / "bd-drain.sh"
    assert orig_drain.is_file(), f"orig bd-drain.sh not found: {orig_drain}"
    assert cand_drain.is_file(), f"candidate bd-drain.sh not found: {cand_drain}"

    # Negative control: orig hardcodes bare root paths
    orig_drain_content = orig_drain.read_text()
    assert "/home/mboyle/bd-row-audit.py" in orig_drain_content
    assert "/home/mboyle/bd-row-chain.sh" in orig_drain_content
    assert "BD_ROW_AUDIT_PY" not in orig_drain_content
    assert "BD_ROW_CHAIN_SH" not in orig_drain_content
    assert "BD_DRAIN_REPO" not in orig_drain_content

    # Candidate introduces harness-first configurable paths and repo override
    cand_drain_content = cand_drain.read_text()
    assert "${BD_ROW_AUDIT_PY:-$H/bd-row-audit.py}" in cand_drain_content
    assert "${BD_ROW_CHAIN_SH:-$H/bd-row-chain.sh}" in cand_drain_content
    assert "${BD_DRAIN_REPO:-/home/mboyle/BulkDownloader}" in cand_drain_content

    orig_bd4 = candidate_dir / "orig" / "bd-bd4-drain.sh"
    cand_bd4 = candidate_dir / "bd-bd4-drain.sh"
    assert orig_bd4.is_file(), f"orig bd-bd4-drain.sh not found: {orig_bd4}"
    assert cand_bd4.is_file(), f"candidate bd-bd4-drain.sh not found: {cand_bd4}"

    orig_bd4_content = orig_bd4.read_text()
    assert "TOOL=/home/mboyle/bd-codex-session.sh" in orig_bd4_content
    assert "BD_CODEX_SESSION_SH" not in orig_bd4_content
    assert "LOG=/home/mboyle/bd-persist/integrator.log" in orig_bd4_content
    assert "BD_BD4_DRAIN_LOG" not in orig_bd4_content

    cand_bd4_content = cand_bd4.read_text()
    assert 'TOOL="${BD_CODEX_SESSION_SH:-/home/mboyle/bd-persist/harness/bd-codex-session.sh}"' in cand_bd4_content
    assert 'LOG="${BD_BD4_DRAIN_LOG:-/home/mboyle/bd-persist/integrator.log}"' in cand_bd4_content

    # Behavioral negative control: stub audit fails safe (exits 1), chain canary NEVER runs
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _setup_mock_git(bin_dir)

    stub_audit = tmp_path / "failing_audit.py"
    stub_audit.write_text("""#!/bin/sh
echo "AUDIT REFUSED ROW" >&2
exit 1
""")
    stub_audit.chmod(0o755)

    chain_canary = tmp_path / "chain_canary.ran"
    mock_chain = tmp_path / "mock_chain.sh"
    mock_chain.write_text(f"""#!/bin/sh
echo "CHAIN_RAN_UNEXPECTEDLY" > "{chain_canary}"
exit 0
""")
    mock_chain.chmod(0o755)

    empty_harness = tmp_path / "empty_harness"
    empty_harness.mkdir()
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BD_HARNESS_DIR"] = str(empty_harness)
    env["BD_ROW_AUDIT_PY"] = str(stub_audit)
    env["BD_ROW_CHAIN_SH"] = str(mock_chain)
    env["BD_DRAIN_ARTIFACTS"] = str(tmp_path)
    env["BD_DRAIN_REPO"] = str(mock_repo)
    (tmp_path / "codex-cuts").mkdir(exist_ok=True)
    (tmp_path / "inflight").mkdir(exist_ok=True)

    res = subprocess.run(
        [str(cand_drain), "999|slug-test|Title Test"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert "OK 999 merged" not in res.stdout
    assert "SKIP 999 -- REFUSED by bd-row-audit" in res.stdout
    assert not chain_canary.exists(), "Chain canary was created, but chain must not run when audit refuses"
