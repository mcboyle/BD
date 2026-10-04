import configparser
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_W2_M7_FACTS_TIMER_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _unit(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with path.open() as stream:
        parser.read_file(stream)
    return parser


@pytest.fixture
def candidate():
    root = Path(CANDIDATE)
    assert root.is_absolute() and root.is_dir(), "M7 TIMER candidate directory absent"
    for name in ("bd-mcp-facts.service", "bd-mcp-facts.timer"):
        assert (root / name).is_file(), f"M7 TIMER candidate unit absent: {name}"
    return root


@pytest.fixture
def fixture_home(candidate, tmp_path):
    home = tmp_path / "home"
    scripts = home / "bd-persist" / "harness" / "bd-mcp"
    scripts.mkdir(parents=True)
    for name in ("bd-mcp-facts.sh", "facts.py", "server.py"):
        shutil.copy2(candidate / ".pre" / "bd-mcp" / name, scripts / name)
    assert os.access(scripts / "bd-mcp-facts.sh", os.X_OK)
    queue = home / "bd-persist" / "queues" / "O1670-QUEUE.tsv"
    queue.parent.mkdir(parents=True)
    queue.write_text("id\tholder\nopen-row\tWAIT\nclosed-row\tCLOSED\n")
    assert len(queue.read_text().splitlines()) == 3
    cache = home / "bd-persist" / "state" / "mcp-cache"
    cache.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("BD_")}
    env.update(
        HOME=str(home),
        LC_ALL="C",
        BD_MCP_CACHE_DIR=str(cache),
        BD_QUEUE_DIR=str(queue.parent),
        BD_OBJECT_CLAIMS=str(home / "absent-claims.tsv"),
        BD_MCP_FACTS_SERVER=str(scripts / "server.py"),
        BD_MCP_FACTS_PYTHON=os.environ.get("BD_O1698_W2_M7_FACTS_PYTHON", sys.executable),
    )
    return home, queue, cache, env


def _run(unit, fixture_home):
    home, _, _, env = fixture_home
    command = shlex.split(unit["Service"]["ExecStart"].replace("%h", str(home)))
    return subprocess.run(
        [*command, "--no-fetch", "--cut", str(home / "fixture-cut")],
        env=env, cwd=home, capture_output=True, text=True, timeout=30, check=False,
    )


def test_execstart_refreshes_numeric_queue_in_two_cycles(candidate, fixture_home):
    unit = _unit(candidate / "bd-mcp-facts.service")
    _, queue, cache, _ = fixture_home
    facts = cache / "queue-open.json"
    started = time.monotonic()
    first = _run(unit, fixture_home)
    assert first.returncode == 0, f"M7 TIMER ExecStart failed rc={first.returncode}: {first.stderr}"
    assert "FACTS-WRITTEN claims=1 cuts=1 fetch=disabled" in first.stdout
    assert facts.is_file(), "M7 TIMER queue facts missing after ExecStart"
    initial = json.loads(facts.read_text())
    assert type(initial["open"]) is int and initial["open"] == 1
    assert initial["queue"] == "O1670-QUEUE"
    first_mtime = facts.stat().st_mtime_ns
    queue.write_text(queue.read_text() + "new-open-row\t-\n")
    assert len(queue.read_text().splitlines()) == 4
    second = _run(unit, fixture_home)
    assert second.returncode == 0, f"M7 TIMER second ExecStart failed: {second.stderr}"
    updated = json.loads(facts.read_text())
    assert updated["open"] == 2, "M7 TIMER queue row addition must refresh q=2"
    assert facts.stat().st_mtime_ns > first_mtime, "M7 TIMER facts not refreshed"
    assert time.monotonic() - started < 120, "M7 TIMER refresh exceeded two 60s cycles"
    assert updated["utc"].endswith("Z")


def test_base_execstart_is_missing_with_distinct_diagnostic(candidate, fixture_home):
    base = _unit(candidate / ".pre" / "bd-mcp" / "bd-mcp-facts.service")
    home, _, cache, env = fixture_home
    command = shlex.split(base["Service"]["ExecStart"].replace("%h", str(home)))
    assert not Path(command[0]).exists()
    result = subprocess.run(
        ["bash", *command], env=env, cwd=home, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert result.returncode == 127, "M7 TIMER base must fail with missing script rc=127"
    assert "bd-mcp-facts.sh: No such file or directory" in result.stderr
    assert not (cache / "queue-open.json").exists()


@pytest.mark.parametrize("key,value", [("Nice", "19"), ("IOSchedulingClass", "idle")])
def test_service_has_low_priority(candidate, key, value):
    service = _unit(candidate / "bd-mcp-facts.service")["Service"]
    assert service.get(key) == value, f"M7 TIMER {key} must be {value}"
    assert service["Type"] == "oneshot"
    assert service["TimeoutStartSec"] == "90s"


def test_timer_preserves_minute_cadence(candidate):
    timer = _unit(candidate / "bd-mcp-facts.timer")
    assert timer["Timer"]["OnBootSec"] == "60s", "M7 TIMER boot cadence must stay 60s"
    assert timer["Timer"]["OnUnitActiveSec"] == "60s", "M7 TIMER active cadence must stay 60s"
    assert timer["Timer"]["Unit"] == "bd-mcp-facts.service"
    assert timer["Install"]["WantedBy"] == "timers.target"
