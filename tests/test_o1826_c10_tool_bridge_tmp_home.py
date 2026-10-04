"""O1826 C10 (M181) -- bridged tools get a private per-run HOME/cwd, not /tmp.

tool_bridge.run used to hand every tool HOME=/tmp and, when the app data dir was
missing, cwd=/tmp, and it listed "/tmp" itself as an allowed root for path-typed
flags. /tmp is world-writable and shared: a tool reading dotfiles from HOME (yt-dlp
config, cache) reads whatever another local user planted there.

These tests drive the REAL subprocess path with a stand-in allowlist entry
(/usr/bin/env, /bin/pwd), so they measure what the child actually sees.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile

import pytest

from bulk_downloader import tool_bridge as tb

BD_GATE_SCOPE = "module"


def _entry(binary):
    path = shutil.which(binary)
    if not path:
        pytest.skip("%s not on PATH -- child env NOT measured" % binary)
    return {"argv0": path, "desc": "test stand-in", "flags": {}}


@pytest.fixture
def app_home(monkeypatch, tmp_path):
    home = tmp_path / "bd-home"
    home.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    return home


def _child_env(monkeypatch):
    monkeypatch.setitem(tb.ALLOWLIST, "o1826-env", _entry("env"))
    r = tb.run("o1826-env", {})
    assert r["returncode"] == 0, r
    return dict(line.split("=", 1) for line in r["stdout"].splitlines() if "=" in line)


def _child_cwd(monkeypatch):
    monkeypatch.setitem(tb.ALLOWLIST, "o1826-pwd", _entry("pwd"))
    r = tb.run("o1826-pwd", {})
    assert r["returncode"] == 0, r
    return r["stdout"].strip()


def test_child_home_and_cwd_are_not_tmp(monkeypatch, app_home):
    home = _child_env(monkeypatch)["HOME"]
    assert home != "/tmp", "O1826-C10: bridged tool still gets HOME=/tmp"
    assert os.path.dirname(os.path.realpath(home)) == os.path.realpath(app_home), (
        "O1826-C10: private HOME %s is not under the app data dir %s" % (home, app_home))
    cwd = _child_cwd(monkeypatch)
    assert cwd != "/tmp" and os.path.dirname(cwd) == os.path.realpath(app_home), (
        "O1826-C10: bridged tool cwd %s is not a private dir under %s" % (cwd, app_home))


def test_missing_app_dir_falls_back_to_tmpdir_not_bare_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("BD_HOME", str(tmp_path / "absent"))
    tdir = tmp_path / "tmpdir"
    tdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(tdir))
    monkeypatch.setattr(tempfile, "tempdir", None)  # gettempdir() caches; re-read TMPDIR
    cwd = _child_cwd(monkeypatch)
    assert cwd != "/tmp", "O1826-C10: missing app dir still runs the tool in /tmp"
    assert os.path.dirname(cwd) == os.path.realpath(tdir), (
        "O1826-C10: fallback scratch %s does not honour TMPDIR=%s" % (cwd, tdir))


def test_private_home_is_0700_owned_by_us_and_removed(monkeypatch, app_home):
    seen = {}
    real_run = tb.subprocess.run

    def spy(argv, **kw):
        st = os.stat(kw["cwd"])
        seen.update(cwd=kw["cwd"], home=kw["env"]["HOME"],
                    mode=stat.S_IMODE(st.st_mode), uid=st.st_uid)
        return real_run(argv, **kw)

    monkeypatch.setitem(tb.ALLOWLIST, "o1826-env", _entry("env"))
    monkeypatch.setattr(tb.subprocess, "run", spy)
    tb.run("o1826-env", {})
    assert seen["home"] == seen["cwd"], seen
    assert seen["mode"] == 0o700, "O1826-C10: scratch HOME mode %o, want 700" % seen["mode"]
    assert seen["uid"] == os.getuid(), seen
    assert not os.path.exists(seen["cwd"]), "O1826-C10: per-run scratch dir left behind"


def test_allowed_root_is_the_app_dir_not_tmp(monkeypatch, app_home):
    """NEG control: the containment check still ACCEPTS a path under the new root
    and REFUSES one under bare /tmp (no longer an allowed root)."""
    spec = {"type": "path", "positional": True}
    inside = app_home / "media" / "clip.mkv"
    assert tb._validate_value("input", spec, str(inside)) == os.path.realpath(inside)
    with pytest.raises(tb.BridgeError, match="outside the allowed roots"):
        tb._validate_value("input", spec, "/tmp/o1826-c10-outside/clip.mkv")


def _path_tool(monkeypatch):
    entry = _entry("echo")
    entry["flags"] = {"input": {"type": "path", "positional": True}}
    monkeypatch.setitem(tb.ALLOWLIST, "o1826-echo", entry)
    return "o1826-echo"


@pytest.fixture
def no_app_home_shared_tmp(monkeypatch, tmp_path):
    """E1 shape: app data dir absent and tempfile's default parent IS the shared /tmp."""
    monkeypatch.setenv("BD_HOME", str(tmp_path / "absent"))
    monkeypatch.setattr(tempfile, "tempdir", "/tmp")
    assert tempfile.gettempdir() == "/tmp"


def test_missing_app_dir_still_refuses_unrelated_shared_tmp_input(
        monkeypatch, no_app_home_shared_tmp):
    """O1851 (1): with no app dir the fallback root is the run's PRIVATE scratch,
    never gettempdir() itself -- an unrelated /tmp input is refused."""
    tool = _path_tool(monkeypatch)
    with pytest.raises(tb.BridgeError, match="outside the allowed roots"):
        tb.run(tool, {"input": "/tmp/o1826-c10-unrelated-synthetic/clip.mkv"})


def test_missing_app_dir_accepts_input_inside_the_private_scratch(
        monkeypatch, no_app_home_shared_tmp):
    tool = _path_tool(monkeypatch)
    real_build = tb.build_argv
    seen = {}

    def spy(tool_, flags, scratch=None):
        assert scratch, "O1826-C10: path validation ran without the run's private scratch"
        seen["scratch"] = scratch
        return real_build(tool_, {"input": os.path.join(scratch, "in.mkv")}, scratch)

    monkeypatch.setattr(tb, "build_argv", spy)
    r = tb.run(tool, {})
    assert r["returncode"] == 0, r
    assert os.path.dirname(os.path.realpath(seen["scratch"])) == "/tmp", seen
    assert os.path.realpath(os.path.join(seen["scratch"], "in.mkv")) in r["stdout"], r
