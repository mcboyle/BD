"""BH2-14 (findings/BH2-launchers-bd-agy-audit-2.md, HIGH bd-launch-kimi.sh:fail-open-on-dead-session).

The live launcher never checked that the tmux session survived startup: a seat that died (or never reached the
Kimi welcome screen) was still kicked and reported "LAUNCHED ... kick=consumed" with exit 0.

The candidate lives outside the repo (harness-work/FIX/bh2-14-bd-worker-B3-B/bd-launch-kimi.sh) and is opted in
with BD_BH2_14_CANDIDATE. tmux is a PATH stub driven by STUB_MODE; the seats root and the role-claim tool are
env seams pointed into tmp_path, so nothing leaves the host and no live seat, claim or session is touched.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_14_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

ROLE = "adjudicator"  # any role with a prompt under bd-persist/role-prompts (read-only)
NAME = "bd-bh2-14-stubseat"

TMUX_STUB = r"""#!/bin/bash
# tmux stub. State: $STUB_DIR/started exists once new-session ran. Every call is logged.
echo "$*" >> "$STUB_DIR/calls"
case "$1" in
  has-session)
    [ -e "$STUB_DIR/preexisting" ] && exit 0
    [ -e "$STUB_DIR/started" ] || exit 1
    [ "$STUB_MODE" = dead ] && exit 1
    exit 0 ;;
  new-session) touch "$STUB_DIR/started"; exit 0 ;;
  capture-pane)
    case "$STUB_MODE" in
      live) echo "Welcome to Kimi Code" ;;
      *) echo "still loading" ;;
    esac
    exit 0 ;;
  send-keys) exit 0 ;;
esac
exit 0
"""

CLAIM_STUB = """#!/bin/bash
echo "$*" >> "$STUB_DIR/claims"
"""


def _exe(p: Path, body: str) -> None:
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _launch(
    tmp_path: Path, mode: str, tries: int = 3, preexisting: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"BD_BH2_14_CANDIDATE={CANDIDATE} is not a file"
    stub = tmp_path / "stub"
    bindir = stub / "bin"
    bindir.mkdir(parents=True)
    _exe(bindir / "tmux", TMUX_STUB)
    _exe(stub / "claim.sh", CLAIM_STUB)
    if preexisting:
        (stub / "preexisting").touch()
    seats = tmp_path / "seats"
    env = dict(os.environ)
    env.update(
        PATH=f"{bindir}:{env.get('PATH', '/usr/bin:/bin')}",
        STUB_DIR=str(stub),
        STUB_MODE=mode,
        BD_KIMI_SEATS_ROOT=str(seats),
        BD_KIMI_ROLE_CLAIM=str(stub / "claim.sh"),
        BD_KIMI_START_TRIES=str(tries),
    )
    r = subprocess.run(
        ["bash", str(cand), ROLE, "B", NAME],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls_f = stub / "calls"
    calls = calls_f.read_text().splitlines() if calls_f.exists() else []
    return r, calls, stub


def _kicks(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("send-keys") and "BEGIN. SEAT:" in c]


def test_live_session_launches_and_kicks(tmp_path: Path) -> None:
    """Positive control: a healthy session is kicked once and reported LAUNCHED, rc 0."""
    r, calls, stub = _launch(tmp_path, "live")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"LAUNCHED {NAME} on kimi" in r.stdout
    assert len(_kicks(calls)) == 1, calls
    assert (stub / "claims").read_text().split() == ["claim", ROLE, NAME]
    assert (tmp_path / "seats" / NAME / "SYSTEM.md").is_file()


def test_session_dead_after_start_fails_loud(tmp_path: Path) -> None:
    r, calls, _ = _launch(tmp_path, "dead")
    assert r.returncode == 6, r.stdout + r.stderr
    assert f"session {NAME} EXITED during startup" in r.stdout
    assert "LAUNCHED" not in r.stdout
    assert _kicks(calls) == [], calls


def test_never_ready_times_out_nonzero(tmp_path: Path) -> None:
    r, calls, _ = _launch(tmp_path, "hang", tries=2)
    assert r.returncode == 6, r.stdout + r.stderr
    assert f"{NAME} never showed the Kimi welcome in 2 tries" in r.stdout
    assert "still loading" in r.stdout  # the pane tail is carried in the diagnostic
    assert "LAUNCHED" not in r.stdout
    assert _kicks(calls) == [], calls
    assert (
        sum(c.startswith("has-session") for c in calls) >= 3
    )  # duplicate probe + one per try


def test_duplicate_session_refusal_unchanged(tmp_path: Path) -> None:
    """Negative control on the pre-existing guard: rc 4, nothing started, nothing claimed."""
    r, calls, stub = _launch(tmp_path, "live", preexisting=True)
    assert r.returncode == 4, r.stdout + r.stderr
    assert f"duplicate session {NAME}" in r.stdout
    assert not any(c.startswith("new-session") for c in calls)
    assert not (stub / "claims").exists()
