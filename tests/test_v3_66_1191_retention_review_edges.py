"""v3.66.1191 -- independent retention review edge cases are executable."""
from __future__ import annotations

import errno
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import stat
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
GC_PATH = REPO / "toolchain" / "bin" / "bd-gc"
HUNT_PATH = REPO / "toolchain" / "bin" / "bd-wedge-hunt"
TMPROOT_PATH = REPO / "tests" / "_tmproot.py"
POLICY = REPO / "project-knowledge" / "TEST_ARTIFACT_RETENTION_POLICY.md"


def _load(name: str, path: Path = GC_PATH):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _marked(root: Path, state: str, age_hours: int) -> Path:
    root.mkdir()
    lock = root / ".bd-testrun.lock"
    lock.touch()
    stamp = time.time() - age_hours * 3600
    rst, lst = root.stat(), lock.stat()
    record = {
        "schema": 1,
        "state": state,
        "pid": 123,
        "host": "test-host",
        "started_at": stamp - 60,
        "updated_at": stamp,
        "root_dev": rst.st_dev,
        "root_ino": rst.st_ino,
        "lock_dev": lst.st_dev,
        "lock_ino": lst.st_ino,
    }
    if state == "KEPT_FOR_FORENSICS":
        record["exitstatus"] = 1
    elif state == "RECLAIMABLE":
        record["exitstatus"] = 0
    marker = root / ".bd-testrun"
    marker.write_text(json.dumps(record))
    os.utime(marker, (stamp, stamp))
    os.utime(root, (stamp, stamp))
    return root


def test_young_roots_do_not_spend_the_at_risk_newest_n_floor(
        tmp_path, monkeypatch):
    gc = _load("bd_gc_1187_mixed")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    monkeypatch.setattr(gc, "FORENSICS_KEEP", 2)
    young = [_marked(tmp_path / f"bd-testrun-young-{i}",
                     "KEPT_FOR_FORENSICS", i + 1) for i in range(3)]
    old = [_marked(tmp_path / f"bd-testrun-old-{i}",
                   "KEPT_FOR_FORENSICS", 100 + i) for i in range(2)]
    eligible, skipped = gc.scan(time.time(), 60, root=str(tmp_path))
    assert eligible == []
    assert {Path(path) for path, why in skipped if "newest" in why} == set(old)
    assert all(any(Path(path) == item for path, _why in skipped) for item in young)


@pytest.mark.parametrize("failure_point,err", [("lock", errno.ENOSPC),
                                                ("publish", errno.EMFILE)])
def test_marker_setup_resource_failure_degrades_observably_and_still_cleans(
        failure_point, err):
    script = (
        "import errno, os, pathlib, _tmproot\n"
        f"point={failure_point!r}; code={err}\n"
        "real_open, real_write = os.open, os.write\n"
        "def bad_open(path, *a, **k):\n"
        "  if point == 'lock' and path == '.bd-testrun.lock':\n"
        "    raise OSError(code, os.strerror(code))\n"
        "  return real_open(path, *a, **k)\n"
        "def bad_write(fd, data):\n"
        "  if point == 'publish':\n"
        "    raise OSError(code, os.strerror(code))\n"
        "  return real_write(fd, data)\n"
        "os.open, os.write = bad_open, bad_write\n"
        "root = _tmproot.install()\n"
        "os.open, os.write = real_open, real_write\n"
        "print('ROOT', root)\n"
        "print('REMOVED', _tmproot.finish(0))\n"
        "print('EXISTS', pathlib.Path(root).exists())\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "RETENTION MARKER DEGRADED" in result.stderr
    assert "REMOVED True" in result.stdout and "EXISTS False" in result.stdout


def test_automatic_sweeps_are_scoped_to_the_classified_test_root_family():
    capture_lib = (REPO / "scripts/lib/capture_run_dir.sh").read_text()
    hunt = HUNT_PATH.read_text()
    assert "--only classified" in capture_lib
    assert "--only classified" in hunt


def test_automatic_scope_includes_vaults_but_excludes_mtime_only_families(
        tmp_path, monkeypatch):
    gc = _load("bd_gc_1187_scope")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path) + "/",))
    root = _marked(tmp_path / "bd-testrun-old", "RECLAIMABLE", 30)
    generic = tmp_path / "bdcut_live"
    generic.mkdir()
    os.utime(generic, (1, 1))
    vault = tmp_path / "bd_capture_vault-old"
    vault.mkdir(); (vault / ".bd-capture-vault.lock").touch()
    os.utime(vault, (1, 1))
    eligible, skipped = gc.scan(time.time(), 60, root=str(tmp_path),
                                only_family="classified")
    assert {Path(path) for path, _why in eligible} == {root}
    assert any(Path(path) == vault and "newest" in why
               for path, why in skipped)
    assert all(Path(path) != generic for path, _why in eligible + skipped)


def test_test_roots_and_secret_vaults_have_independent_newest_n_budgets(
        tmp_path, monkeypatch):
    gc = _load("bd_gc_1187_family_budgets")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path) + "/",))
    monkeypatch.setattr(gc, "FORENSICS_KEEP", 2)
    roots = [_marked(tmp_path / f"bd-testrun-{i}",
                     "KEPT_FOR_FORENSICS", 30 + i) for i in range(3)]
    vaults = []
    for index in range(3):
        vault = tmp_path / f"bd_capture_vault-{index}"
        vault.mkdir(); (vault / ".bd-capture-vault.lock").touch()
        stamp = time.time() - (30 + index) * 3600
        os.utime(vault, (stamp, stamp))
        vaults.append(vault)
    eligible, skipped = gc.scan(time.time(), 60, root=str(tmp_path),
                                only_family="classified")
    assert {Path(path) for path, _why in eligible} == {roots[-1], vaults[-1]}
    assert sum("newest" in why for _path, why in skipped) == 4


def test_degraded_marker_setup_removes_the_unheld_lock_name():
    script = (
        "import errno, os, pathlib, _tmproot\n"
        "real_flock = _tmproot.fcntl.flock\n"
        "def fail(fd, op): raise OSError(errno.ENOLCK, 'injected')\n"
        "_tmproot.fcntl.flock = fail\n"
        "root = _tmproot.install()\n"
        "_tmproot.fcntl.flock = real_flock\n"
        "print('ROOT', root)\n"
        "print('LOCK', pathlib.Path(root, '.bd-testrun.lock').exists())\n"
        "_tmproot.finish(0)\n")
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "LOCK False" in result.stdout


def test_interrupted_publish_and_private_removal_residues_are_decidable(
        tmp_path):
    gc = _load("bd_gc_1187_residue")
    lock_only = tmp_path / "bd-testrun-lock-only"
    lock_only.mkdir(); (lock_only / ".bd-testrun.lock").touch()
    os.utime(lock_only, (1, 1))
    assert gc._test_root_state(lock_only, time.time())[0] == "ABANDONED"
    private = tmp_path / "bd-testrun-old.bdrm-0123456789abcdef"
    private.mkdir(); os.utime(private, (1, 1))
    state, stamp, why = gc._test_root_state(private, time.time())
    assert state == "RECLAIMABLE" and stamp == 1
    assert "interrupted object-bound removal" in why


def test_marker_timestamp_cannot_predate_its_published_file(tmp_path):
    gc = _load("bd_gc_1187_time")
    root = _marked(tmp_path / "bd-testrun-impossible", "RECLAIMABLE", 30)
    marker = json.loads((root / ".bd-testrun").read_text())
    marker["started_at"] = marker["updated_at"] = 0
    (root / ".bd-testrun").write_text(json.dumps(marker))
    state, _stamp, why = gc._test_root_state(root, time.time())
    assert state == "UNKNOWN" and "timestamp" in why


def test_terminal_publish_reopens_a_lost_root_descriptor():
    script = (
        "import json, os, _tmproot\n"
        "root = _tmproot.install()\n"
        "os.close(_tmproot._ROOT_FD)\n"
        "_tmproot._ROOT_FD = None; _tmproot._ROOT_FD_PATH = None\n"
        "_tmproot.finish(3)\n"
        "print('ROOT', root)\n"
        "print('STATE', json.load(open(root + '/.bd-testrun'))['state'])\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    root = Path(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("ROOT ")))
    try:
        assert "STATE KEPT_FOR_FORENSICS" in result.stdout
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_clean_refusal_republishes_through_the_reopened_root_descriptor(
        monkeypatch):
    script = (
        "import os, _tmproot\n"
        "root = _tmproot.install()\n"
        "os.close(_tmproot._ROOT_FD)\n"
        "_tmproot._ROOT_FD = None; _tmproot._ROOT_FD_PATH = None\n"
        "def refuse(*a, **k):\n"
        "    os.unlink(root + '/.bd-testrun')\n"
        "    os.unlink(root + '/.bd-testrun.lock')\n"
        "    return False\n"
        "_tmproot._force_rmtree = refuse\n"
        "print('REMOVED', _tmproot.finish(0))\n"
        "print('ROOT', root)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    root = Path(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("ROOT ")))
    try:
        gc = _load("bd_gc_1191_lost_fd_refusal", GC_PATH)
        monkeypatch.setattr(gc, "PREFIXES", (str(root.parent / "bd-testrun-"),))
        ok, why = gc.is_candidate(root, time.time(), 60)
        assert not ok and "RECLAIMABLE" in why and "UNKNOWN" not in why
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_mode_0500_lost_fd_clean_refusal_is_durably_reclaimable(monkeypatch):
    script = (
        "import json, os, stat, _tmproot\n"
        "root = _tmproot.install()\n"
        "os.chmod(root, 0o500)\n"
        "os.close(_tmproot._ROOT_FD)\n"
        "_tmproot._ROOT_FD = None; _tmproot._ROOT_FD_PATH = None\n"
        "def refuse(*a, **k):\n"
        "    os.chmod(root, 0o700)\n"
        "    os.unlink(root + '/.bd-testrun')\n"
        "    os.unlink(root + '/.bd-testrun.lock')\n"
        "    os.chmod(root, 0o500)\n"
        "    return False\n"
        "_tmproot._force_rmtree = refuse\n"
        "print('REMOVED', _tmproot.finish(0))\n"
        "print('ROOT', root)\n"
        "print('MODE', oct(stat.S_IMODE(os.stat(root).st_mode)))\n"
        "print('STATE', json.load(open(root + '/.bd-testrun'))['state'])\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    root_line = next((line for line in result.stdout.splitlines()
                      if line.startswith("ROOT ")), None)
    root = Path(root_line.split(" ", 1)[1]) if root_line else None
    try:
        assert result.returncode == 0, result.stderr
        assert "REMOVED False" in result.stdout
        assert "MODE 0o500" in result.stdout
        assert "STATE RECLAIMABLE" in result.stdout
        gc = _load("bd_gc_1191_mode_0500_refusal", GC_PATH)
        monkeypatch.setattr(gc, "PREFIXES", (str(root.parent / "bd-testrun-"),))
        ok, why = gc.is_candidate(root, time.time(), 60)
        assert not ok and "RECLAIMABLE" in why and "UNKNOWN" not in why
    finally:
        if root is not None and root.exists():
            root.chmod(0o700)
            import shutil
            shutil.rmtree(root)


def test_named_lock_refuses_when_original_mode_cannot_be_restored(
        tmp_path, monkeypatch):
    tmproot = _load("bd_tmproot_1191_mode_restore", TMPROOT_PATH)
    root = tmp_path / "bd-testrun-mode-restore"
    root.mkdir(mode=0o500)
    root.chmod(0o500)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    ident = (root.stat().st_dev, root.stat().st_ino)
    tmproot._RUN_RECORDS[ident] = {}
    real_fchmod = tmproot.os.fchmod
    calls = 0

    def lose_restore(target_fd, mode):
        nonlocal calls
        calls += 1
        if calls == 1:
            real_fchmod(target_fd, mode)

    monkeypatch.setattr(tmproot.os, "fchmod", lose_restore)
    try:
        with pytest.raises(OSError, match="mode restoration did not verify"):
            tmproot._ensure_named_lock(fd, ident)
    finally:
        real_fchmod(fd, 0o700)
        os.close(fd)


def _assert_bd_gc_closes_lock_fd_on_post_open_error(
        tmp_path, monkeypatch, state_fn, lock_name, failure):
    gc = _load("bd_gc_1191_close_%s_%s" %
               (state_fn.strip("_"), failure.replace("-", "_")))
    root = tmp_path / ("bd_capture_vault-case" if "capture" in state_fn
                       else "bd-testrun-case")
    root.mkdir()
    lock = root / lock_name
    if failure == "non-regular":
        lock.mkdir()
    else:
        lock.touch()

    real_open, real_fstat = gc.os.open, gc.os.fstat
    opened = []

    def recording_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if os.fspath(path) == str(lock):
            opened.append(fd)
        return fd

    def injected_fstat(fd):
        if failure == "fstat" and fd in opened:
            raise OSError(errno.EIO, "injected fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(gc.os, "open", recording_open)
    monkeypatch.setattr(gc.os, "fstat", injected_fstat)
    state, _stamp, _why = getattr(gc, state_fn)(root, time.time())
    assert state == "UNKNOWN"
    assert len(opened) == 1
    try:
        with pytest.raises(OSError, match="Bad file descriptor"):
            real_fstat(opened[0])
    finally:
        try:
            os.close(opened[0])
        except OSError:
            pass


def test_bd_gc_closes_capture_lock_fd_when_fstat_fails(tmp_path, monkeypatch):
    _assert_bd_gc_closes_lock_fd_on_post_open_error(
        tmp_path, monkeypatch, "_capture_vault_state",
        ".bd-capture-vault.lock", "fstat")


def test_bd_gc_closes_test_root_lock_fd_when_fstat_fails(tmp_path, monkeypatch):
    _assert_bd_gc_closes_lock_fd_on_post_open_error(
        tmp_path, monkeypatch, "_test_root_state", ".bd-testrun.lock", "fstat")


def test_bd_gc_closes_capture_lock_fd_when_it_is_not_regular(
        tmp_path, monkeypatch):
    _assert_bd_gc_closes_lock_fd_on_post_open_error(
        tmp_path, monkeypatch, "_capture_vault_state",
        ".bd-capture-vault.lock", "non-regular")


def test_bd_gc_closes_test_root_lock_fd_when_it_is_not_regular(
        tmp_path, monkeypatch):
    _assert_bd_gc_closes_lock_fd_on_post_open_error(
        tmp_path, monkeypatch, "_test_root_state", ".bd-testrun.lock",
        "non-regular")


def test_a_skipped_terminal_publish_is_never_silent():
    script = (
        "import _tmproot\n"
        "root = _tmproot.install()\n"
        "_tmproot._RUN_RECORDS.clear()\n"
        "_tmproot.finish(4)\n"
        "print('ROOT', root)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env,
                            text=True, capture_output=True, timeout=30)
    root = Path(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith("ROOT ")))
    try:
        assert result.returncode == 0
        assert "RETENTION MARKER DEGRADED" in result.stderr
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_producer_consumer_and_policy_share_the_exact_state_vocabulary():
    gc = _load("bd_gc_1187_states")
    producer = _load("bd_tmproot_1187_states", TMPROOT_PATH)
    table_states = tuple(
        line.split("`")[1]
        for line in POLICY.read_text().splitlines()
        if line.startswith("| `"))
    assert producer.DURABLE_STATES == gc.DURABLE_STATES == table_states


def test_real_failed_producer_marker_is_consumed_as_kept_forensics():
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    result = subprocess.run(
        [sys.executable, "-c",
         "import _tmproot\n"
         "root = _tmproot.install()\n"
         "_tmproot.finish(5)\n"
         "print(root)\n"],
        cwd=REPO, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    root = Path(result.stdout.strip().splitlines()[-1])
    try:
        gc = _load("bd_gc_1187_real_producer")
        state, _stamp, why = gc._test_root_state(root, time.time())
        assert state == "KEPT_FOR_FORENSICS" and "nonzero" in why
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_interrupted_private_vault_removal_is_reclaimable(tmp_path):
    gc = _load("bd_gc_1189_vault_residue")
    residue = tmp_path / "bd_capture_vault-run.bdrm-0123456789abcdef"
    residue.mkdir(); (residue / "secrets.json").write_text("credential")
    state, _stamp, why = gc._capture_vault_state(residue, time.time())
    assert state == "RECLAIMABLE", why


def test_only_summary_names_its_actual_scope(tmp_path, monkeypatch, capsys):
    gc = _load("bd_gc_1189_scope")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bdcut_"),
                                         str(tmp_path / "bd-testrun-")))
    args = type("Args", (), {"older_than": 120, "only": "classified",
                              "verbose": False, "show": 10,
                              "apply": False})()
    assert gc.run(args) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert "classified" in line
    assert "bdcut_" not in line
