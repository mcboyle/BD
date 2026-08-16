"""Row 149: fleet retention removes the inspected object, never a pathname heir."""

import importlib.machinery
import importlib.util
import os
import pathlib

import pytest


BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-fleet-run"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_fleet_run_1159", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run(base, name, module, payload="owned"):
    path = base / name
    path.mkdir()
    (path / module.SENTINEL).write_text("owned\n", encoding="utf-8")
    (path / "payload").write_text(payload, encoding="utf-8")
    return path


def _open_directory_fds_beneath(root):
    root = str(root.resolve()) + os.sep
    found = {}
    for item in pathlib.Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(item)
        except OSError:
            continue
        if target.startswith(root):
            found[int(item.name)] = target
    return found


@pytest.fixture()
def mod():
    return _load()


def test_success_is_proved_on_the_inspected_directory_descriptor(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    expected = os.lstat(doomed)
    remover = mod._owned_remover_module()
    real_remove = remover._remove_owned_dir
    observed = {"calls": 0, "identity": None, "links": None}

    def recording_remove(path, identity, held_fd):
        observed["calls"] += 1
        held = os.fstat(held_fd)
        observed["identity"] = (held.st_dev, held.st_ino)
        answer = real_remove(path, identity, held_fd)
        observed["links"] = os.fstat(held_fd).st_nlink
        return answer

    monkeypatch.setattr(remover, "_remove_owned_dir", recording_remove)
    dropped, failures = mod.prune(base, 1)

    assert observed["calls"] == 1, "the object-bound removal seam did not fire"
    assert observed["identity"] == (expected.st_dev, expected.st_ino)
    assert observed["links"] == 0, "success was not proved on the held inode"
    assert dropped == [doomed.name]
    assert failures == []


def test_name_replacement_cannot_delete_foreign_or_claim_owned_removal(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    displaced = base / "displaced-owned"
    expected = os.lstat(doomed)
    held_fd = os.open(doomed, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    remover = mod._owned_remover_module()
    real_walk = remover._rmtree_fd
    fired = {"count": 0}

    def replace_before_bound_walk(fd, device, depth=0):
        if depth == 0:
            fired["count"] += 1
            os.rename(doomed, displaced)
            doomed.mkdir()
            (doomed / "foreign").write_text("must survive", encoding="utf-8")
        return real_walk(fd, device, depth)

    monkeypatch.setattr(remover, "_rmtree_fd", replace_before_bound_walk)
    try:
        dropped, failures = mod.prune(base, 1)
        links = os.fstat(held_fd).st_nlink
    finally:
        os.close(held_fd)

    assert fired["count"] == 1, "the replacement injection did not fire"
    assert (expected.st_dev, expected.st_ino) == (
        os.lstat(displaced).st_dev, os.lstat(displaced).st_ino)
    assert links != 0, "the retained owned directory was unexpectedly removed"
    assert (doomed / "foreign").read_text(encoding="utf-8") == "must survive"
    assert doomed.name not in dropped
    assert any(doomed.name in failure and "foreign" in failure.lower()
               for failure in failures), failures


def test_a_remover_success_claim_without_terminal_unlink_is_unknown(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    remover = mod._owned_remover_module()
    fired = {"count": 0}

    def lying_remove(path, identity, held_fd):
        fired["count"] += 1
        assert pathlib.Path(path) == doomed
        assert os.fstat(held_fd).st_nlink != 0
        return True, None

    monkeypatch.setattr(remover, "_remove_owned_dir", lying_remove)
    dropped, failures = mod.prune(base, 1)

    assert fired["count"] == 1, "the false-success injection did not fire"
    assert doomed.is_dir()
    assert doomed.name not in dropped
    assert any(doomed.name in failure and "unlinked" in failure.lower()
               for failure in failures), failures


def test_dangling_symlink_replacement_cannot_become_success(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    displaced = base / "displaced-owned"
    held_fd = os.open(doomed, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    remover = mod._owned_remover_module()
    real_walk = remover._rmtree_fd
    fired = {"count": 0}

    def replace_with_dangling_link(fd, device, depth=0):
        if depth == 0:
            fired["count"] += 1
            os.rename(doomed, displaced)
            os.symlink(base / "absent-target", doomed)
        return real_walk(fd, device, depth)

    monkeypatch.setattr(remover, "_rmtree_fd", replace_with_dangling_link)
    try:
        dropped, failures = mod.prune(base, 1)
        links = os.fstat(held_fd).st_nlink
    finally:
        os.close(held_fd)

    assert fired["count"] == 1, "the dangling-link injection did not fire"
    assert doomed.is_symlink(), "the foreign dangling symlink was mutated"
    assert links != 0, "the renamed owned directory was laundered into success"
    assert doomed.name not in dropped
    assert any("foreign" in failure.lower() for failure in failures), failures


def test_replaced_ownership_sentinel_blocks_destructive_removal(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    real_loader = mod._owned_remover_module
    fired = {"loader": 0, "remove": 0}

    class ForbiddenRemover:
        @staticmethod
        def _remove_owned_dir(path, identity, held_fd):
            fired["remove"] += 1
            return True, None

    def replace_sentinel_after_inspection():
        fired["loader"] += 1
        sentinel = doomed / mod.SENTINEL
        original = doomed / "sentinel-owned-original"
        sentinel.rename(original)
        sentinel.write_text("foreign replacement\n", encoding="utf-8")
        old_st = os.lstat(original)
        new_st = os.lstat(sentinel)
        assert (old_st.st_dev, old_st.st_ino) != (new_st.st_dev, new_st.st_ino), (
            "fixture did not create a distinct sentinel identity")
        assert real_loader() is not None
        return ForbiddenRemover

    monkeypatch.setattr(mod, "_owned_remover_module", replace_sentinel_after_inspection)
    dropped, failures = mod.prune(base, 1)

    assert fired["loader"] == 1, "the post-inspection injection did not fire"
    assert fired["remove"] == 0, "removal ran after ownership proof was replaced"
    assert doomed.is_dir()
    assert doomed.name not in dropped
    assert any("sentinel" in failure.lower() and "changed" in failure.lower()
               for failure in failures), failures


def test_control_exception_propagates_after_owned_descriptor_is_closed(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    _run(base, "20250101T000000Z-bbbbbbbb", mod)
    _run(base, "20240101T000000Z-cccccccc", mod)
    observed = {"calls": 0, "fd": None}

    class InterruptingRemover:
        @staticmethod
        def _remove_owned_dir(path, identity, held_fd):
            observed["calls"] += 1
            observed["fd"] = held_fd
            raise KeyboardInterrupt("injected control exception")

    monkeypatch.setattr(mod, "_owned_remover_module", lambda: InterruptingRemover)
    with pytest.raises(KeyboardInterrupt, match="injected control exception"):
        mod.prune(base, 1)

    assert observed["calls"] == 1, "the control-exception seam did not fire"
    assert observed["fd"] is not None
    with pytest.raises(OSError):
        os.fstat(observed["fd"])
    assert _open_directory_fds_beneath(base) == {}, (
        "a later removal candidate descriptor leaked")


def test_loader_control_exception_closes_every_retained_descriptor(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    _run(base, "20250101T000000Z-bbbbbbbb", mod)
    fired = {"count": 0}

    def interrupt_loader():
        fired["count"] += 1
        assert _open_directory_fds_beneath(base), (
            "fixture did not observe the retained removal descriptor")
        raise KeyboardInterrupt("injected loader exception")

    monkeypatch.setattr(mod, "_owned_remover_module", interrupt_loader)
    with pytest.raises(KeyboardInterrupt, match="injected loader exception"):
        mod.prune(base, 1)

    assert fired["count"] == 1, "the loader exception seam did not fire"
    assert _open_directory_fds_beneath(base) == {}, (
        "loader failure leaked a retained removal descriptor")


def test_descriptor_close_control_exception_is_not_swallowed(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    retained = _run(base, "20260102T000000Z-aaaaaaaa", mod)
    _run(base, "20250101T000000Z-bbbbbbbb", mod)
    real_close = os.close
    fired = {"count": 0}

    def interrupt_after_close(fd):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            target = ""
        if target == str(retained) and fired["count"] == 0:
            fired["count"] += 1
            real_close(fd)
            raise KeyboardInterrupt("injected close exception")
        return real_close(fd)

    monkeypatch.setattr(mod.os, "close", interrupt_after_close)
    with pytest.raises(KeyboardInterrupt, match="injected close exception"):
        mod.prune(base, 1)

    assert fired["count"] == 1, "the close control-exception seam did not fire"
    assert _open_directory_fds_beneath(base) == {}


def test_cleanup_control_exception_takes_precedence_over_primary_control(
        tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    _run(base, "20250101T000000Z-bbbbbbbb", mod)
    pending = _run(base, "20240101T000000Z-cccccccc", mod)
    real_close = os.close
    fired = {"remove": 0, "close": 0}

    class InterruptingRemover:
        @staticmethod
        def _remove_owned_dir(path, identity, held_fd):
            fired["remove"] += 1
            raise KeyboardInterrupt("injected primary remover control")

    def interrupt_pending_close(fd):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            target = ""
        if target == str(pending) and fired["close"] == 0:
            fired["close"] += 1
            real_close(fd)
            raise SystemExit("injected cleanup close control")
        return real_close(fd)

    monkeypatch.setattr(mod, "_owned_remover_module", lambda: InterruptingRemover)
    monkeypatch.setattr(mod.os, "close", interrupt_pending_close)
    with pytest.raises(SystemExit, match="injected cleanup close control") as caught:
        mod.prune(base, 1)

    assert fired == {"remove": 1, "close": 1}, (
        "both primary and cleanup control-exception seams must fire")
    assert isinstance(caught.value.__cause__, KeyboardInterrupt)
    assert "injected primary remover control" in str(caught.value.__cause__)
    assert _open_directory_fds_beneath(base) == {}


def test_ordinary_loader_failure_is_actionable_and_closes_descriptors(tmp_path, mod, monkeypatch):
    base = tmp_path / "runs"
    base.mkdir()
    _run(base, "20260102T000000Z-aaaaaaaa", mod)
    doomed = _run(base, "20250101T000000Z-bbbbbbbb", mod)
    fired = {"count": 0}

    def fail_loader():
        fired["count"] += 1
        assert _open_directory_fds_beneath(base)
        raise OSError(5, "injected remover load failure")

    monkeypatch.setattr(mod, "_owned_remover_module", fail_loader)
    dropped, failures = mod.prune(base, 1)

    assert fired["count"] == 1, "the ordinary loader-failure seam did not fire"
    assert dropped == []
    assert doomed.is_dir()
    assert any(doomed.name in failure and "remover unavailable" in failure
               and "injected remover load failure" in failure
               for failure in failures), failures
    assert _open_directory_fds_beneath(base) == {}
