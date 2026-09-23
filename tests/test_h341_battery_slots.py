"""Real entry points share host slots, refuse excess work, and release on exit.

Only argument parsing is replaced: its rendezvous stands in for the battery,
so no gates or nested pytest batteries run in these subprocess controls.
"""
import os
import selectors
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"
BIN = Path(__file__).resolve().parents[1] / "toolchain" / "bin"
DRIVER = r'''
import argparse
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

tool, lock, mode = sys.argv[1:]
DRIVER_SOURCE = sys.orig_argv[sys.orig_argv.index("-c") + 1]
loader = importlib.machinery.SourceFileLoader("battery_subject", tool)
spec = importlib.util.spec_from_loader(loader.name, loader)
subject = importlib.util.module_from_spec(spec)
loader.exec_module(subject)
if (Path(tool).parent / "bd_battery.py").exists():
    import bd_battery
    bd_battery.LOCK_BASE = lock

def workload(*args, **kwargs):
    print("ENTERED", flush=True)
    if mode == "nest":
        import subprocess
        nested = subprocess.run(
            [sys.executable, "-c", DRIVER_SOURCE, str(Path(tool).parent / "bd-band"),
             lock, "normal"], input="finish\n", capture_output=True, text=True, timeout=30)
        print("NESTED", nested.returncode, nested.stdout.split("\n")[0], flush=True)
    sys.stdin.readline()
    if mode == "error":
        raise RuntimeError("controlled workload failure")
    raise SystemExit(0)

argparse.ArgumentParser.parse_args = workload
raise SystemExit(subject.main([]))
'''


@pytest.fixture
def launch(tmp_path):
    children = []

    def start(tool, maximum="2", mode="normal", held=None):
        cwd = tmp_path / str(len(children))
        cwd.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "BD_BATTERY_HELD"}
        env["BD_BATTERY_MAX"] = maximum
        env["LC_ALL"] = "C"
        if held is not None:
            env["BD_BATTERY_HELD"] = held
        child = subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(BIN / tool),
             str(tmp_path / "battery.lock"), mode],
            cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        children.append(child)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(15), "entry point produced no result"
        first = child.stdout.readline().strip()
        return child, first

    yield start
    for child in children:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize("maximum", ["1", "2", "3"])
def test_mixed_entry_points_refuse_excess_and_release(launch, maximum):
    holders = []
    for index in range(int(maximum)):
        child, first = launch(("bd-band", "bd-precut")[index % 2], maximum)
        assert first == "ENTERED", first
        holders.append(child)
    for tool in ("bd-band", "bd-precut"):
        child, first = launch(tool, maximum)
        assert "REFUSED-QUEUE" in first, first
        assert child.wait(timeout=10) == 75
    holders[0].communicate(input="finish\n", timeout=10)
    child, first = launch("bd-precut", maximum)
    assert first == "ENTERED", first


@pytest.mark.parametrize("ending", ["kill", "error"])
def test_crash_releases_the_slot(launch, ending):
    holder, first = launch("bd-precut", "1", ending)
    assert first == "ENTERED", first
    if ending == "kill":
        holder.kill()
        holder.communicate(timeout=10)
    else:
        holder.communicate(input="finish\n", timeout=10)
        assert holder.returncode == 1
    _child, first = launch("bd-band", "1")
    assert first == "ENTERED", first


@pytest.mark.parametrize("maximum", ["0", "-1", "invalid", "1.5"])
@pytest.mark.parametrize("tool", ["bd-band", "bd-precut"])
def test_invalid_capacity_refuses_before_work(launch, maximum, tool):
    child, first = launch(tool, maximum)
    assert "REFUSED-QUEUE" in first and "BD_BATTERY_MAX" in first, first
    assert child.wait(timeout=10) == 75


def test_nested_entry_inside_a_held_battery_is_admitted(launch):
    holder, first = launch("bd-precut", "1", "nest")
    assert first == "ENTERED", first
    nested = holder.stdout.readline().split()
    assert nested == ["NESTED", "0", "ENTERED"], nested
    _child, first = launch("bd-band", "1")
    assert "REFUSED-QUEUE" in first, first


@pytest.mark.parametrize("marker", ["dead", "junk", ""])
def test_stale_or_bogus_held_marker_does_not_bypass(launch, marker):
    _holder, first = launch("bd-precut", "1")
    assert first == "ENTERED", first
    if marker == "dead":
        gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True, check=True)
        marker = gone.stdout.strip()
    child, first = launch("bd-band", "1", held=marker)
    assert "REFUSED-QUEUE" in first, first
    assert child.wait(timeout=10) == 75


def test_live_lane_holder_marker_is_admitted(launch):
    holder, first = launch("bd-precut", "1")
    assert first == "ENTERED", first
    _child, first = launch("bd-band", "1", held=str(holder.pid))
    assert first == "ENTERED", first

