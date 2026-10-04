"""O1672: launch-role adapter routes every provider to its own launcher behind one demand gate.

Runs the candidate operations.py named by BD_O1672_LAUNCH_ADAPTER_POOLS_CANDIDATE against stub
launchers, a scratch POOL_STATE, a fake /proc and a scratch launch stamp. No live seat, no tmux.
The fake /proc/stat advances one fixed step (10% iowait) on each time.sleep, so the O1690 two-sample iowait read moves.
"""
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1672_LAUNCH_ADAPTER_POOLS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

LAUNCHERS = (
    "bd-launch-role.sh",
    "bd-launch-codex-role.sh",
    "bd-launch-kimi.sh",
    "bd-launch-grok.sh",
    "bd-launch-agy.sh",
)
HEADER = (
    "pool\tstate\tsince\tevidence\tpct\tresets_at\tweekly_pct\tfive_hour_remaining_pct\tgemini_five_hour_remaining_pct"
    "\tgemini_weekly_remaining_pct\tclaude_gpt_five_hour_remaining_pct\tclaude_gpt_weekly_remaining_pct\tquota_observed_at\n"
)
CPU_STEP = (90, 0, 0, 0, 10, 0, 0, 0)
CLOCK = f"""import runpy, sys, time
from pathlib import Path
step = {CPU_STEP!r}
stat = Path(sys.argv[1]) / "stat"
sleep = time.sleep
def advance(seconds):
    head, *rest = stat.read_text().splitlines()
    cells = head.split()
    cells[1:9] = [str(int(v) + d) for v, d in zip(cells[1:9], step)]
    stat.write_text("\\n".join([" ".join(cells), *rest]) + "\\n")
    sleep(seconds)
time.sleep = advance
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _row(pool, state="OK"):
    return f"{pool}\t{state}\t2026-10-02T00:00:00Z\tfixture\t1\t-\t0\t-\t-\t-\t100\t100\t-\n"


@pytest.fixture
def env(tmp_path):
    assert os.path.isfile(CANDIDATE) and os.access(CANDIDATE, os.R_OK), f"candidate missing: {CANDIDATE}"
    harness = tmp_path / "harness"
    harness.mkdir()
    log = tmp_path / "launch.log"
    for launcher in LAUNCHERS:
        script = harness / launcher
        script.write_text(f'#!/bin/sh\necho "STUB {launcher} $*" >> "{log}"\necho "STUB {launcher} $*"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    premise = tmp_path / "premise.sh"
    premise.write_text('#!/bin/sh\necho \'{"verdict":"PROCEED","violations":[]}\'\n')
    premise.chmod(premise.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "POOL_STATE.tsv"
    state.write_text(
        HEADER + "".join(_row(p) for p in ("A", "B", "C", "D", "codex", "AGY", "kimi", "grok"))
    )
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1.00 1.00 1.00 1/100 1\n")
    (proc / "stat").write_text("cpu 1000 0 0 8000 100 0 0 0 0 0\ncpu0 1000 0 0 8000 100 0 0 0 0 0\n")
    clock = tmp_path / "clock.py"
    clock.write_text(CLOCK)
    for pid in range(1, 11):
        (proc / str(pid)).mkdir()
    brief = tmp_path / "BRIEF.md"
    brief.write_text("# BRIEF fixture\nTASK: build the adapter fix.\n")
    variables = {
        **os.environ,
        "BD_HARNESS": str(harness),
        "BD_PERSIST": str(tmp_path),
        "BD_PREMISE_HELPER": str(premise),
        "BD_LIMIT_STATE": str(state),
        "BD_PROC_ROOT": str(proc),
        "BD_LAUNCH_STAMP": str(tmp_path / "launch-gate.stamp"),
        "BD_IOWAIT_SAMPLE_S": "0",
    }
    for key in list(variables):
        if key.startswith("BD_LAUNCH_") and key.endswith("_HELPER"):
            del variables[key]
    return {"env": variables, "clock": str(clock), "log": log, "state": state, "proc": proc, "brief": str(brief), "tmp": tmp_path}


def _launch(env, *args):
    return subprocess.run(
        [sys.executable, env["clock"], env["env"]["BD_PROC_ROOT"], CANDIDATE, "bd-launch-role-demand", *args],
        env=env["env"], text=True, capture_output=True, timeout=60,
    )


ROUTES = [
    (("A",), "bd-launch-role.sh worker A"),
    (("B",), "bd-launch-role.sh worker B"),
    (("C",), "bd-launch-role.sh worker C"),
    (("D",), "bd-launch-role.sh worker D"),
    (("codex",), "bd-launch-codex-role.sh worker"),
    (("kimi",), "bd-launch-kimi.sh worker kimi"),
    (("grok",), "bd-launch-grok.sh worker grok"),
    (("agy",), "bd-launch-agy.sh worker bd-worker-agy flash"),
    (("agy-claude", "--model", "claude-sonnet-4-6"), "bd-launch-agy.sh worker bd-worker-agy claude-sonnet-4-6"),
]


@pytest.mark.parametrize("pool_args,expected", ROUTES, ids=[r[0][0] for r in ROUTES])
def test_dry_run_routes_each_provider_to_its_launcher(env, pool_args, expected):
    result = _launch(env, "worker", *pool_args, "--brief", env["brief"], "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN: demand verified" in result.stdout
    assert expected in result.stdout, result.stdout
    assert not env["log"].exists(), "dry-run executed a launcher"
    assert not (env["tmp"] / "launch-gate.stamp").exists(), "dry-run took the launch slot"


def test_pool_d_launch_executes_routed_launcher_with_name(env):
    result = _launch(env, "worker", "D", "--brief", env["brief"], "--name", "bd-worker-D9")
    assert result.returncode == 0, result.stderr
    assert env["log"].read_text().splitlines() == ["STUB bd-launch-role.sh worker D bd-worker-D9"]


@pytest.mark.parametrize("state,shown", [("UNKNOWN", "UNKNOWN"), (None, "absent")])
def test_unknown_or_absent_pool_row_refuses_and_names_pool(env, state, shown):
    rows = [p for p in ("A", "B", "C", "D", "codex", "AGY", "grok")]
    text = HEADER + "".join(_row(p) for p in rows)
    if state:
        text += _row("kimi", state)
    env["state"].write_text(text)
    result = _launch(env, "worker", "kimi", "--brief", env["brief"], "--dry-run")
    assert result.returncode == 2
    assert f"POOL-STATE-UNKNOWN: pool kimi row is {shown}" in result.stderr, result.stderr
    control = _launch(env, "worker", "C", "--brief", env["brief"], "--dry-run")
    assert control.returncode == 0, control.stderr


def test_hub_load_breach_refuses(env):
    (env["proc"] / "loadavg").write_text("35.99 1.00 1.00 1/100 1\n")
    below = _launch(env, "worker", "D", "--brief", env["brief"], "--dry-run")
    assert below.returncode == 0, below.stderr
    (env["proc"] / "loadavg").write_text("36.00 1.00 1.00 1/100 1\n")
    result = _launch(env, "worker", "D", "--brief", env["brief"], "--dry-run")
    assert result.returncode == 2
    assert "HUB-GATE: load1 36.0 >= 36 (O1672)" in result.stderr, result.stderr


def test_hub_process_breach_refuses(env):
    for pid in range(11, 8000):
        (env["proc"] / str(pid)).mkdir()
    below = _launch(env, "worker", "D", "--brief", env["brief"], "--dry-run")
    assert below.returncode == 0, below.stderr
    (env["proc"] / "8000").mkdir()
    result = _launch(env, "worker", "D", "--brief", env["brief"], "--dry-run")
    assert result.returncode == 2
    assert "HUB-GATE: procs 8000 >= 8000 (O1672)" in result.stderr, result.stderr


def test_second_launch_inside_30s_refused(env):
    first = _launch(env, "worker", "C", "--brief", env["brief"])
    assert first.returncode == 0, first.stderr
    second = _launch(env, "worker", "kimi", "--brief", env["brief"])
    assert second.returncode == 2
    assert "LAUNCH-RATE: last fleet launch" in second.stderr, second.stderr
    assert env["log"].read_text().splitlines() == ["STUB bd-launch-role.sh worker C"]
    for stamp in env["tmp"].glob("launch-gate*.stamp"):  # fleet stamp + O1690 per-host stamp
        stamp.write_text(f"{time.time() - 31:.3f}\n")
    third = _launch(env, "worker", "kimi", "--brief", env["brief"])
    assert third.returncode == 0, third.stderr
    assert env["log"].read_text().splitlines()[-1] == "STUB bd-launch-kimi.sh worker kimi"


@pytest.mark.parametrize(
    "pool_args,diagnostic",
    [
        (("agy-claude",), "agy-claude needs --model claude-*|gpt-*"),
        (("agy", "--model", "gpt-5"), "use pool agy-claude"),
        (("D", "--model", "flash"), "--model applies only to agy pools"),
        (("E",), "invalid pool: E"),
    ],
)
def test_bad_provider_arguments_refuse(env, pool_args, diagnostic):
    result = _launch(env, "worker", *pool_args, "--brief", env["brief"], "--dry-run")
    assert result.returncode == 2
    assert diagnostic in result.stderr, result.stderr
