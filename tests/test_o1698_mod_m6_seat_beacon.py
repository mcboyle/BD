"""O1698 M6: the bd-guard seat beacon's writer, hooks/beacon-write.sh, against the real filesystem.

The candidate is a HARNESS plugin (O1045/O1066) under bd-persist/harness-work/FIX: opt in with
BD_TEST_O1698_MOD_M6_SEAT_BEACON=1 (unset, every test here skips); BD_O1698_MOD_M6_SEAT_BEACON_CANDIDATE
names another candidate bd-guard folder. The hook logic (usage() figures, counter, subagent steps) is
proved by the plugin's own hooks/beacon.test.ts (`claude plugin test`); the last test here runs it when a
claude binary is on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

BD_GATE_SCOPE = "module"

OPT_IN = os.environ.get("BD_TEST_O1698_MOD_M6_SEAT_BEACON") == "1"
PLUGIN = os.environ.get(
    "BD_O1698_MOD_M6_SEAT_BEACON_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/o1698-mod-m6-seat-beacon/bd-guard",
)
WRITER = os.path.join(PLUGIN, "hooks", "beacon-write.sh")
pytestmark = pytest.mark.skipif(not OPT_IN, reason="candidate opt-in required (BD_TEST_O1698_MOD_M6_SEAT_BEACON=1)")

RECORD = {
    "seat": "bd-worker-B2", "pool": "B", "five_hour": 42, "seven_day": 61.5,
    "resetsAt": {"five_hour": "2026-10-03T05:00:00Z", "seven_day": "2026-10-07T00:00:00Z"},
    "ctx_pct": 25, "tokens": {"input": 10, "output": 20, "cache_read": 3000, "cache_write": 40},
    "last_turn_utc": "2026-10-03T03:00:00.000Z", "src": "turn.step",
}


def _write(beacon_dir, name="bd-worker-B2.json", record=None, path=None):
    env = {k: v for k, v in os.environ.items() if k != "BD_SEAT"}
    env["LC_ALL"] = "C"
    if path is not None:
        env["PATH"] = path
    text = record if isinstance(record, str) else json.dumps(record or RECORD)
    return subprocess.run(["/bin/sh", WRITER, str(beacon_dir), name, text],
                          capture_output=True, text=True, env=env, timeout=30, check=False)


@pytest.fixture
def fake_nfs(tmp_path):
    """A PATH whose `stat -f` reports nfs, every other tool the real one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stat = bin_dir / "stat"
    stat.write_text('#!/bin/sh\necho nfs\n')
    stat.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


def test_writes_the_record_with_host_and_writer_parent_pid(tmp_path):
    r = _write(tmp_path / "seats")
    assert r.returncode == 0, r.stderr
    beacon = tmp_path / "seats" / "bd-worker-B2.json"
    got = json.loads(beacon.read_text())
    assert got.pop("host") == os.uname().nodename
    assert got.pop("pid") == os.getpid()
    assert got == RECORD
    assert beacon.stat().st_size <= 1024
    assert sorted(p.name for p in (tmp_path / "seats").iterdir()) == ["bd-worker-B2.json"]


def test_replaces_by_rename_never_in_place(tmp_path):
    seats = tmp_path / "seats"
    assert _write(seats).returncode == 0
    before = (seats / "bd-worker-B2.json").stat().st_ino
    assert _write(seats, record={**RECORD, "five_hour": 43}).returncode == 0
    after = seats / "bd-worker-B2.json"
    assert after.stat().st_ino != before
    assert json.loads(after.read_text())["five_hour"] == 43
    assert sorted(p.name for p in seats.iterdir()) == ["bd-worker-B2.json"]


def test_network_filesystem_is_refused_and_the_beacon_untouched(tmp_path, fake_nfs):
    seats = tmp_path / "seats"
    assert _write(seats).returncode == 0
    old = (seats / "bd-worker-B2.json").read_bytes()
    r = _write(seats, record={**RECORD, "five_hour": 99}, path=fake_nfs)
    assert r.returncode == 3 and "local disk only" in r.stderr
    assert (seats / "bd-worker-B2.json").read_bytes() == old


def test_a_record_over_1024_bytes_is_refused_and_the_beacon_untouched(tmp_path):
    seats = tmp_path / "seats"
    assert _write(seats).returncode == 0
    old = (seats / "bd-worker-B2.json").read_bytes()
    r = _write(seats, record={**RECORD, "seat": "x" * 1100})
    assert r.returncode == 4 and "> 1024" in r.stderr
    assert (seats / "bd-worker-B2.json").read_bytes() == old
    assert sorted(p.name for p in seats.iterdir()) == ["bd-worker-B2.json"]


@pytest.mark.parametrize("name", ["../escape.json", "a/b.json", ".hidden.json", ""])
def test_a_name_that_could_leave_the_dir_is_refused(tmp_path, name):
    r = _write(tmp_path / "seats", name=name)
    assert r.returncode == 2 and "bad name" in r.stderr
    assert not (tmp_path / "escape.json").exists()


def test_non_object_json_is_refused(tmp_path):
    r = _write(tmp_path / "seats", record="[1,2]")
    assert r.returncode == 2 and "not an object" in r.stderr


def test_an_unwritable_dir_fails_without_a_temp_file(tmp_path):
    seats = tmp_path / "seats"
    seats.mkdir()
    seats.chmod(0o555)
    try:
        if os.access(seats, os.W_OK):
            pytest.skip("running as a user who can write a 0555 dir")
        r = _write(seats)
        assert r.returncode == 5
        assert list(seats.iterdir()) == []
    finally:
        seats.chmod(0o755)


@pytest.mark.skipif(shutil.which("claude") is None, reason="no claude binary on PATH")
def test_plugin_validates_and_its_own_tests_pass(tmp_path):
    for verb in ("validate", "test"):
        r = subprocess.run(["claude", "plugin", verb, PLUGIN], capture_output=True, text=True,
                           cwd=tmp_path, env={**os.environ, "LC_ALL": "C"}, timeout=100, check=False)
        assert r.returncode == 0, f"claude plugin {verb}: {r.stdout[-2000:]}{r.stderr[-2000:]}"
