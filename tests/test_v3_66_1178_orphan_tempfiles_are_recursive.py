"""v3.66.1178: orphan tempfile hygiene covers nested capture/download trees."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bulk_downloader import selftest


def _age(path: Path, *, hours: float) -> None:
    stamp = time.time() - hours * 3600.0
    os.utime(path, (stamp, stamp))


@pytest.mark.parametrize(
    ("pattern", "name"),
    [
        ("*.part", "video.part"),
        (".tmp*", ".tmp-download"),
        ("tmp*", "tmp-stage"),
        (".selftest_*", ".selftest_probe"),
        ("*.tmp", "chunk.tmp"),
    ],
)
def test_every_nested_temp_shape_is_reported_without_flagging_recent_sibling(
    tmp_path: Path, pattern: str, name: str
) -> None:
    assert selftest._ORPHAN_TEMP_GLOBS == (
        "*.part",
        ".tmp*",
        "tmp*",
        ".selftest_*",
        "*.tmp",
    )
    nested = tmp_path / "run" / "segments"
    nested.mkdir(parents=True)
    stale = nested / name
    recent = nested / ("current" + name)
    stale.write_bytes(b"old")
    recent.write_bytes(b"active")
    _age(stale, hours=48)

    result = selftest.check_orphan_tempfiles(tmp_path, max_age_hours=24)

    assert result["status"] == selftest.WARN
    assert result["detail"]["count"] == 1
    assert result["detail"]["sample"] == [str(stale)]
    assert str(recent) not in result["detail"]["sample"]


def test_recursive_scan_stays_inside_root_when_a_directory_symlink_points_out(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    stale = outside / "escaped.part"
    stale.write_bytes(b"outside")
    _age(stale, hours=48)
    (root / "linked").symlink_to(outside, target_is_directory=True)

    result = selftest.check_orphan_tempfiles(root, max_age_hours=24)

    assert result["status"] == selftest.OK
    assert result["detail"]["count"] == 0


def test_matching_file_symlink_does_not_observe_target_metadata(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    stale = outside / "target"
    stale.write_bytes(b"outside")
    _age(stale, hours=48)
    (root / "escaped.part").symlink_to(stale)

    result = selftest.check_orphan_tempfiles(root, max_age_hours=24)

    assert result["status"] == selftest.OK
    assert result["detail"]["count"] == 0


def test_symlink_supplied_as_root_is_incomplete_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    stale = outside / "escaped.part"
    stale.write_bytes(b"outside")
    _age(stale, hours=48)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)

    result = selftest.check_orphan_tempfiles(linked_root, max_age_hours=24)

    assert result["status"] == selftest.WARN
    assert result["detail"]["count"] == 0
    assert result["detail"]["incomplete"] is True
    assert "configured root is a symlink" in " ".join(result["detail"]["errors"])


def test_scan_error_and_resource_caps_cannot_be_reported_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    real_scandir = selftest.os.scandir

    def raising_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("injected unreadable descendant")
        return real_scandir(path)

    monkeypatch.setattr(selftest.os, "scandir", raising_scandir)
    errored = selftest.check_orphan_tempfiles(tmp_path)
    assert errored["status"] == selftest.WARN
    assert errored["detail"]["incomplete"] is True
    assert "PermissionError" in " ".join(errored["detail"]["errors"])

    monkeypatch.setattr(selftest.os, "scandir", real_scandir)
    (tmp_path / "one.txt").write_text("1")
    (tmp_path / "two.txt").write_text("2")
    capped = selftest.check_orphan_tempfiles(tmp_path, max_entries=1)
    assert capped["status"] == selftest.WARN
    assert capped["detail"]["incomplete"] is True
    assert "entry limit 1" in " ".join(capped["detail"]["errors"])


def test_entry_limit_is_enforced_without_materializing_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(3):
        (tmp_path / f"ordinary-{number}.txt").write_text(str(number))
    real_scandir = selftest.os.scandir
    consumed = 0

    class GuardedIterator:
        def __init__(self, path: Path):
            self.inner = real_scandir(path)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal consumed
            consumed += 1
            if consumed > 2:
                raise AssertionError("scanner consumed beyond max_entries + 1")
            return next(self.inner)

    monkeypatch.setattr(selftest.os, "scandir", GuardedIterator)

    result = selftest.check_orphan_tempfiles(tmp_path, max_entries=1)

    assert result["status"] == selftest.WARN
    assert result["detail"]["incomplete"] is True
    assert consumed == 2


def test_lazy_iteration_error_is_structured_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "first.txt").write_text("1")
    real_scandir = selftest.os.scandir

    class FailingIterator:
        def __init__(self, path: Path):
            self.inner = real_scandir(path)
            self.seen = False

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            if self.seen:
                raise PermissionError("injected mid-iteration failure")
            self.seen = True
            return next(self.inner)

    monkeypatch.setattr(selftest.os, "scandir", FailingIterator)

    result = selftest.check_orphan_tempfiles(tmp_path)

    assert result["status"] == selftest.WARN
    assert result["detail"]["incomplete"] is True
    assert "cannot iterate" in " ".join(result["detail"]["errors"])
    assert "PermissionError" in " ".join(result["detail"]["errors"])


def test_root_identity_error_is_not_laundered_into_missing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lstat = Path.lstat

    def failing_lstat(path: Path):
        if path == tmp_path:
            raise PermissionError("injected root identity failure")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", failing_lstat)

    result = selftest.check_orphan_tempfiles(tmp_path)

    assert result["status"] == selftest.WARN
    assert result["detail"]["incomplete"] is True
    assert "cannot inspect configured root" in " ".join(result["detail"]["errors"])
    assert "PermissionError" in " ".join(result["detail"]["errors"])


def test_depth_limit_is_visible_and_duplicate_roots_do_not_duplicate_findings(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    stale = nested / "deep.part"
    stale.write_bytes(b"old")
    _age(stale, hours=48)

    bounded = selftest.check_orphan_tempfiles(tmp_path, max_depth=0)
    assert bounded["status"] == selftest.WARN
    assert bounded["detail"]["incomplete"] is True
    assert "depth limit 0" in " ".join(bounded["detail"]["errors"])

    complete = selftest.check_orphan_tempfiles(tmp_path, tmp_path, max_depth=4)
    assert complete["detail"]["count"] == 1
    assert complete["detail"]["sample"] == [str(stale)]


def test_top_level_stale_tempfile_remains_covered(tmp_path: Path) -> None:
    stale = tmp_path / ".tmp-download"
    stale.write_bytes(b"old")
    _age(stale, hours=48)

    result = selftest.check_orphan_tempfiles(tmp_path, max_age_hours=24)

    assert result["status"] == selftest.WARN
    assert result["detail"]["sample"] == [str(stale)]


BD_GATE_SCOPE = "module"
