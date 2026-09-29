"""bh1-47 (BH-bd-kimi-audit-1-172): bd-launch-kimi.sh swallowed a failed role claim.

`"$ROLE_CLAIM" claim ... >/dev/null 2>&1 || true` discarded the exit code, so a seat whose claim failed
was launched unregistered (invisible to the cardinality guard and `who`) and the launcher reported plain
success. GREEN: the launch still succeeds, but stderr carries a WARN naming the failed claim and the
LAUNCHED line says claim=FAILED (claim=ok on success), as bd-launch-role.sh / bd-launch-codex-role.sh do.

The candidate lives outside the repo (harness-cut shape, O1045). Opt in with
BD_BH1_47_CANDIDATE=<absolute path to the candidate bd-launch-kimi.sh>. tmux and the claim tool are
stubs; the seat dir is tmp_path (BD_KIMI_SEATS_ROOT seam), so nothing live is launched or registered.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH1_47_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

NAME = "zz-bh1-47-probe"
WARN = "WARN could not record the role claim for worker/" + NAME

# has-session answers 0 only after new-session ran, so the duplicate-session check passes and the
# startup loop sees a live pane that shows the Kimi welcome screen.
TMUX_STUB = """#!/bin/bash
echo "tmux $*" >> "$STUB_LOG/tmux.log"
case "$1" in
  has-session) [ -e "$STUB_LOG/session" ] ;;
  new-session) : > "$STUB_LOG/session" ;;
  capture-pane) echo "Welcome to Kimi Code" ;;
  *) exit 0 ;;
esac
"""

CLAIM_STUB = """#!/bin/bash
echo "claim-tool $*" >> "$STUB_LOG/claim.log"
exit "${CLAIM_RC:-0}"
"""


def _launch(tmp_path, claim_rc=None, claim_tool=None):
    script = Path(CANDIDATE)
    assert script.is_file(), f"bh1-47: named candidate missing: {script}"
    bindir, logs, seats = tmp_path / "bin", tmp_path / "logs", tmp_path / "seats"
    for d in (bindir, logs, seats):
        d.mkdir()
    (bindir / "tmux").write_text(TMUX_STUB)
    (bindir / "tmux").chmod(0o755)
    stub = tmp_path / "claim-stub.sh"
    stub.write_text(CLAIM_STUB)
    stub.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        STUB_LOG=str(logs),
        BD_KIMI_SEATS_ROOT=str(seats),
        BD_KIMI_ROLE_CLAIM=str(claim_tool or stub),
        BD_KIMI_START_TRIES="3",
        CLAIM_RC=str(claim_rc if claim_rc is not None else 0),
    )
    env.pop("TMUX", None)
    r = subprocess.run(["bash", str(script), "worker", "pool", NAME], env=env, capture_output=True, text=True, timeout=60)
    claim_log = logs / "claim.log"
    return r, claim_log.read_text() if claim_log.exists() else ""


def test_failed_claim_warns_and_launch_still_succeeds(tmp_path):
    r, claim_log = _launch(tmp_path, claim_rc=1)
    assert claim_log.strip() == f"claim-tool claim worker {NAME}", claim_log  # the claim really ran and failed
    assert r.returncode == 0, r.stdout + r.stderr
    assert WARN in r.stderr, r.stderr
    assert f"LAUNCHED {NAME} on kimi" in r.stdout and "claim=FAILED" in r.stdout, r.stdout


def test_missing_claim_tool_warns(tmp_path):
    r, _ = _launch(tmp_path, claim_tool=tmp_path / "no-such-claim-tool")
    assert r.returncode == 0, r.stdout + r.stderr
    assert WARN in r.stderr, r.stderr
    assert "claim=FAILED" in r.stdout, r.stdout


def test_successful_claim_is_silent_and_reported_ok(tmp_path):
    r, claim_log = _launch(tmp_path, claim_rc=0)
    assert claim_log.strip() == f"claim-tool claim worker {NAME}", claim_log
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN" not in r.stderr, r.stderr
    assert "claim=ok" in r.stdout, r.stdout
