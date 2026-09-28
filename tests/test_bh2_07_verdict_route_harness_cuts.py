"""Tests for BH2-07: bd-verdict-route.sh also globs harness-cuts/*/.review/VERDICT-*.md."""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_07_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def vroute_env(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK)

    persist = tmp_path / "persist"
    persist.mkdir()
    review_wt = tmp_path / "review_wt"
    review_wt.mkdir()
    (persist / "logs").mkdir()
    (persist / "state").mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    say_bin = bin_dir / "bd-say.sh"
    say_bin.write_text('#!/bin/sh\necho "SAY: $*" >> ' + str(tmp_path / "say.log") + "\nexit 0\n")
    say_bin.chmod(0o755)

    tmux_bin = bin_dir / "tmux"
    tmux_bin.write_text("#!/bin/sh\necho bd-worker-A1-A\n")
    tmux_bin.chmod(0o755)

    landed_gate = bin_dir / "bd-landed-gate"
    landed_gate.write_text("#!/bin/sh\nexit 1\n")
    landed_gate.chmod(0o755)

    log_file = persist / "logs" / "offer-sweep.log"
    env = dict(
        os.environ,
        BD_PERSIST_ROOT=str(persist),
        BD_REVIEW_WT=str(review_wt),
        BD_VERDICT_ROUTE_LOG=str(log_file),
        BD_VERDICT_ROUTE_SAY=str(say_bin),
        BD_LANDED_GATE=str(landed_gate),
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
    )

    def invoke():
        return subprocess.run(
            ["bash", str(candidate)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return invoke, persist, review_wt, log_file


def test_routes_harness_cuts_verdicts(vroute_env):
    invoke, persist, review_wt, log_file = vroute_env

    # 1. Setup row cut in review_wt
    row_cut = review_wt / "row888"
    (row_cut / ".review").mkdir(parents=True)
    (row_cut / "DONE.md").write_text("seat: bd-worker-A1-A\n")
    (row_cut / ".review" / "VERDICT-correctness-test.md").write_text("VERDICT: REFUTE\nReason: row defect\n")

    # 2. Setup harness cut in persist/harness-cuts
    harness_cut = persist / "harness-cuts" / "bh-sample-cut-1"
    (harness_cut / ".review").mkdir(parents=True)
    (harness_cut / "DONE.md").write_text("seat: bd-worker-A1-A\n")
    (harness_cut / ".review" / "VERDICT-correctness-test.md").write_text("VERDICT: REFUTE\nReason: harness defect\n")

    result = invoke()
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"

    assert log_file.exists()
    log_content = log_file.read_text()

    # Both row888 and bh-sample-cut-1 must be routed
    assert "VERDICT-ROUTED 888 " in log_content
    assert "VERDICT-ROUTED bh-sample-cut-1 " in log_content


def test_no_harness_cuts_runs_cleanly(vroute_env):
    invoke, _persist, review_wt, log_file = vroute_env

    # Setup only row cut
    row_cut = review_wt / "row777"
    (row_cut / ".review").mkdir(parents=True)
    (row_cut / "DONE.md").write_text("seat: bd-worker-A1-A\n")
    (row_cut / ".review" / "VERDICT-correctness-test.md").write_text("VERDICT: REFUTE\nReason: clean test\n")

    result = invoke()
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"

    assert log_file.exists()
    log_content = log_file.read_text()
    assert "VERDICT-ROUTED 777 " in log_content
