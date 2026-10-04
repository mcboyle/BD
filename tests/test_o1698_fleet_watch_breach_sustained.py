import csv
import datetime
import json
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_FLEET_WATCH_BREACH_SUSTAINED_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def watch(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute(), "CANDIDATE-INVALID: absolute path required"
    assert candidate.is_file() and os.access(candidate, os.X_OK), "CANDIDATE-INVALID: absent or non-executable"
    root = tmp_path / "persist"
    home = tmp_path / "home"
    state = root / "state"
    samples = state / "hub-samples"
    samples.mkdir(parents=True)
    (root / "harness").mkdir()
    (root / "inbox/PM").mkdir(parents=True)
    project = home / ".claude-c/projects/-var-tmp-bd-seats-pm"
    project.mkdir(parents=True)
    (project / "fixture.jsonl").write_text("{}\n")
    hosts = root / "hosts"
    hosts.write_text("fixture 192.0.2.1\n")
    for name in ("harness/host-watch-extra", "HOST-STATE.tsv", "ROLE-OCCUPANCY.tsv", "DISPATCH-LEDGER.tsv"):
        (root / name).write_text("# empty fixture population\n")
    (root / "HOST-CAPS.tsv").write_text("fixture 4\n")
    (root / "PM-SEAT").write_text("bd-pm-C\n")
    (root / "PLACEMENTS.tsv").write_text("fixture placement\n")
    scripts = {}
    for name, body in {
        "tcp": "exit 0\n",
        "ssh": "printf '11111111-1111-1111-1111-111111111111 100 0 METRICS 0 4 0 1000\\n'\n",
        "push": 'printf "%s\\n" "$1" >> "$BD_ALERTS_LOG"\n',
        "say": 'printf "%s\\n" "$*" >> "$BD_FIXTURE_WAKE_LOG"\n',
    }.items():
        script = tmp_path / name
        script.write_text("#!/bin/sh\nset -eu\n" + body)
        script.chmod(0o755)
        scripts[name] = str(script)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BD_FLEET_WATCH_") and k not in ("BD_SAY", "BD_ALERTS_LOG")}
    env.update({
        "LC_ALL": "C",
        "BD_FLEET_WATCH_ROOT": str(root),
        "BD_FLEET_WATCH_HOME": str(home),
        "BD_FLEET_WATCH_HOSTS": str(hosts),
        "BD_FLEET_WATCH_TCP": scripts["tcp"],
        "BD_FLEET_WATCH_SSH": scripts["ssh"],
        "BD_FLEET_WATCH_PUSH": scripts["push"],
        "BD_SAY": scripts["say"],
        "BD_ALERTS_LOG": str(root / "alerts.log"),
        "BD_FIXTURE_WAKE_LOG": str(root / "wake.log"),
    })

    def run(values):
        assert values, "FIXTURE-EMPTY: samples required"
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        sample_file = samples / (day + ".tsv")
        with sample_file.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["load1", "iowait_pct", "blocked"])
            writer.writerows(values)
        with sample_file.open() as handle:
            assert len(list(csv.DictReader(handle, delimiter="\t"))) == len(values)
        result = subprocess.run([str(candidate)], env=env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=10, check=False)
        assert result.returncode == 0, f"WATCH-RC={result.returncode}: {result.stderr}"
        digest = (state / "fleet-watch.md").read_text()
        flags = next(line.removeprefix("FLAGS=") for line in digest.splitlines() if line.startswith("FLAGS="))
        assert "COVERAGE=1/1 SEATED=0" in digest, "FIXTURE-HOST: probe did not execute"
        assert "SAMPLES=OK PLACEMENTS=OK PM=FRESH" in digest, "FIXTURE-STATE: unrelated uncertainty"
        alerts = json.loads((state / "fleet-watch.json").read_text())["alerts"]
        pushes = (root / "alerts.log").read_text().splitlines() if (root / "alerts.log").exists() else []
        wakes = (root / "wake.log").read_text().splitlines() if (root / "wake.log").exists() else []
        return flags, alerts, pushes, wakes, (state / "OPERATOR-ALERT").exists()

    return run


QUIET = (0, 0, 0)
BREACHES = [(24, 0, 0), (0, 20.1, 0), (0, 0, 12)]


@pytest.mark.parametrize("breach", BREACHES, ids=["load", "iowait", "blocked"])
def test_single_spike_has_digest_without_alert(watch, breach):
    flags, alerts, pushes, wakes, operator_alert = watch([QUIET] * 10 + [breach] + [QUIET] * 19)
    assert not pushes and not wakes and not alerts and not operator_alert, "SINGLE-SPIKE-ALERT: one sample woke or pushed"
    assert flags == "HUB-SPIKE", "SINGLE-SPIKE-DIGEST: missing spike flag"


@pytest.mark.parametrize("breach", BREACHES, ids=["load", "iowait", "blocked"])
def test_three_consecutive_samples_alert(watch, breach):
    flags, alerts, pushes, wakes, operator_alert = watch([QUIET] * 27 + [breach] * 3)
    assert flags == "HUB-BREACH" and alerts == ["HUB-BREACH"] and operator_alert, "SUSTAINED-BREACH-MISSING"
    assert len(pushes) == len(wakes) == 1, "SUSTAINED-DELIVERY-COUNT"
    assert pushes[0].startswith("HIGH HUB-BREACH ") and "WAKE PUSH HUB-BREACH " in wakes[0]


@pytest.mark.parametrize("count", [5, 6])
def test_scattered_sample_threshold(watch, count):
    values = [QUIET] * 30
    for index in range(count):
        values[index * 5] = BREACHES[index % 3]
    flags, alerts, pushes, wakes, operator_alert = watch(values)
    expected = count == 6
    assert flags == ("HUB-BREACH" if expected else "HUB-SPIKE"), "SCATTERED-THRESHOLD"
    assert alerts == (["HUB-BREACH"] if expected else [])
    assert len(pushes) == len(wakes) == int(expected) and operator_alert == expected


def test_two_consecutive_samples_are_spike(watch):
    flags, alerts, pushes, wakes, operator_alert = watch([BREACHES[0]] * 2 + [QUIET] * 28)
    assert (flags, alerts, pushes, wakes, operator_alert) == ("HUB-SPIKE", [], [], [], False)


def test_consecutive_condition_can_change_metric(watch):
    flags, alerts, pushes, wakes, operator_alert = watch(BREACHES + [QUIET] * 27)
    assert flags == "HUB-BREACH" and alerts == ["HUB-BREACH"] and operator_alert
    assert len(pushes) == len(wakes) == 1


def test_only_last_thirty_samples_count(watch):
    flags, alerts, pushes, wakes, operator_alert = watch([BREACHES[0]] * 6 + [QUIET] * 30)
    assert (flags, alerts, pushes, wakes, operator_alert) == ("NONE", [], [], [], False), "WINDOW-LEAK: old samples alerted"


def test_threshold_boundaries_are_quiet(watch):
    flags, alerts, pushes, wakes, operator_alert = watch([(23.99, 20, 11)] * 30)
    assert (flags, alerts, pushes, wakes, operator_alert) == ("NONE", [], [], [], False), "THRESHOLD-DRIFT"


def test_edge_trigger_held_then_cleared_then_rearmed(watch):
    sustained = [BREACHES[0]] * 3 + [QUIET] * 27
    for repeat in range(2):
        flags, alerts, pushes, wakes, operator_alert = watch(sustained)
        assert flags == "HUB-BREACH" and alerts == ["HUB-BREACH"] and operator_alert
        assert len(pushes) == len(wakes) == 1, f"HELD-REPEAT-ALERT: cycle={repeat}"
    flags, alerts, pushes, wakes, _ = watch([BREACHES[0]] + [QUIET] * 29)
    assert flags == "HUB-SPIKE" and alerts == []
    assert len(pushes) == len(wakes) == 1, "SPIKE-REPEAT-ALERT"
    flags, alerts, pushes, wakes, operator_alert = watch(sustained)
    assert flags == "HUB-BREACH" and alerts == ["HUB-BREACH"] and operator_alert
    assert len(pushes) == len(wakes) == 2, "REARM-ALERT-MISSING"
