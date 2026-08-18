"""Rows 150/151: shared-/tmp janitors isolate a name before destruction."""
import importlib.util
import importlib.machinery
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "module"
sys.path.insert(0, str(ROOT))


def _load(name, path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bd_gc_covers_the_measured_bdcut_family():
    gc = _load("bd_gc_1183", ROOT / "toolchain/bin/bd-gc")
    assert "/tmp/bdcut_" in gc.PREFIXES


def test_bd_gc_finds_plain_and_private_bdcut_entries(tmp_path, monkeypatch):
    gc = _load("bd_gc_scan_1183", ROOT / "toolchain/bin/bd-gc")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bdcut_"),))
    now = 1_000_000.0
    for name in ("bdcut_plain", "bdcut_plain.bdrm-deadbeef"):
        path = tmp_path / name
        path.mkdir()
        os.utime(path, (now - 9999, now - 9999))
    eligible, skipped = gc.scan(now, 60, root=str(tmp_path))
    assert skipped == []
    assert {Path(path).name for path, _ in eligible} == {
        "bdcut_plain", "bdcut_plain.bdrm-deadbeef"}


def test_safe_removal_renames_then_verifies_before_destroy(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    (victim / "payload").write_text("owned")
    seen = []
    real = safe._destroy_private

    def observe(path):
        seen.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(safe, "_destroy_private", observe)
    ok, why = safe.rename_verify_destroy(victim)
    assert ok, why
    assert seen and seen[0].startswith("bdcut_victim.bdrm-")
    assert not victim.exists()


def test_substitution_before_private_rename_is_detected(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    (victim / "owned").write_text("yes")
    moved = tmp_path / "owned-moved"
    real = safe._rename_noreplace
    fired = 0

    def substitute(parent_fd, old, private):
        nonlocal fired
        fired += 1
        os.rename(victim, moved)
        victim.mkdir()
        (victim / "foreign").write_text("keep")
        return real(parent_fd, old, private)

    monkeypatch.setattr(safe, "_rename_noreplace", substitute)
    ok, why = safe.rename_verify_destroy(victim)
    assert fired == 1
    assert not ok and "identity" in why
    assert moved.joinpath("owned").read_text() == "yes"
    private = list(tmp_path.glob("bdcut_victim.bdrm-*"))
    assert len(private) == 1
    assert private[0].joinpath("foreign").read_text() == "keep"


def test_private_rename_never_clobbers_an_existing_entry(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    occupied = tmp_path / "fixed-private"
    occupied.mkdir()
    (occupied / "foreign").write_text("keep")
    monkeypatch.setattr(safe, "_private_name", lambda _name: occupied.name)
    ok, why = safe.rename_verify_destroy(victim)
    assert not ok and "File exists" in why
    assert victim.is_dir()
    assert occupied.joinpath("foreign").read_text() == "keep"


def test_mid_destroy_not_found_is_a_failure_not_success(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    monkeypatch.setattr(
        safe, "_destroy_private",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("mid-destroy")))
    ok, why = safe.rename_verify_destroy(victim)
    assert not ok and "mid-destroy" in why
    assert list(tmp_path.glob("bdcut_victim.bdrm-*"))


def test_private_residue_check_is_load_bearing(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    monkeypatch.setattr(safe, "_destroy_private", lambda _path: None)
    ok, why = safe.rename_verify_destroy(victim)
    assert not ok and "still exists" in why


def test_held_link_count_check_is_load_bearing(tmp_path, monkeypatch):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    victim.mkdir()
    real_fstat = safe.os.fstat
    calls = 0

    def wrong_final_link_count(fd):
        nonlocal calls
        calls += 1
        actual = real_fstat(fd)
        if calls == 1:
            return actual
        return SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino,
                               st_nlink=actual.st_nlink + 1)

    monkeypatch.setattr(safe.os, "fstat", wrong_final_link_count)
    ok, why = safe.rename_verify_destroy(victim)
    assert not ok and "link count" in why


def test_regular_file_hardlink_removes_only_the_named_link(tmp_path):
    from tools import safe_temp_remove as safe

    victim = tmp_path / "bdcut_victim"
    other = tmp_path / "other-link"
    victim.write_text("owned")
    os.link(victim, other)
    ok, why = safe.rename_verify_destroy(victim)
    assert ok, why
    assert not victim.exists()
    assert other.read_text() == "owned"


def test_reaper_does_not_report_failed_removal_as_done(tmp_path, monkeypatch):
    reaper = _load("reaper_1183", ROOT / "tools/reap_orphan_tempdirs.py")
    victim = tmp_path / "bdback_failed"
    victim.mkdir()
    monkeypatch.setattr(reaper, "rename_verify_destroy",
                        lambda _path: (False, "injected failure"), raising=False)
    done, failed = reaper.reap([str(victim)], apply=True)
    assert done == []
    assert failed == [(str(victim), "injected failure")]


def test_reaper_module_loads_in_isolation():
    module = _load("reaper_isolated_1183", ROOT / "tools/reap_orphan_tempdirs.py")
    assert callable(module.rename_verify_destroy)
