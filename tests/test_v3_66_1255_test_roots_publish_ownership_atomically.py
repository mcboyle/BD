"""v3.66.1255 -- public test roots are born with complete ownership metadata."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
GC_PATH = REPO / "toolchain" / "bin" / "bd-gc"
TMPROOT_PATH = REPO / "tests" / "_tmproot.py"

# This is the independent denominator of fallible ownership-publication
# boundaries.  The injected operation must fire exactly once in every arm;
# merely observing that no root was reported would otherwise let a dead
# fixture manufacture green.
_PREPUBLICATION_FAILURE_POINTS = (
    "root-open",
    "root-fstat",
    "lock-open",
    "lock-flock",
    "lock-fstat",
    "marker-open",
    "marker-write",
    "marker-fsync",
    "marker-rename",
    "root-fsync",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FAILURE_CHILD = r"""
import errno
import json
import os
import pathlib
import _tmproot

base = pathlib.Path(os.environ["ROW245_TMP"])
point = os.environ["ROW245_POINT"]
_tmproot.SYSTEM_TMP = base

real_open = os.open
real_fstat = os.fstat
real_flock = _tmproot.fcntl.flock
real_write = os.write
real_fsync = os.fsync
real_rename = os.rename
fd_kind = {}
fired = []

def hit(label):
    if point == label:
        fired.append(label)
        raise OSError(errno.EIO, "row245 injected " + label)

def watched_open(path, flags, *args, **kwargs):
    name = os.path.basename(os.fspath(path))
    if ((flags & os.O_DIRECTORY) and
            (name.startswith("bd-testrun-") or
             name.startswith(".bd-testrun-stage-"))):
        hit("root-open")
        fd = real_open(path, flags, *args, **kwargs)
        fd_kind[fd] = "root"
        return fd
    if name == _tmproot._LOCK_NAME:
        hit("lock-open")
        fd = real_open(path, flags, *args, **kwargs)
        fd_kind[fd] = "lock"
        return fd
    if name.startswith(_tmproot._MARKER_NAME + ".tmp-"):
        hit("marker-open")
        fd = real_open(path, flags, *args, **kwargs)
        fd_kind[fd] = "marker"
        return fd
    return real_open(path, flags, *args, **kwargs)

def watched_fstat(fd):
    kind = fd_kind.get(fd)
    if kind == "root":
        hit("root-fstat")
    elif kind == "lock":
        hit("lock-fstat")
    return real_fstat(fd)

def watched_flock(fd, operation):
    if fd_kind.get(fd) == "lock":
        hit("lock-flock")
    return real_flock(fd, operation)

def watched_write(fd, data):
    if fd_kind.get(fd) == "marker":
        hit("marker-write")
    return real_write(fd, data)

def watched_fsync(fd):
    if fd_kind.get(fd) == "marker":
        hit("marker-fsync")
    elif fd_kind.get(fd) == "root":
        hit("root-fsync")
    return real_fsync(fd)

def watched_rename(old, new, *args, **kwargs):
    if os.fspath(new) == _tmproot._MARKER_NAME:
        hit("marker-rename")
    return real_rename(old, new, *args, **kwargs)

os.open = watched_open
os.fstat = watched_fstat
_tmproot.fcntl.flock = watched_flock
os.write = watched_write
os.fsync = watched_fsync
os.rename = watched_rename

returned = None
install_error = None
try:
    returned = _tmproot.install()
except BaseException as exc:
    install_error = type(exc).__name__ + ": " + str(exc)

public = []
for path in sorted(base.iterdir()):
    if path.name.startswith("bd-testrun-"):
        public.append({
            "name": path.name,
            "marker": (path / _tmproot._MARKER_NAME).is_file(),
            "lock": (path / _tmproot._LOCK_NAME).is_file(),
        })

os.open = real_open
os.fstat = real_fstat
_tmproot.fcntl.flock = real_flock
os.write = real_write
os.fsync = real_fsync
os.rename = real_rename

removed = None
finish_error = None
if _tmproot._ROOT is not None:
    try:
        removed = _tmproot.finish(0)
    except BaseException as exc:
        finish_error = type(exc).__name__ + ": " + str(exc)

print(json.dumps({
    "fired": fired,
    "returned": returned,
    "install_error": install_error,
    "public_before_finish": public,
    "removed": removed,
    "finish_error": finish_error,
    "root_remains": bool(returned and pathlib.Path(returned).exists()),
    "last_reason": _tmproot._LAST_REASON,
}, sort_keys=True))
"""


def _child(script: str, tmp_path: Path, **extra: str):
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO / "tests"),
        ROW245_TMP=str(tmp_path),
        **extra,
    )
    env.pop("KEEP_TEST_TMPDIRS", None)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("failure_point", _PREPUBLICATION_FAILURE_POINTS)
def test_every_prepublication_failure_stays_out_of_the_public_namespace(
        tmp_path, failure_point):
    assert len(_PREPUBLICATION_FAILURE_POINTS) == 10
    result = _child(
        _FAILURE_CHILD, tmp_path, ROW245_POINT=failure_point)
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout.splitlines()[-1])

    assert observation["fired"] == [failure_point], (
        "the requested ownership boundary did not fire exactly once: "
        f"{observation}")
    assert observation["public_before_finish"] == [], (
        f"{failure_point} exposed a public root without both ownership "
        f"objects: {observation['public_before_finish']}")
    assert observation["install_error"] is None
    assert observation["finish_error"] is None
    assert Path(observation["returned"]).name.startswith(
        ".bd-testrun-stage-")

    if failure_point in {"root-open", "root-fstat"}:
        assert observation["removed"] is False
        assert observation["root_remains"] is True
        assert "[no-identity]" in observation["last_reason"]
    else:
        assert observation["removed"] is True
        assert observation["root_remains"] is False


def test_markerless_fixture_is_unknown_once_and_a_published_root_is_not(
        tmp_path, monkeypatch):
    markerless = tmp_path / "bd-testrun-markerless-fixture"
    markerless.mkdir()
    assert markerless.is_dir()
    assert not (markerless / ".bd-testrun").exists()
    assert not (markerless / ".bd-testrun.lock").exists()

    result = _child(
        "import json, os, pathlib, _tmproot\n"
        "_tmproot.SYSTEM_TMP = pathlib.Path(os.environ['ROW245_TMP'])\n"
        "root = _tmproot.install()\n"
        "before = [pathlib.Path(root, name).is_file() for name in "
        "          (_tmproot._MARKER_NAME, _tmproot._LOCK_NAME)]\n"
        "_tmproot.finish(7)\n"
        "print(json.dumps({'root': root, 'before': before}))\n",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    produced = json.loads(result.stdout.splitlines()[-1])
    published = Path(produced["root"])
    assert produced["before"] == [True, True]
    assert published.is_dir()
    assert (published / ".bd-testrun").is_file()
    assert (published / ".bd-testrun.lock").is_file()

    gc = _load("bd_gc_1255_markerless", GC_PATH)
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd-testrun-"),))
    eligible, skipped = gc.scan(
        time.time(), 60, root=str(tmp_path), only_family="bd-testrun")
    classified = eligible + skipped
    assert len(classified) == 2
    assert {Path(path) for path, _why in classified} == {markerless, published}

    unknown = [
        (Path(path), why) for path, why in classified
        if why.startswith("[UNKNOWN]")
    ]
    assert len(unknown) == 1
    assert unknown[0][0] == markerless
    assert "lock cannot be evaluated" in unknown[0][1]
    assert all(path != published for path, _why in unknown)


def test_transform_control_imports_tmproot_without_judging_publication_order():
    module = _load("tmproot_1255_transform_control", TMPROOT_PATH)
    assert callable(module.install)
