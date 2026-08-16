"""Row 148: bd-cut must publish only output made by the current Vite attempt.

The old implementation erased ``frontend/dist`` with
``shutil.rmtree(..., ignore_errors=True)`` and accepted the first JavaScript
name found afterwards.  A stale content-hashed bundle therefore looked like a
successful new build whenever removal failed.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]
BDCUT = REPO / "toolchain" / "bin" / "bd-cut"


def _load_cut():
    loader = importlib.machinery.SourceFileLoader("bd_cut_row_148", str(BDCUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv
    sys.argv = ["bd-cut", "--plan"]
    try:
        loader.exec_module(module)
    finally:
        sys.argv = saved_argv
    return module


def _work(tmp_path: Path, *, stale: bool = True) -> Path:
    work = tmp_path / "work"
    (work / "frontend" / "node_modules" / ".bin").mkdir(parents=True)
    if stale:
        assets = work / "frontend" / "dist" / "assets"
        assets.mkdir(parents=True)
        (assets / "index-STALE.js").write_bytes(b"stale javascript")
        (assets / "index-STALE.css").write_bytes(b"stale css")
        (work / "frontend" / "dist" / "index.html").write_bytes(b"stale html")
    return work


def _successful_vite(
    module, fired: dict[str, int], *, emit: bool = True, emit_js: bool = True
):
    def fake_run(cmd, cwd=None, **_kwargs):
        fired["vite"] = fired.get("vite", 0) + 1
        assert cmd[:2] == ["node_modules/.bin/vite", "build"]
        assert "--outDir" in cmd
        out = Path(cmd[cmd.index("--outDir") + 1])
        assert out.parent == Path(cwd)
        assert out.is_dir(), "the attempt output must be an owned directory"
        assert list(out.iterdir()) == [], "the attempt output must start empty"
        if emit:
            assets = out / "assets"
            assets.mkdir(parents=True)
            if emit_js:
                (assets / "index-FRESH.js").write_bytes(b"fresh javascript")
            (assets / "index-FRESH.css").write_bytes(b"fresh css")
            (out / "index.html").write_bytes(b"fresh html")
        return subprocess.CompletedProcess(cmd, 0, "vite ok", "")

    module.run = fake_run


def _assert_stale_tree(dist: Path, identity: tuple[int, int]) -> None:
    now = os.lstat(dist)
    assert (now.st_dev, now.st_ino) == identity
    assert (dist / "assets" / "index-STALE.js").read_bytes() == b"stale javascript"
    assert (dist / "assets" / "index-STALE.css").read_bytes() == b"stale css"


def test_complete_dist_removal_failure_cannot_become_build_success(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)

    real_remove = module._remove_owned_dir

    def refuse(path, ident, held_fd=None):
        if Path(path) != dist:
            return real_remove(path, ident, held_fd)
        fired["remove"] = fired.get("remove", 0) + 1
        assert Path(path) == dist
        assert ident == (before.st_dev, before.st_ino)
        assert held_fd is not None
        return False, "[not-proven] injected complete removal failure (EACCES)"

    module._remove_owned_dir = refuse
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._remove_owned_dir = real_remove

    assert exc.value.code == 1
    assert fired == {"vite": 1, "remove": 1}, "every injection must fire"
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    out = capsys.readouterr().out
    assert "frontend/dist removal failed" in out
    assert "EACCES" in out
    assert "built bundle" not in out


def test_partial_removal_that_leaves_stale_hashed_assets_is_not_success(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)
    real_remove = module._remove_owned_dir

    def remove_one_then_refuse(path, ident, held_fd=None):
        if Path(path) != dist:
            return real_remove(path, ident, held_fd)
        fired["partial"] = fired.get("partial", 0) + 1
        assert Path(path) == dist
        assert ident == (before.st_dev, before.st_ino)
        (dist / "index.html").unlink()
        return False, "[not-proven] injected partial removal; stale children remain"

    module._remove_owned_dir = remove_one_then_refuse
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._remove_owned_dir = real_remove

    assert exc.value.code == 1
    assert fired == {"vite": 1, "partial": 1}
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    assert "stale children remain" in capsys.readouterr().out


def test_success_with_no_output_from_this_attempt_rejects_old_js_candidate(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    _successful_vite(module, fired, emit_js=False)

    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    out = capsys.readouterr().out
    assert "current build attempt produced no non-empty regular JavaScript" in out
    assert "index-STALE.js" not in out


def test_vite_failure_preserves_the_previous_dist_and_reports_stderr(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired = {"vite": 0}

    def fail_vite(cmd, **_kwargs):
        fired["vite"] += 1
        return subprocess.CompletedProcess(cmd, 23, "", "synthetic vite failure")

    module.run = fail_vite
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    assert "synthetic vite failure" in capsys.readouterr().out


def test_clean_rebuild_publishes_only_attempt_output_with_new_identity(tmp_path):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)

    result = module.build(str(work))

    after = os.lstat(dist)
    assert fired == {"vite": 1}
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    assert result == ["index-FRESH.js"]
    assert (dist / "index.html").read_bytes() == b"fresh html"
    assert (dist / "assets" / "index-FRESH.js").read_bytes() == b"fresh javascript"
    assert (dist / "assets" / "index-FRESH.css").read_bytes() == b"fresh css"
    assert not (dist / "assets" / "index-STALE.js").exists()
    assert not (dist / "assets" / "index-STALE.css").exists()


def test_a_symlink_at_dist_is_refused_without_touching_its_target(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path, stale=False)
    victim = tmp_path / "victim"
    victim.mkdir()
    payload = victim / "keep.txt"
    payload.write_bytes(b"outside payload")
    dist = work / "frontend" / "dist"
    dist.symlink_to(victim, target_is_directory=True)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)

    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert dist.is_symlink()
    assert payload.read_bytes() == b"outside payload"
    assert "symlink" in capsys.readouterr().out.lower()


def test_concurrent_dist_replacement_is_not_clobbered_at_publish(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired: dict[str, int] = {}
    _successful_vite(module, fired)
    real_publish = module._rename_noclobber
    foreign: dict[str, object] = {}

    def replace_then_refuse(old, new, parent_fd):
        if new != "dist" or not old.startswith("bdcut_build_"):
            return real_publish(old, new, parent_fd)
        fired["publish"] = fired.get("publish", 0) + 1
        assert new == "dist"
        assert old.startswith("bdcut_build_")
        dist.mkdir()
        payload = dist / "foreign.txt"
        payload.write_bytes(b"concurrent owner")
        st = os.lstat(dist)
        foreign["identity"] = (st.st_dev, st.st_ino)
        raise FileExistsError(17, "injected no-clobber refusal", new)

    module._rename_noclobber = replace_then_refuse
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._rename_noclobber = real_publish

    assert exc.value.code == 1
    assert fired == {"vite": 1, "publish": 1}
    now = os.lstat(dist)
    assert (now.st_dev, now.st_ino) == foreign["identity"]
    assert (dist / "foreign.txt").read_bytes() == b"concurrent owner"
    assert "without replacing a concurrent frontend/dist" in capsys.readouterr().out
