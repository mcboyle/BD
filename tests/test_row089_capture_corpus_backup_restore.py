"""Row 89: explicit, non-destructive capture-corpus backup and restore.

The ordinary app backup deliberately excludes captures.  This contract is a
separate operator action: both source and destination roots are required, the
only roots in scope are dom_analyzer's three capture-output directories, and a
restore never merges into an existing corpus.
"""
from __future__ import annotations

import inspect
import json
import zipfile
from pathlib import Path

from bulk_downloader import backup as bk
from bulk_downloader import dom_analyzer as da


BD_GATE_SCOPE = "module"


def _output_dirs() -> tuple[str, ...]:
    return tuple(da._CAPTURE_OUTPUT_DIRS)


def _make_empty(root: Path) -> None:
    for directory in _output_dirs():
        (root / directory).mkdir(parents=True)


def _make_present(root: Path) -> tuple[Path, bytes, int]:
    _make_empty(root)
    artifact = root / "captures" / "nested" / "example.wacz"
    artifact.parent.mkdir()
    payload = b"synthetic-row089-capture"
    artifact.write_bytes(payload)
    mtime_ns = 1_700_000_000_123_456_789
    artifact.touch()
    import os
    os.utime(artifact, ns=(mtime_ns, mtime_ns))
    for directory in ("offline_out", "offline_captures"):
        sibling = root / directory / "nested" / f"{directory}.wacz"
        sibling.parent.mkdir()
        sibling.write_bytes(f"synthetic-{directory}".encode("ascii"))
    return artifact, payload, mtime_ns


def test_corpus_backup_and_restore_require_explicit_roots() -> None:
    """Neither operation may discover or mutate a live corpus by default."""
    assert inspect.signature(bk.create_capture_corpus_backup).parameters[
        "source_root"
    ].default is inspect.Parameter.empty
    assert inspect.signature(bk.restore_capture_corpus_backup).parameters[
        "target_root"
    ].default is inspect.Parameter.empty


def test_present_corpus_round_trips_bytes_mtime_and_declared_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    artifact, payload, mtime_ns = _make_present(source)
    archive = tmp_path / "corpus.zip"

    created = bk.create_capture_corpus_backup(archive, source_root=source)

    assert created["ok"] is True, created
    assert created["source_corpus_state"] == "present"
    assert created["files"] == len(_output_dirs())
    assert archive.is_file()
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("_capture_corpus_manifest.json"))
        assert manifest["source_corpus_state"] == "present"
        assert [row["arcname"] for row in manifest["files"]] == [
            "captures/nested/example.wacz",
            "offline_captures/nested/offline_captures.wacz",
            "offline_out/nested/offline_out.wacz",
        ]

    destination = tmp_path / "destination"
    restored = bk.restore_capture_corpus_backup(archive, target_root=destination)

    copied = destination / "captures" / "nested" / artifact.name
    assert restored["ok"] is True, restored
    assert restored["target_corpus_state"] == "absent"
    assert restored["files_restored"] == len(_output_dirs())
    assert copied.read_bytes() == payload
    assert copied.stat().st_mtime_ns == mtime_ns
    assert da.scan_captures_summary(root=destination)["corpus_state"] == "present"
    assert (destination / "offline_out" / "nested" / "offline_out.wacz").is_file()
    assert (destination / "offline_captures" / "nested" / "offline_captures.wacz").is_file()


def test_empty_corpus_is_a_restorable_empty_snapshot_not_an_absent_one(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_empty(source)
    archive = tmp_path / "empty.zip"

    created = bk.create_capture_corpus_backup(archive, source_root=source)

    assert created["ok"] is True, created
    assert created["source_corpus_state"] == "empty"
    assert created["files"] == 0
    destination = tmp_path / "destination"
    restored = bk.restore_capture_corpus_backup(archive, target_root=destination)
    assert restored["ok"] is True, restored
    assert bk._capture_corpus_state(destination)[0] == "empty"
    assert all((destination / directory).is_dir() for directory in _output_dirs())


def test_empty_state_with_unrecognised_residue_refuses_a_misleading_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_empty(source)
    (source / "captures" / ".unexpected").write_bytes(b"not-a-capture")
    archive = tmp_path / "empty-with-residue.zip"

    created = bk.create_capture_corpus_backup(archive, source_root=source)

    assert created["ok"] is False, created
    assert created["source_corpus_state"] == "empty"
    assert not archive.exists()


def test_absent_and_partial_sources_refuse_to_create_a_misleading_backup(tmp_path: Path) -> None:
    absent_archive = tmp_path / "absent.zip"
    absent = bk.create_capture_corpus_backup(absent_archive, source_root=tmp_path / "absent")
    assert absent["ok"] is False, absent
    assert absent["source_corpus_state"] == "absent"
    assert not absent_archive.exists()

    partial_root = tmp_path / "partial"
    (partial_root / "captures").mkdir(parents=True)
    partial_archive = tmp_path / "partial.zip"
    partial = bk.create_capture_corpus_backup(partial_archive, source_root=partial_root)
    assert partial["ok"] is False, partial
    assert partial["source_corpus_state"] == "partial"
    assert not partial_archive.exists()


def test_backup_archive_cannot_be_written_into_the_live_corpus(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_present(source)
    archive = source / "captures" / "corpus.zip"

    created = bk.create_capture_corpus_backup(archive, source_root=source)

    assert created["ok"] is False, created
    assert not archive.exists()


def test_restore_refuses_present_or_partial_target_without_touching_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_present(source)
    archive = tmp_path / "corpus.zip"
    assert bk.create_capture_corpus_backup(archive, source_root=source)["ok"] is True

    populated = tmp_path / "populated"
    _make_empty(populated)
    incumbent = populated / "captures" / "incumbent.wacz"
    incumbent.write_bytes(b"do-not-overwrite")
    refused = bk.restore_capture_corpus_backup(archive, target_root=populated)
    assert refused["ok"] is False, refused
    assert refused["target_corpus_state"] == "present"
    assert incumbent.read_bytes() == b"do-not-overwrite"

    partial = tmp_path / "partial"
    (partial / "captures").mkdir(parents=True)
    refused = bk.restore_capture_corpus_backup(archive, target_root=partial)
    assert refused["ok"] is False, refused
    assert refused["target_corpus_state"] == "partial"
    assert not (partial / "offline_out").exists()


def test_restore_accepts_an_existing_physically_empty_corpus(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_present(source)
    archive = tmp_path / "corpus.zip"
    assert bk.create_capture_corpus_backup(archive, source_root=source)["ok"] is True

    target = tmp_path / "target"
    _make_empty(target)
    restored = bk.restore_capture_corpus_backup(archive, target_root=target)

    assert restored["ok"] is True, restored
    assert restored["target_corpus_state"] == "empty"
    assert (target / "captures" / "nested" / "example.wacz").is_file()


def test_restore_rejects_unsafe_member_before_creating_target_paths(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    manifest = {
        "format": "bd-capture-corpus-backup",
        "format_version": 1,
        "source_corpus_state": "present",
        "roots": list(_output_dirs()),
        "files": [{"arcname": "../escape.wacz", "sha256": "0" * 64,
                   "size": 1, "mtime_ns": 0}],
    }
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("_capture_corpus_manifest.json", json.dumps(manifest))
        zf.writestr("../escape.wacz", b"x")

    target = tmp_path / "target"
    restored = bk.restore_capture_corpus_backup(archive, target_root=target)

    assert restored["ok"] is False, restored
    assert not target.exists(), "unsafe archive must fail before creating output roots"
    assert not (tmp_path / "escape.wacz").exists()
