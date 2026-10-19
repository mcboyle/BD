"""o1671-a07-photosets-free-name-race (O1671 AUDIT-07, MED, correctness).

bulk_downloader/photo_sets.py _free_name picked the first non-existing "name" / "name_N" with
exists() and returned it WITHOUT creating it. Two concurrent extractions of same-named sets in
one parent therefore both received the same folder: the second work.mkdir() raised
FileExistsError and its error path rmtree'd the FIRST extraction's half-written work folder.

Contract: _free_name RESERVES the name atomically (mkdir, exist_ok=False) and returns the
directory it actually created; extract_images moves the finished work folder over that empty
reservation. Two racing callers always end with two distinct existing folders, each complete.

RED (base 8dda38e3): test_free_name_reserves_atomically -- both threads get the same path.
"""

BD_GATE_SCOPE = "module"

import threading
import zipfile
from pathlib import Path

import pytest

from bulk_downloader import photo_sets

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
MEMBERS = ("a.jpg", "b.jpg", "c.jpg")


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in MEMBERS:
            zf.writestr(name, JPEG)
    return path


def _race(parties, fn):
    """Run fn(idx) on `parties` threads at once; return ([results], [exceptions])."""
    results, errors = [None] * parties, [None] * parties

    def run(idx):
        try:
            results[idx] = fn(idx)
        except BaseException as e:  # noqa: BLE001 -- the test reports it
            errors[idx] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(parties)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "race threads hung"
    return results, errors


def _reserve_then_meet(free_name, parent, name, parties=2):
    """Every thread calls free_name(parent, name) and only then meets the others at a barrier,
    so no thread acts on its answer before every thread has one. A non-atomic free_name hands
    the same path to all of them; an atomic one cannot."""
    gate = threading.Barrier(parties, timeout=30)

    def body(idx):
        got = free_name(parent, name)
        gate.wait()
        return got

    results, errors = _race(parties, body)
    assert errors == [None] * parties, errors
    return results


def _base_free_name(parent: Path, name: str) -> Path:
    """The pre-fix implementation (positive control for the probe: it must be caught)."""
    candidate = parent / name
    i = 1
    while candidate.exists():
        candidate = parent / f"{name}_{i}"
        i += 1
    return candidate


def test_free_name_reserves_atomically(tmp_path):
    got = _reserve_then_meet(photo_sets._free_name, tmp_path, "set")
    assert len({str(p) for p in got}) == 2, f"both callers received the same folder: {got}"
    for p in got:
        assert p.is_dir(), f"_free_name returned {p} without creating it"
    assert {p.name for p in got} == {"set", "set_1"}


def test_free_name_probe_catches_the_pre_fix_shape(tmp_path):
    """Positive control (rule 7): the same probe, run against the non-atomic original, sees
    the collision. If this ever passes vacuously the race test above proves nothing."""
    got = _reserve_then_meet(_base_free_name, tmp_path, "set")
    assert len({str(p) for p in got}) == 1, f"probe lost its teeth: {got}"
    assert not got[0].exists()


def test_free_name_single_caller_sequence(tmp_path):
    """Control: one caller gets `name`, then `name_1`, then `name_2`; each exists afterwards."""
    first = photo_sets._free_name(tmp_path, "set")
    second = photo_sets._free_name(tmp_path, "set")
    third = photo_sets._free_name(tmp_path, "set")
    assert [p.name for p in (first, second, third)] == ["set", "set_1", "set_2"]
    assert all(p.is_dir() for p in (first, second, third))


def test_free_name_creates_missing_parent(tmp_path):
    got = photo_sets._free_name(tmp_path / "deep" / "er", "set")
    assert got == tmp_path / "deep" / "er" / "set" and got.is_dir()


def test_concurrent_extractions_get_distinct_complete_folders(tmp_path, monkeypatch):
    """The extraction path: two threads extract same-named sets into one parent. The name
    step of each is held at a barrier until both have chosen (the pre-fix window), then both
    proceed. Both must finish, into two distinct folders, each holding every image."""
    zips = [_make_zip(tmp_path / f"src{i}.zip") for i in range(2)]
    parent = tmp_path / "scene"
    parent.mkdir()
    gate = threading.Barrier(2, timeout=30)
    real = photo_sets._free_name

    def held(parent_, name_):
        got = real(parent_, name_)
        gate.wait()
        return got

    monkeypatch.setattr(photo_sets, "_free_name", held)
    results, errors = _race(2, lambda i: photo_sets.extract_images(zips[i], parent / "set"))
    assert errors == [None, None], f"an extraction failed: {errors!r}"
    folders = [folder for _, folder in results]
    assert len({str(f) for f in folders}) == 2, f"both extractions landed in {folders}"
    for written, folder in results:
        assert written == len(MEMBERS)
        assert sorted(p.name for p in folder.iterdir()) == sorted(MEMBERS)
    assert sorted(p.name for p in parent.iterdir()) == ["set", "set_1"], "stale .extracting left"


def test_extract_images_single_call_unchanged(tmp_path):
    z = _make_zip(tmp_path / "set.zip")
    written, folder = photo_sets.extract_images(z, tmp_path / "set")
    assert (written, folder) == (len(MEMBERS), tmp_path / "set")
    assert sorted(p.name for p in folder.iterdir()) == sorted(MEMBERS)
    written2, folder2 = photo_sets.extract_images(z, tmp_path / "set")
    assert (written2, folder2) == (len(MEMBERS), tmp_path / "set_1")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["set", "set.zip", "set_1"]


def test_extract_images_failure_leaves_no_reservation(tmp_path):
    """Negative control: a broken archive raises, and neither the work folder nor the
    reserved destination survives, so the archive's own name is free for a retry."""
    bad = tmp_path / "set.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(zipfile.BadZipFile):
        photo_sets.extract_images(bad, tmp_path / "set")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["set.zip"]
    good = _make_zip(tmp_path / "good.zip")
    written, folder = photo_sets.extract_images(good, tmp_path / "set")
    assert (written, folder) == (len(MEMBERS), tmp_path / "set")


@pytest.mark.parametrize("failure", [OSError("SECOND-RESERVATION"), KeyboardInterrupt("SECOND-RESERVATION")])
def test_work_reservation_failure_removes_dest(tmp_path, monkeypatch, failure):
    archive = _make_zip(tmp_path / "set.zip")
    before = archive.read_bytes()
    neighbor = tmp_path / "neighbor"
    neighbor.mkdir()
    marker = neighbor / "keep.jpg"
    marker.write_bytes(JPEG)
    reserve = photo_sets._free_name
    calls = []

    def fail_second(parent, name):
        calls.append((parent, name))
        if len(calls) == 2:
            assert (tmp_path / "set").is_dir()
            assert name == "set.extracting"
            raise failure
        return reserve(parent, name)

    monkeypatch.setattr(photo_sets, "_free_name", fail_second)
    with pytest.raises(type(failure), match="SECOND-RESERVATION"):
        photo_sets.extract_images(archive, tmp_path / "set")
    assert len(calls) == 2
    assert not (tmp_path / "set").exists(), "destination reservation leaked after second reservation failed"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["neighbor", "set.zip"]
    assert archive.read_bytes() == before
    assert marker.read_bytes() == JPEG
