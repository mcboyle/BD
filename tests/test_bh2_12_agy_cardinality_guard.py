"""BH2-12 G3 (findings/BH2-launchers-bd-agy-audit-2.md#cardinality-guard-absent,
findings/BH2-launch-agycodex-bd-kimi-audit-2.md#MED-1).

bd-launch-agy.sh had no cardinality guard and recorded no role claim, so an AGY seat could take a SINGLE role another
seat held and stayed invisible to every other launcher's guard. G1/G2 were refuted: G1's guard failed open, and G2's
test sourced only the guard function, so deleting the call site still passed. This test drives the REAL launcher end
to end, so a launcher that never calls the guard, or never records the claim, fails here.

The candidate lives outside the repo (harness-work/FIX/bh2-12-g3-bd-worker-B3-B/bd-launch-agy.sh) and is opted in
with BD_BH2_12_CANDIDATE. tmux and the role-claim tool are stubs; the cardinality table, workdir, system-prompt dir
and HOME (agy's trustedFolders.json) are tmp_path fixtures. Only read-only live files are read (lib_limits.sh,
role-prompts/, FLEET_RULE-FLOOR.md, agy-hooks.json).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_12_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

NAME = "bd-bh2-12-stubseat"
OTHER = "bd-other-seat"

CARDINALITY = "audit\tSINGLE\tx\nworker\tMULTI\tx\nintegrator\t2\tx\n"

TMUX_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/tmux.log"
case "$1" in
  has-session) [ -e "$STUB_DIR/preexisting" ] && exit 0; exit 1 ;;
  capture-pane) printf '? for shortcuts\n● reading FLEET_RULE-FLOOR.md\n' ;;
  list-panes) echo 4242 ;;
esac
exit 0
"""

CLAIM_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/claim.log"
case "$1" in
  who) [ "${STUB_WHO_FAIL:-0}" = 1 ] && exit 2; cat "$STUB_DIR/holders" 2>/dev/null; exit 0 ;;
  claim) exit 0 ;;
esac
exit 0
"""


def _exe(p: Path, body: str) -> None:
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


class Launch:
    def __init__(self, tmp_path: Path) -> None:
        cand = Path(CANDIDATE)
        assert cand.is_file(), f"BD_BH2_12_CANDIDATE={CANDIDATE} is not a file"
        self.cand = cand
        self.stub = tmp_path / "stub"
        bindir = self.stub / "bin"
        bindir.mkdir(parents=True)
        _exe(bindir / "tmux", TMUX_STUB)
        self.claim = self.stub / "bd-role-claim.sh"
        _exe(self.claim, CLAIM_STUB)
        self.cardf = tmp_path / "ROLE-CARDINALITY.tsv"
        self.cardf.write_text(CARDINALITY)
        home = tmp_path / "home"
        (home / ".gemini").mkdir(parents=True)
        self.spdir = tmp_path / "role-systemprompts"
        self.workdir = tmp_path / "seat"
        self.env = dict(os.environ)
        self.env.pop("BD_LAUNCH_ALLOW_DUP", None)
        self.env.update(
            PATH=f"{bindir}:{self.env.get('PATH', '/usr/bin:/bin')}",
            HOME=str(home),
            STUB_DIR=str(self.stub),
            BD_LAUNCH_ROLE_CLAIM=str(self.claim),
            BD_ROLE_CARDINALITY=str(self.cardf),
            BD_AGY_WORKDIR=str(self.workdir),
            BD_AGY_SPDIR=str(self.spdir),
        )

    def holders(self, *seats: str) -> None:
        (self.stub / "holders").write_text("".join(s + "\n" for s in seats))

    def run(self, role: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(
            ["bash", str(self.cand), role, NAME, "flash"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def log(self, name: str) -> list[str]:
        p = self.stub / name
        return p.read_text().splitlines() if p.exists() else []

    def started(self) -> bool:
        return any(c.startswith("new-session") for c in self.log("tmux.log"))

    def claimed(self) -> list[str]:
        return [c for c in self.log("claim.log") if c.startswith("claim ")]


@pytest.fixture
def launch(tmp_path: Path) -> Launch:
    return Launch(tmp_path)


def _refused(launch: Launch, r: subprocess.CompletedProcess[str], diag: str) -> None:
    assert r.returncode == 8, r.stdout + r.stderr
    assert diag in r.stderr, r.stderr
    assert "LAUNCHED" not in r.stdout
    assert not launch.started(), launch.log("tmux.log")
    assert launch.claimed() == []
    assert not launch.workdir.exists()  # refused before any side effect


# ---- refusals: every one must stop the REAL launch path (a launcher that never calls the guard fails all of these)
def test_single_held_by_another_seat_refuses(launch: Launch) -> None:
    launch.holders(OTHER)
    r = launch.run("audit")
    _refused(
        launch,
        r,
        f"audit is SINGLE-HOLDER and is already held by: {OTHER}. Launching {NAME}",
    )


def test_all_lanes_held_refuses(launch: Launch) -> None:
    launch.holders(OTHER, "bd-third-seat")
    r = launch.run("integrator")
    _refused(
        launch, r, f"integrator has 2 lanes and all are held by: {OTHER},bd-third-seat"
    )


def test_role_without_cardinality_refuses(launch: Launch) -> None:
    launch.cardf.write_text("audit\tSINGLE\tx\n")
    r = launch.run("worker")
    _refused(launch, r, "worker has no cardinality in")


def test_absent_claim_tool_fails_closed(launch: Launch) -> None:
    r = launch.run("worker", BD_LAUNCH_ROLE_CLAIM=str(launch.stub / "missing.sh"))
    _refused(launch, r, "WARN cardinality guard SKIPPED")


def test_unreadable_holders_fail_closed(launch: Launch) -> None:
    r = launch.run("audit", STUB_WHO_FAIL="1")
    _refused(launch, r, "guard could not read holders -- refusing")


# ---- permits: the full launch proceeds and the claim is recorded
def test_multi_launches_and_records_the_claim(launch: Launch) -> None:
    r = launch.run("worker")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "claim=ok" in r.stdout and f"LAUNCHED: {NAME}" in r.stdout
    assert launch.started()
    assert launch.claimed() == [f"claim worker {NAME}"]
    assert (launch.spdir / f"{NAME}.systemprompt").is_file()


def test_single_free_launches_and_records_the_claim(launch: Launch) -> None:
    launch.holders(NAME)  # its own row is not a second authority
    r = launch.run("audit")
    assert r.returncode == 0, r.stdout + r.stderr
    assert launch.claimed() == [f"claim audit {NAME}"]


def test_lane_free_passes_the_guard(launch: Launch) -> None:
    """One of two lanes held: the guard permits; a preexisting session then stops the launch at rc 5 (fast)."""
    launch.holders(OTHER)
    (launch.stub / "preexisting").touch()
    r = launch.run("integrator")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "already exists" in r.stdout
    assert launch.claimed() == []


def test_allow_dup_bypasses_loudly(launch: Launch) -> None:
    launch.holders(OTHER)
    (launch.stub / "preexisting").touch()
    r = launch.run("audit", BD_LAUNCH_ALLOW_DUP="1")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "ALLOW_DUP=1 -- guard bypassed for audit" in r.stderr
