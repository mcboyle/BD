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
        if "--outDir" in cmd:
            out = Path(cmd[cmd.index("--outDir") + 1])
            assert out.parent == Path(cwd)
            assert out.is_dir(), "the attempt output must be an owned directory"
            assert list(out.iterdir()) == [], "the attempt output must start empty"
        else:
            out = Path(cwd) / "dist"
        if emit:
            assets = out / "assets"
            assets.mkdir(parents=True, exist_ok=True)
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
    real_rmtree = module.shutil.rmtree

    def refuse_old(path, *args, **kwargs):
        fired["remove"] = fired.get("remove", 0) + 1
        assert Path(path) == dist
        return None

    def refuse(path, ident, held_fd=None):
        if Path(path) != dist:
            return real_remove(path, ident, held_fd)
        fired["remove"] = fired.get("remove", 0) + 1
        assert Path(path) == dist
        assert ident == (before.st_dev, before.st_ino)
        assert held_fd is not None
        return False, "[not-proven] injected complete removal failure (EACCES)"

    module._remove_owned_dir = refuse
    module.shutil.rmtree = refuse_old
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._remove_owned_dir = real_remove
        module.shutil.rmtree = real_rmtree

    assert exc.value.code == 1
    assert fired == {"remove": 1}, (
        "the cleanup injection must fire once and Vite must not run afterward")
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
    real_rmtree = module.shutil.rmtree

    def remove_one_old(path, *args, **kwargs):
        fired["partial"] = fired.get("partial", 0) + 1
        assert Path(path) == dist
        (dist / "index.html").unlink()
        return None

    def remove_one_then_refuse(path, ident, held_fd=None):
        if Path(path) != dist:
            return real_remove(path, ident, held_fd)
        fired["partial"] = fired.get("partial", 0) + 1
        assert Path(path) == dist
        assert ident == (before.st_dev, before.st_ino)
        (dist / "index.html").unlink()
        return False, "[not-proven] injected partial removal; stale children remain"

    module._remove_owned_dir = remove_one_then_refuse
    module.shutil.rmtree = remove_one_old
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._remove_owned_dir = real_remove
        module.shutil.rmtree = real_rmtree

    assert exc.value.code == 1
    assert fired == {"partial": 1}, (
        "partial cleanup must be observed before Vite can be invoked")
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    assert "stale children remain" in capsys.readouterr().out


def test_success_with_no_output_from_this_attempt_rejects_old_js_candidate(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired: dict[str, int] = {}
    _successful_vite(module, fired, emit_js=False)

    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert not os.path.lexists(dist)
    out = capsys.readouterr().out
    assert "current build attempt produced no non-empty regular JavaScript" in out
    assert "index-STALE.js" not in out


def test_vite_failure_leaves_dist_absent_and_reports_stderr(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired = {"vite": 0}

    def fail_vite(cmd, **_kwargs):
        fired["vite"] += 1
        return subprocess.CompletedProcess(cmd, 23, "", "synthetic vite failure")

    module.run = fail_vite
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert not os.path.lexists(dist)
    out = capsys.readouterr().out
    assert "after verified frontend/dist removal" in out
    assert "synthetic vite failure" in out


def test_clean_rebuild_publishes_only_attempt_output_with_new_identity(tmp_path):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    removed_fd: dict[str, int] = {}
    _successful_vite(module, fired)
    real_remove = module._remove_owned_dir

    def retain_removed_object(path, ident, held_fd=None):
        assert Path(path) == dist
        assert ident == (before.st_dev, before.st_ino)
        assert held_fd is not None
        removed_fd["fd"] = os.dup(held_fd)
        return real_remove(path, ident, held_fd)

    module._remove_owned_dir = retain_removed_object
    try:
        result = module.build(str(work))
    finally:
        module._remove_owned_dir = real_remove

    try:
        removed = os.fstat(removed_fd["fd"])
        after = os.lstat(dist)
        assert fired == {"vite": 1}
        assert (removed.st_dev, removed.st_ino) == (before.st_dev, before.st_ino)
        assert removed.st_nlink == 0, "the exact old directory must be unlinked"
        assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        assert result == ["index-FRESH.js"]
        assert (dist / "index.html").read_bytes() == b"fresh html"
        assert (dist / "assets" / "index-FRESH.js").read_bytes() == b"fresh javascript"
        assert (dist / "assets" / "index-FRESH.css").read_bytes() == b"fresh css"
        assert not (dist / "assets" / "index-STALE.js").exists()
        assert not (dist / "assets" / "index-STALE.css").exists()
    finally:
        os.close(removed_fd["fd"])


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
    assert fired == {}, "a symlink refusal must prevent Vite authorization"
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

    def replace_then_refuse(old, new, parent_fd, allow_fallback=True):
        if new != "dist" or not old.startswith("bdcut_build_"):
            return real_publish(old, new, parent_fd, allow_fallback)
        fired["publish"] = fired.get("publish", 0) + 1
        assert allow_fallback is False
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


def test_attempt_path_replacement_cannot_publish_foreign_output(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired = {"vite": 0}
    moved: dict[str, Path] = {}

    def swapping_vite(cmd, cwd=None, **_kwargs):
        fired["vite"] += 1
        if "--outDir" not in cmd:
            out = Path(cwd) / "dist"
            (out / "assets").mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_bytes(b"foreign html")
            (out / "assets" / "index-FOREIGN.js").write_bytes(
                b"foreign javascript")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        out = Path(cmd[cmd.index("--outDir") + 1])
        owned = out.with_name(out.name + "-moved")
        out.rename(owned)
        moved["owned"] = owned
        assets = out / "assets"
        assets.mkdir(parents=True)
        (out / "index.html").write_bytes(b"foreign html")
        (assets / "index-FOREIGN.js").write_bytes(b"foreign javascript")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    module.run = swapping_vite
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert moved["owned"].is_dir(), "the creation-bound object must still exist"
    assert not os.path.lexists(dist)
    assert not (dist / "assets" / "index-FOREIGN.js").exists()
    assert "changed identity" in capsys.readouterr().out


def test_publication_rechecks_the_inode_the_kernel_actually_renamed(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    frontend = work / "frontend"
    dist = frontend / "dist"
    fired: dict[str, int] = {}
    moved: dict[str, Path] = {}
    foreign: dict[str, tuple[int, int]] = {}
    _successful_vite(module, fired)
    real_publish = module._rename_noclobber

    def replace_source_then_publish(old, new, parent_fd, allow_fallback=True):
        if new != "dist" or not old.startswith("bdcut_build_"):
            return real_publish(old, new, parent_fd, allow_fallback)
        fired["publish"] = fired.get("publish", 0) + 1
        assert allow_fallback is False
        attempt = frontend / old
        owned = attempt.with_name(attempt.name + ".owned")
        attempt.rename(owned)
        moved["owned"] = owned
        assets = attempt / "assets"
        assets.mkdir(parents=True)
        (attempt / "index.html").write_bytes(b"foreign html")
        (assets / "index-FOREIGN.js").write_bytes(b"foreign javascript")
        st = os.lstat(attempt)
        foreign["identity"] = (st.st_dev, st.st_ino)
        return real_publish(old, new, parent_fd, allow_fallback)

    module._rename_noclobber = replace_source_then_publish
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._rename_noclobber = real_publish

    assert exc.value.code == 1
    assert fired == {"vite": 1, "publish": 1}
    assert moved["owned"].is_dir(), "the verified build inode must be preserved"
    now = os.lstat(dist)
    assert (now.st_dev, now.st_ino) == foreign["identity"]
    assert (dist / "index.html").read_bytes() == b"foreign html"
    assert (dist / "assets" / "index-FOREIGN.js").read_bytes() == b"foreign javascript"
    out = capsys.readouterr().out.lower()
    assert "publication identity" in out
    assert "frontend/dist" in out
    assert "built bundle" not in out


def test_missing_artifact_evidence_is_unknown_not_success(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    fired: dict[str, int] = {}
    _successful_vite(module, fired, emit=False)

    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert "missing or unreadable" in capsys.readouterr().out.lower()


def test_malformed_artifact_evidence_is_not_success(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    fired: dict[str, int] = {}

    def malformed_vite(cmd, cwd=None, **_kwargs):
        fired["vite"] = fired.get("vite", 0) + 1
        out = Path(cmd[cmd.index("--outDir") + 1])
        (out / "index.html").mkdir()
        assets = out / "assets"
        assets.mkdir()
        (assets / "index-MALFORMED.js").write_bytes(b"javascript")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    module.run = malformed_vite
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert "not a non-empty regular file" in capsys.readouterr().out.lower()


def test_unknown_attempt_identity_is_not_success(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)
    real_owned = module._owned_tempdir

    def create_without_identity(*args, **kwargs):
        attempt = real_owned(*args, **kwargs)
        fired["identity_removed"] = fired.get("identity_removed", 0) + 1
        assert module._TEMPDIR_IDENT.pop(attempt, None) is not None
        return attempt

    module._owned_tempdir = create_without_identity
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._owned_tempdir = real_owned

    assert exc.value.code == 1
    assert fired == {"identity_removed": 1, "vite": 1}
    assert "no creation identity" in capsys.readouterr().out.lower()


def test_unreadable_attempt_files_are_not_published(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired: dict[str, int] = {}

    def unreadable_vite(cmd, cwd=None, **_kwargs):
        fired["vite"] = fired.get("vite", 0) + 1
        out = (Path(cmd[cmd.index("--outDir") + 1]) if "--outDir" in cmd
               else Path(cwd) / "dist")
        assets = out / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        index = out / "index.html"
        js = assets / "index-LOCKED.js"
        index.write_bytes(b"html")
        js.write_bytes(b"javascript")
        index.chmod(0)
        js.chmod(0)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    module.run = unreadable_vite
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert not os.path.lexists(dist)
    assert "unreadable" in capsys.readouterr().out.lower()


def test_publish_refuses_when_kernel_no_clobber_is_unavailable(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    fired: dict[str, int] = {}
    _successful_vite(module, fired)
    real_libc = module._LIBC
    module._LIBC = None
    try:
        with pytest.raises(SystemExit) as exc:
            module.build(str(work))
    finally:
        module._LIBC = real_libc

    assert exc.value.code == 1
    assert fired == {"vite": 1}
    assert not dist.exists(), "old dist was removed, but no fallback may publish"
    assert "no-clobber" in capsys.readouterr().out.lower()


def test_dist_on_another_mount_is_refused_before_removal(tmp_path, capsys):
    module = _load_cut()
    work = _work(tmp_path)
    dist = work / "frontend" / "dist"
    before = os.lstat(dist)
    fired: dict[str, int] = {}
    _successful_vite(module, fired)

    def mount_id(fd):
        fired["mount"] = fired.get("mount", 0) + 1
        path = os.readlink(f"/proc/self/fd/{fd}")
        return 202 if path.endswith("/dist") else 101

    module._mount_id = mount_id
    with pytest.raises(SystemExit) as exc:
        module.build(str(work))

    assert exc.value.code == 1
    assert fired == {"mount": 2}, (
        "a mount-boundary refusal must prevent Vite authorization")
    _assert_stale_tree(dist, (before.st_dev, before.st_ino))
    assert "mount" in capsys.readouterr().out.lower()
