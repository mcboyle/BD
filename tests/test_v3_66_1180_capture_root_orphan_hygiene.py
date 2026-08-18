"""v3.66.1180: selftest hygiene is authorized by capture outputs, not the repo."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bulk_downloader import dom_analyzer as da
from bulk_downloader import selftest
from bulk_downloader import app_selftest
import bulk_downloader.app as application
import bulk_downloader.global_config as global_config


def _age(path: Path, *, hours: float = 48.0) -> None:
    stamp = time.time() - hours * 3600.0
    os.utime(path, (stamp, stamp))


def _orphan_row(report: dict) -> dict:
    rows = [row for row in report["checks"] if row["test"] == "orphan_tempfiles"]
    assert len(rows) == 1
    return rows[0]


def _run_with_tight_orphan_cap(monkeypatch: pytest.MonkeyPatch, **kwargs) -> dict:
    real_check = selftest.check_orphan_tempfiles

    def tight_check(*dirs, **check_kwargs):
        return real_check(*dirs, max_entries=5, **check_kwargs)

    monkeypatch.setattr(selftest, "check_orphan_tempfiles", tight_check)
    return selftest.run_all(
        sites_config_path=None,
        db_path=None,
        cookies_dir=str(kwargs.pop("project") / "cookies"),
        download_dirs=[],
        **kwargs,
    )


def test_unset_store_root_never_opens_project_or_venv_and_keeps_nested_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project-root fallback must not authorize recursive hygiene observation."""
    project = tmp_path / "repo"
    venv = project / "venv" / "lib" / "_pytest"
    capture = project / "captures" / "run" / "segments" / "leaked.part"
    venv.mkdir(parents=True)
    capture.parent.mkdir(parents=True)
    capture.write_text("capture", encoding="utf-8")
    _age(capture)
    fleet_residue = venv / "tmpdir.py"
    fleet_residue.write_text("venv", encoding="utf-8")
    _age(fleet_residue)
    for number in range(5):
        residue = venv / f"tmpdir-{number}.py"
        residue.write_text("venv", encoding="utf-8")
        _age(residue)

    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(global_config, "get", lambda key, default=None: default)
    real_scandir = selftest.os.scandir
    opened: list[Path] = []

    def guarded_scandir(path):
        observed = Path(path)
        opened.append(observed)
        if observed == project or observed == project / "venv":
            raise AssertionError(f"repository scale was opened: {observed}")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", guarded_scandir)
    report = _run_with_tight_orphan_cap(
        monkeypatch, project=project, captures_root=str(project)
    )
    orphan = _orphan_row(report)

    assert da.capture_output_dirs() == (
        project / "captures",
        project / "offline_out",
        project / "offline_captures",
    )
    assert orphan["status"] == selftest.WARN
    assert orphan["detail"]["sample"] == [str(capture)]
    assert orphan["detail"]["scanned_entries"] < 5
    assert not orphan["detail"]["incomplete"]
    assert str(fleet_residue) not in orphan["detail"]["sample"]
    assert project not in opened
    assert project / "venv" not in opened
    assert not any(path.is_relative_to(venv) for path in opened)


def test_external_store_opens_only_its_canonical_output_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    store = tmp_path / "capture-store"
    capture = store / "offline_out" / "nested" / "archive.tmp"
    project.mkdir()
    capture.parent.mkdir(parents=True)
    capture.write_text("capture", encoding="utf-8")
    _age(capture)

    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(
        global_config, "get", lambda key, default=None: str(store) if key == "capture_store_root" else default
    )
    real_scandir = selftest.os.scandir
    opened: list[Path] = []

    def guarded_scandir(path):
        observed = Path(path)
        opened.append(observed)
        if observed in (project, store):
            raise AssertionError(f"capture base was opened instead of an output root: {observed}")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", guarded_scandir)
    report = _run_with_tight_orphan_cap(
        monkeypatch, project=project, captures_root=str(store)
    )
    orphan = _orphan_row(report)

    assert da.capture_output_dirs() == (
        store / "captures",
        store / "offline_out",
        store / "offline_captures",
    )
    assert orphan["detail"]["sample"] == [str(capture)]
    assert project not in opened
    assert store not in opened


def test_all_canonical_capture_output_roots_keep_all_five_temp_patterns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    store = tmp_path / "capture-store"
    names = ("video.part", ".tmp-download", "tmp-stage", ".selftest_probe", "chunk.tmp")
    project.mkdir()
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(
        global_config, "get", lambda key, default=None: str(store) if key == "capture_store_root" else default
    )
    expected = []
    for output_root in da.capture_output_dirs():
        for name in names:
            path = output_root / "run" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("capture", encoding="utf-8")
            _age(path)
            expected.append(str(path))

    real_scandir = selftest.os.scandir

    def guarded_scandir(path):
        if Path(path) == store:
            raise AssertionError("capture store base was scanned instead of exact outputs")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", guarded_scandir)
    report = selftest.run_all(
        sites_config_path=None,
        db_path=None,
        cookies_dir=str(project / "cookies"),
        download_dirs=[],
        captures_root=str(store),
    )
    orphan = _orphan_row(report)

    assert orphan["detail"]["count"] == 15
    assert orphan["detail"]["sample"] == sorted(expected)[:10]


@pytest.mark.parametrize("caller", ("startup", "on_demand"))
def test_app_selftest_callers_keep_store_for_disk_but_authorize_only_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str
) -> None:
    """Both service paths reach the same narrow derived hygiene authority."""
    project = tmp_path / "repo"
    store = tmp_path / "capture-store"
    capture = store / "captures" / "nested" / "leaked.part"
    project.mkdir()
    capture.parent.mkdir(parents=True)
    capture.write_text("capture", encoding="utf-8")
    _age(capture)
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(
        global_config, "get", lambda key, default=None: str(store) if key == "capture_store_root" else default
    )
    real_scandir = selftest.os.scandir

    def guarded_scandir(path):
        if Path(path) in (project, store):
            raise AssertionError(f"caller widened hygiene scope to {path}")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", guarded_scandir)
    if caller == "on_demand":
        from flask import Flask

        monkeypatch.setattr(app_selftest, "_app__SITES_CFG_PATH", lambda: None)
        monkeypatch.setattr(app_selftest, "_app__STARTUP_SELFTEST", lambda: {})
        monkeypatch.setattr(app_selftest, "_app_s_cfg", lambda: {})
        monkeypatch.setattr(app_selftest, "_capture_store_root_for_selftest", lambda: store)
        flask = Flask(__name__)
        flask.register_blueprint(app_selftest.selftest_bp)
        report = flask.test_client().get("/api/selftest").get_json()
    else:
        class StopAfterSelftest(Exception):
            pass

        captured: dict[str, dict] = {}
        real_run_all = selftest.run_all

        def stop_after_selftest(**kwargs):
            captured["report"] = real_run_all(**kwargs)
            raise StopAfterSelftest

        monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
        monkeypatch.setattr(application, "_dom_analyzer_capture_store_root", lambda: store)
        monkeypatch.setattr(application._selftest, "run_all", stop_after_selftest)
        with pytest.raises(StopAfterSelftest):
            application.boot_once(force=True)
        report = captured["report"]

    orphan = _orphan_row(report)
    disk = [row for row in report["checks"] if row["test"] == "disk_space"]
    assert any(row["detail"]["path"] == str(store) for row in disk)
    assert orphan["detail"]["sample"] == [str(capture)]


def test_capture_root_derivation_failure_is_visible_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(da, "capture_output_dirs", lambda: (_ for _ in ()).throw(RuntimeError("injected")))

    report = selftest.run_all(
        sites_config_path=None,
        db_path=None,
        cookies_dir=str(tmp_path / "cookies"),
        download_dirs=[],
        captures_root=str(tmp_path / "store"),
    )
    orphan = _orphan_row(report)

    assert orphan["status"] == selftest.WARN
    assert orphan["detail"]["count"] == 0
    assert orphan["detail"]["incomplete"] is True
    assert "capture hygiene roots unavailable" in " ".join(orphan["detail"]["errors"])


def test_canonical_store_symlink_is_refused_without_opening_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    target = tmp_path / "store-target"
    linked_store = tmp_path / "store-link"
    target_capture = target / "captures" / "leaked.part"
    project.mkdir()
    target_capture.parent.mkdir(parents=True)
    target_capture.write_text("capture", encoding="utf-8")
    _age(target_capture)
    linked_store.symlink_to(target, target_is_directory=True)

    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(
        global_config, "get", lambda key, default=None: str(linked_store) if key == "capture_store_root" else default
    )
    real_scandir = selftest.os.scandir

    def guarded_scandir(path):
        if Path(path) == linked_store / "captures":
            raise AssertionError("canonical store symlink was followed")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", guarded_scandir)
    report = _run_with_tight_orphan_cap(
        monkeypatch, project=project, captures_root=str(linked_store)
    )
    orphan = _orphan_row(report)

    assert orphan["status"] == selftest.WARN
    assert orphan["detail"]["count"] == 0
    assert orphan["detail"]["incomplete"] is True
    assert "symlink" in " ".join(orphan["detail"]["errors"])


def test_parent_child_and_duplicate_roots_are_observed_once(tmp_path: Path) -> None:
    parent = tmp_path / "store"
    child = parent / "captures"
    stale = child / "nested" / "leaked.part"
    stale.parent.mkdir(parents=True)
    stale.write_text("capture", encoding="utf-8")
    _age(stale)

    direct = selftest.check_orphan_tempfiles(parent)
    combined = selftest.check_orphan_tempfiles(child, parent, child)

    assert combined["detail"]["sample"] == [str(stale)]
    assert combined["detail"]["scanned_entries"] == direct["detail"]["scanned_entries"]
    assert combined["detail"]["count"] == direct["detail"]["count"] == 1


BD_GATE_SCOPE = "module"
