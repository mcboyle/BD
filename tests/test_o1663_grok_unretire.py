"""Tests for O1663: bd-launch-grok.sh launches again (O1468 RETIRED refusal removed), --dry-run is side-effect free,
an empty lib_limits class is refused, and the sibling lib_limits.sh classes grok-worker as a builder."""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1663_GROK_UNRETIRE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

FAKE_TMUX = """#!/bin/bash
echo "$*" >> "$FAKE_TMUX_LOG"
[ "$1" = has-session ] && exit "${FAKE_TMUX_HAS_RC:-1}"
exit 0
"""

FAKE_LIMITS = """bd_ceiling_for() { case "$1" in grok-worker) echo 150000 ;; *) echo "" ;; esac; }
bd_turns_for() { case "$1" in grok-worker) echo 20 ;; *) echo "" ;; esac; }
"""


@pytest.fixture
def run(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), f"candidate not executable: {candidate}"
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "grok-worker.prompt").write_text("ROLE PROMPT grok-worker\n")
    (prompts / "grok-nolimits.prompt").write_text("ROLE PROMPT grok-nolimits\n")
    assert (prompts / "grok-worker.prompt").read_text()
    limits = tmp_path / "lib_limits.sh"
    limits.write_text(FAKE_LIMITS)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "tmux").write_text(FAKE_TMUX)
    (bindir / "tmux").chmod(0o755)
    grok = bindir / "grok"
    grok.write_text("#!/bin/bash\nexit 0\n")
    grok.chmod(0o755)
    seats = tmp_path / "seats"
    log = tmp_path / "tmux.log"

    def _run(*args, has_rc=1, grok_path=None):
        env = dict(
            os.environ,
            PATH=f"{bindir}:{os.environ['PATH']}",
            BD_GROK=str(grok_path or grok),
            BD_ROLE_PROMPTS=str(prompts),
            BD_SEATS_ROOT=str(seats),
            BD_LIB_LIMITS=str(limits),
            FAKE_TMUX_LOG=str(log),
            FAKE_TMUX_HAS_RC=str(has_rc),
        )
        env.pop("BD_GROK_OVERRIDE", None)
        r = subprocess.run(["bash", str(candidate), *args], capture_output=True, text=True, env=env, check=False, timeout=30)
        calls = log.read_text().splitlines() if log.exists() else []
        return r, calls, seats

    return _run


def test_dry_run_passes_without_retired_refusal(run):
    r, calls, seats = run("grok-worker", "C", "bd-grok-worker-t1", "--dry-run")
    assert r.returncode == 0, f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "RETIRED" not in r.stdout + r.stderr
    assert "DRY: bd-launch-grok name=bd-grok-worker-t1 role=grok-worker" in r.stdout
    assert "autocompact=150000 max_turns=20" in r.stdout
    assert not any(c.startswith("new-session") for c in calls), calls
    assert not seats.exists(), "dry-run wrote a seat workdir"


def test_dry_run_as_third_positional_keeps_default_name(run):
    r, calls, seats = run("grok-worker", "C", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "name=bd-grok-worker " in r.stdout
    assert not any(c.startswith("new-session") for c in calls), calls


def test_launch_starts_grok_seat_with_role_prompt(run):
    r, calls, seats = run("grok-worker", "C", "bd-grok-worker-t2")
    assert r.returncode == 0, f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "LAUNCHED bd-grok-worker-t2 on grok" in r.stdout
    new = [c for c in calls if c.startswith("new-session")]
    assert len(new) == 1 and "-s bd-grok-worker-t2" in new[0] and "--always-approve" in new[0], calls
    assert (seats / "bd-grok-worker-t2" / "SYSTEM.md").read_text() == "ROLE PROMPT grok-worker\n"
    assert (seats / "bd-grok-worker-t2" / ".grok" / "config.toml").read_text() == (
        "[context]\nmax_tokens = 150000\n[agent]\nmax_turns = 20\n"
    )


def test_unknown_role_refused_exit_3(run):
    r, calls, seats = run("no-such-role", "C", "bd-x", "--dry-run")
    assert r.returncode == 3, f"rc={r.returncode} stdout={r.stdout!r}"
    assert "bd-launch-grok: unknown role no-such-role" in r.stdout
    assert not seats.exists()


def test_duplicate_session_refused_exit_4(run):
    r, calls, seats = run("grok-worker", "C", "bd-grok-worker-t3", "--dry-run", has_rc=0)
    assert r.returncode == 4, f"rc={r.returncode} stdout={r.stdout!r}"
    assert "bd-launch-grok: duplicate session bd-grok-worker-t3" in r.stdout
    assert not any(c.startswith("new-session") for c in calls), calls


def test_missing_grok_binary_refused_exit_6(run, tmp_path):
    r, calls, seats = run("grok-worker", "C", "bd-grok-worker-t4", "--dry-run", grok_path=tmp_path / "absent-grok")
    assert r.returncode == 6, f"rc={r.returncode} stderr={r.stderr!r}"
    assert "bd-launch-grok: grok binary not executable" in r.stderr
    assert not seats.exists()


def test_role_without_limits_class_refused_exit_3(run):
    r, calls, seats = run("grok-nolimits", "C", "bd-grok-nolimits-t5")
    assert r.returncode == 3, f"rc={r.returncode} stdout={r.stdout!r}"
    assert "bd-launch-grok: no limits class for role grok-nolimits" in r.stdout
    assert not any(c.startswith("new-session") for c in calls), calls
    assert not seats.exists(), "refused launch wrote a seat workdir"


def test_sibling_lib_limits_classes_grok_worker_as_builder():
    lib = Path(CANDIDATE).parent / "lib_limits.sh"
    assert lib.is_file(), f"sibling lib_limits.sh absent: {lib}"
    script = f'. "{lib}"; for r in grok-worker worker grok-nolimits; do echo "$r|$(bd_ceiling_for $r)|$(bd_turns_for $r)"; done'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["grok-worker|150000|20", "worker|150000|20", "grok-nolimits||"], r.stdout
