"""O1673: the quota circuit breaker keeps no percentage stops (FLEET_RULE 29: pools run to the provider limit).

Removed: weekly >= 80%, 5-hour >= 80% / headroom <= 20%, weekly pace skew > +5%.
Kept: refusal at the provider limit (state LIMITED/EXHAUSTED, 100% used, 0% remaining) and the launch
refusal when a pool's POOL_STATE row is UNKNOWN or absent.

Loads the candidate operations.py named by BD_O1673_BREAKER_REMOVE_CANDIDATE by path; scratch POOL_STATE,
stub helpers, fake /proc and scratch launch stamp. No live seat, no tmux.
"""
import datetime
import importlib.machinery
import importlib.util
import os
import stat

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1673_BREAKER_REMOVE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

HEADER = "pool\tstate\tsince\tevidence\tpct\tresets_at\tweekly_pct\tfive_hour_remaining_pct\n"


@pytest.fixture(scope="module")
def ops():
    assert os.path.isfile(CANDIDATE), f"candidate missing: {CANDIDATE}"
    loader = importlib.machinery.SourceFileLoader("o1673_candidate_operations", CANDIDATE)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resets(hours_from_now):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours_from_now)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _state(tmp_path, *rows):
    path = tmp_path / "POOL_STATE.tsv"
    path.write_text(HEADER + "".join("\t".join(str(c) for c in r) + "\n" for r in rows))
    return str(path)


def _row(pool, state="OK", pct=1, weekly=1, rem="-", resets=None):
    return (pool, state, "2026-10-02T00:00:00Z", "fixture", pct, resets or _resets(2), weekly, rem)


# Each removed stop, just past its old threshold: must no longer trip.
REMOVED = {
    "weekly_80": dict(weekly=97),
    "five_hour_80": dict(pct=95),
    "headroom_20": dict(rem=5),
    "pace_skew": dict(weekly=60, resets=_resets(24 * 6)),  # ~14% of week elapsed -> skew ~ +46
}


@pytest.mark.parametrize("case", sorted(REMOVED))
def test_removed_percentage_stop_no_longer_trips(ops, tmp_path, case):
    path = _state(tmp_path, _row("D", **REMOVED[case]))
    cb = ops.check_quota_circuit_breaker("D", pool_state_path=path)
    assert cb["tripped"] is False, cb["reasons"]
    assert cb["reasons"] == []
    for key in ("weekly_pct", "utilization_pct", "five_hour_pct", "elapsed_week_pct", "weekly_pace_skew", "state"):
        assert key in cb


LIMIT = {
    "state_limited": (dict(state="LIMITED", pct=100), "LIMITED"),
    "state_exhausted": (dict(state="EXHAUSTED"), "EXHAUSTED"),
    "five_hour_100": (dict(pct=100), "5-hour utilization 100.0%"),
    "weekly_100": (dict(weekly=100), "weekly utilization 100.0%"),
    "headroom_0": (dict(rem=0), "5-hour headroom 0.0%"),
}


@pytest.mark.parametrize("case", sorted(LIMIT))
def test_provider_limit_still_trips(ops, tmp_path, case):
    fields, diagnostic = LIMIT[case]
    path = _state(tmp_path, _row("D", **fields))
    cb = ops.check_quota_circuit_breaker("D", pool_state_path=path)
    assert cb["tripped"] is True
    assert diagnostic in cb["reason"], cb["reason"]


@pytest.fixture
def launch_env(tmp_path, monkeypatch):
    stub = tmp_path / "launch.sh"
    stub.write_text("#!/bin/sh\necho STUB \"$@\"\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    premise = tmp_path / "premise.sh"
    premise.write_text("#!/bin/sh\necho '{\"verdict\":\"PROCEED\",\"violations\":[]}'\n")
    premise.chmod(premise.stat().st_mode | stat.S_IXUSR)
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "loadavg").write_text("1.00 1.00 1.00 1/100 1\n")
    brief = tmp_path / "BRIEF.md"
    brief.write_text("# BRIEF fixture\nTASK: build.\n")
    monkeypatch.setenv("BD_LAUNCH_HELPER", str(stub))
    monkeypatch.setenv("BD_PREMISE_HELPER", str(premise))
    monkeypatch.setenv("BD_PROC_ROOT", str(proc))
    monkeypatch.setenv("BD_LAUNCH_STAMP", str(tmp_path / "launch-gate.stamp"))
    return tmp_path, str(brief), monkeypatch


def test_launch_runs_past_old_weekly_stop(ops, launch_env, capsys):
    tmp_path, brief, mp = launch_env
    mp.setenv("BD_LIMIT_STATE", _state(tmp_path, _row("D", weekly=97)))
    ops.launch(["worker", "D", "--brief", brief, "--dry-run"])
    assert "DRY-RUN: demand verified worker D" in capsys.readouterr().out


@pytest.mark.parametrize("rows,shown", [((("D", "UNKNOWN"),), "UNKNOWN"), ((), "absent")], ids=["unknown", "absent"])
def test_launch_unknown_or_absent_row_still_refuses(ops, launch_env, rows, shown):
    tmp_path, brief, mp = launch_env
    mp.setenv("BD_LIMIT_STATE", _state(tmp_path, _row("C"), *[_row(p, state=s) for p, s in rows]))
    with pytest.raises(ops.Refusal, match=f"POOL-STATE-UNKNOWN: pool D row is {shown}"):
        ops.launch(["worker", "D", "--brief", brief, "--dry-run"])


def test_launch_refuses_worker_at_provider_limit(ops, launch_env):
    tmp_path, brief, mp = launch_env
    mp.setenv("BD_LIMIT_STATE", _state(tmp_path, _row("D", state="LIMITED", pct=100)))
    with pytest.raises(ops.Refusal, match="QUOTA-CIRCUIT-BREAKER: pool D tripped"):
        ops.launch(["worker", "D", "--brief", brief, "--dry-run"])
