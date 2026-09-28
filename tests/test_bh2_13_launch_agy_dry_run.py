"""BH2-13: bd-launch-agy.sh --dry-run must print the plan and exit 0 before any side effect.

Defect (findings/BH2-launchers-bd-agy-audit-2.md#dry-run-launches-live-agent): the launcher had no --dry-run
arm, so `bd-launch-agy.sh worker NAME --dry-run` took "--dry-run" as the MODEL and launched a live seat.

Harness candidate test (bd-harness-cut shape): opt in with
BD_BH2_13_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/bh2-13-bd-worker-A3-A/bd-launch-agy.sh.
Hermetic side effects: `tmux` and `mkdir` are PATH stubs that log every call. A mkdir of the real
role-systemprompts dir is REFUSED by the stub (exit 97), so a launcher that gets past its dry-run point
cannot write the real system-prompt file. HOME and BD_AGY_WORKDIR point into tmp_path.
Reads (not writes) the fleet's lib_limits.sh and role-prompts/, as the launcher does.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_13_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

STUB_TMUX = """#!/bin/bash
echo "tmux $*" >> "$BH2_13_LOG"
case "$1" in has-session) exit 1 ;; esac
exit 0
"""
STUB_MKDIR = """#!/bin/bash
echo "mkdir $*" >> "$BH2_13_LOG"
case "$*" in *role-systemprompts*) echo "BH2-13-STUB: real role-systemprompts write blocked" >&2; exit 97 ;; esac
exec /bin/mkdir "$@"
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), (
        f"BD_BH2_13_CANDIDATE not an executable file: {cand}"
    )
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in (("tmux", STUB_TMUX), ("mkdir", STUB_MKDIR)):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(home),
        "LC_ALL": "C",
        "BD_AGY_WORKDIR": str(tmp_path / "seat"),
        "BH2_13_LOG": str(tmp_path / "calls.log"),
    }


def _run(env: dict[str, str], *args: str) -> tuple[int, str, list[str]]:
    proc = subprocess.run(
        ["bash", CANDIDATE, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    log = Path(env["BH2_13_LOG"])
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


def _no_side_effects(env: dict[str, str], calls: list[str]) -> None:
    assert not [c for c in calls if c.startswith("mkdir")], (
        f"dry-run created a directory: {calls}"
    )
    assert not [
        c for c in calls if c.startswith(("tmux new-session", "tmux send-keys"))
    ], calls
    assert not Path(env["BD_AGY_WORKDIR"]).exists(), "dry-run created the seat workdir"
    assert not (Path(env["HOME"]) / ".gemini").exists(), (
        "dry-run touched trustedFolders.json"
    )


@pytest.mark.parametrize(
    "args",
    [
        ("worker", "bh2-13-probe-seat", "flash", "--dry-run"),
        ("--dry-run", "worker", "bh2-13-probe-seat", "pro", "high"),
        ("worker", "bh2-13-probe-seat", "flash", "--rc", "--dry-run"),
    ],
)
def test_dry_run_prints_plan_and_touches_nothing(
    env: dict[str, str], args: tuple[str, ...]
) -> None:
    rc, out, calls = _run(env, *args)
    assert rc == 0, f"rc={rc} out={out!r}"
    dry = [
        line for line in out.splitlines() if line.startswith("DRY bh2-13-probe-seat ")
    ]
    assert len(dry) == 1, out
    assert "model=--dry-run" not in out
    _no_side_effects(env, calls)


def test_finding_shape_flag_in_model_slot_is_a_usage_error_not_a_launch(
    env: dict[str, str],
) -> None:
    # The finding's exact argv: --dry-run where MODEL goes. Base took it as the model and launched.
    rc, out, calls = _run(env, "worker", "bh2-13-probe-seat", "--dry-run")
    assert rc != 0 and "missing model" in out, f"rc={rc} out={out!r}"
    assert "model=--dry-run" not in out
    _no_side_effects(env, calls)


def test_dry_run_resolves_tiers_like_a_real_launch(env: dict[str, str]) -> None:
    rc, out, _ = _run(env, "worker", "bh2-13-probe-seat", "pro", "--dry-run")
    assert rc == 0, out
    assert (
        "model=gemini-3.1-pro-high" in out
        and "role=worker" in out
        and "prompt=/" in out
    ), out
    rc, out, _ = _run(env, "worker", "bh2-13-probe-seat", "--dry-run", "flash")
    assert rc == 0 and "model=gemini-3.8-flash-high" in out, out


def test_dry_run_still_refuses_an_unknown_role(env: dict[str, str]) -> None:
    rc, out, calls = _run(
        env, "no-such-role-bh2-13", "bh2-13-probe-seat", "flash", "--dry-run"
    )
    assert rc == 3 and "no lib_limits.sh entry" in out, out
    _no_side_effects(env, calls)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("worker", "bh2-13-probe-seat", "--dryrun"), "unknown option --dryrun"),
        (("worker", "bh2-13-probe-seat", "flash", "-n"), "unknown option -n"),
        (
            ("worker", "bh2-13-probe-seat", "flash", "high", "extra"),
            "too many arguments",
        ),
    ],
)
def test_unknown_flag_or_extra_arg_is_refused_not_launched(
    env: dict[str, str], args: tuple[str, ...], needle: str
) -> None:
    rc, out, calls = _run(env, *args)
    assert rc == 2 and needle in out, f"rc={rc} out={out!r}"
    _no_side_effects(env, calls)
