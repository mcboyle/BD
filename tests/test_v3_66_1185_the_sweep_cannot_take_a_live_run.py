"""v3.66.1185 -- bd-gc applies the test-artifact retention policy."""
from __future__ import annotations

import argparse
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
GC_PATH = REPO / "toolchain" / "bin" / "bd-gc"
HUNT_PATH = REPO / "toolchain" / "bin" / "bd-wedge-hunt"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _root(tmp_path: Path, name: str, state: str, age_hours: int = 30) -> Path:
    path = tmp_path / name
    path.mkdir()
    st = path.stat()
    stamp = time.time() - age_hours * 3600
    marker = {
        "schema": 1,
        "state": state,
        "pid": 123,
        "host": "test-host",
        "started_at": stamp - 60,
        "updated_at": stamp,
        "root_dev": st.st_dev,
        "root_ino": st.st_ino,
    }
    if state == "KEPT_FOR_FORENSICS":
        marker["exitstatus"] = 1
    elif state == "RECLAIMABLE":
        marker["exitstatus"] = 0
    (path / ".bd-testrun.lock").touch()
    lock_st = (path / ".bd-testrun.lock").stat()
    marker.update(lock_dev=lock_st.st_dev, lock_ino=lock_st.st_ino)
    (path / ".bd-testrun").write_text(json.dumps(marker))
    os.utime(path / ".bd-testrun", (stamp, stamp))
    os.utime(path, (stamp, stamp))
    return path


def test_bd_gc_refuses_a_root_whose_lock_is_held(tmp_path, monkeypatch):
    gc = _load("bd_gc_1185_live", GC_PATH)
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    root = _root(tmp_path, "bd-testrun-live", "RUNNING")
    lock = os.open(root / ".bd-testrun.lock", os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ok, why = gc.is_candidate(root, time.time(), 60)
        assert not ok and "LIVE" in why and "lock" in why
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    ok, why = gc.is_candidate(root, time.time(), 60)
    assert ok and "ABANDONED" in why


def test_bd_gc_keeps_the_newest_forensics_roots_and_drops_the_oldest(
        tmp_path, monkeypatch):
    gc = _load("bd_gc_1185_bound", GC_PATH)
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    monkeypatch.setattr(gc, "FORENSICS_KEEP", 2)
    roots = [
        _root(tmp_path, f"bd-testrun-{i}", "KEPT_FOR_FORENSICS", 30 + i)
        for i in range(3)
    ]
    eligible, skipped = gc.scan(time.time(), 60, root=str(tmp_path))
    assert {Path(path) for path, _ in eligible} == {roots[-1]}
    protected = {Path(path) for path, why in skipped if "newest" in why}
    assert protected == set(roots[:2])


def test_a_reclaimable_clean_root_obeys_the_24_hour_floor(tmp_path, monkeypatch):
    gc = _load("bd_gc_1185_clean", GC_PATH)
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    young = _root(tmp_path, "bd-testrun-young", "RECLAIMABLE", 23)
    old = _root(tmp_path, "bd-testrun-old", "RECLAIMABLE", 25)
    assert gc.is_candidate(young, time.time(), 60)[0] is False
    assert gc.is_candidate(old, time.time(), 60)[0] is True


def test_an_unreadable_marker_is_unknown_and_reported(tmp_path, monkeypatch,
                                                       capsys):
    gc = _load("bd_gc_1185_unknown", GC_PATH)
    # run() scans /tmp by default; a slash-terminated prefix scopes its own
    # base exactly the same way bd-gc's selftest does.
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path) + "/",))
    root = tmp_path / "bd-testrun-broken"
    root.mkdir()
    (root / ".bd-testrun").write_text("{")
    (root / ".bd-testrun.lock").touch()
    os.utime(root, (1, 1))
    ok, why = gc.is_candidate(root, time.time(), 60)
    assert not ok and "UNKNOWN" in why
    args = argparse.Namespace(older_than=60, apply=False, show=5,
                              verbose=False, measure=False)
    assert gc.run(args) == 0
    assert "UNKNOWN=1" in capsys.readouterr().out


def test_a_marker_for_a_different_root_is_unknown(tmp_path, monkeypatch):
    gc = _load("bd_gc_1185_identity", GC_PATH)
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    root = _root(tmp_path, "bd-testrun-wrong-identity", "RECLAIMABLE")
    marker = json.loads((root / ".bd-testrun").read_text())
    marker["root_ino"] += 1
    (root / ".bd-testrun").write_text(json.dumps(marker))
    ok, why = gc.is_candidate(root, time.time(), 60)
    assert not ok and "UNKNOWN" in why and "identity" in why


def test_a_clean_removal_refusal_remains_classifiable(monkeypatch):
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run(
        [sys.executable, "-c",
         "import os, _tmproot\n"
         "root = _tmproot.install()\n"
         "def refuse(*a, **k):\n"
         "    os.unlink(root + '/.bd-testrun')\n"
         "    os.unlink(root + '/.bd-testrun.lock')\n"
         "    return False\n"
         "_tmproot._force_rmtree = refuse\n"
         "print('REMOVED', _tmproot.finish(0))\n"
         "print('ROOT', root)\n"],
        cwd=REPO, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    root = Path(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("ROOT ")))
    try:
        gc = _load("bd_gc_1185_refusal", GC_PATH)
        monkeypatch.setattr(gc, "PREFIXES", (str(root.parent / "bd-testrun-"),))
        ok, why = gc.is_candidate(root, time.time(), 60)
        assert not ok and "RECLAIMABLE" in why and "UNKNOWN" not in why
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_the_bd_runctx_sink_is_never_a_candidate():
    gc = _load("bd_gc_1185_runctx", GC_PATH)
    ok, why = gc.is_candidate("/tmp/bd-runctx", time.time(), 60)
    assert not ok and "run context" in why


def test_capture_and_wedge_hunt_sweep_on_the_way_in(monkeypatch):
    capture = (REPO / "capture.sh").read_text()
    assert 'bd_test_root_gc "$(dirname "$0")" || true' in capture

    hunt = _load("bd_wedge_hunt_1185", HUNT_PATH)
    calls = []
    monkeypatch.setattr(
        hunt, "ssh",
        lambda addr, script, timeout=120: calls.append((addr, script, timeout))
        or subprocess.CompletedProcess([], 0, "", ""))
    result = hunt.sweep_test_roots("192.0.2.1")
    assert result.returncode == 0
    assert len(calls) == 1
    assert "bd-gc --apply" in calls[0][1]
    assert calls[0][2] <= 300
    assert "sweep_test_roots(addr)" in HUNT_PATH.read_text()


def test_bd_gc_selftest_still_passes():
    result = subprocess.run(
        [sys.executable, str(GC_PATH), "--selftest"],
        cwd=REPO, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELFTEST PASS" in result.stdout
