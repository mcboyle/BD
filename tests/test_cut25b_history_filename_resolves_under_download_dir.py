"""Two library-doctor checks resolved a BASENAME against the process CWD.

THE DEFECT, measured against the real functions with production-shaped rows.

`runner_transport.py:961-989` is the dominant done-record path:

    final_path = dl_dir / rendered      # absolute
    final_path = safe_dest(final_path)
    filename   = final_path.name        # <- BASENAME ONLY
    db_log(..., filename, file_size_on_disk, ...)

So `history.filename` holds a bare basename, not a path. Both history-driven
doctor checks then fed that straight to `Path(fn)`, which resolves a relative
name against the process CWD -- never the download directory:

    list_missing_from_disk   `if fn and not Path(fn).exists(): -> MISSING`
    list_size_drift          `if not Path(fn).exists(): continue`

They fail in OPPOSITE directions off one root cause, and both are wrong on
every production row:

    disk: flat.mp4(5000), sub/nested.mp4(7000), short.mp4(100 recorded 9999)
    list_missing_from_disk -> ['short.mp4','nested.mp4','flat.mp4']   all exist
    list_size_drift        -> []                                      one is truncated

`missing` cries wolf on a healthy library -- CLAUDE.md calls over-sensitivity a
soundness bug, not a safe default, because a panel that always alarms gets
ignored. `size_drift` is the section 0 shape: it reports 0 truthfully and
uselessly, and a genuinely truncated download is invisible.

WHY THE EXISTING CONTRACT TEST IS BLIND. test_library_audit_panel_contract.py
drives the real audit() -- but its fixture records an ABSOLUTE filename
(`os.path.join(tempfile.mkdtemp(...), "vanished.mp4")`). Absolute paths resolve
identically under CWD and under download_dir, so the basename case is
structurally outside its denominator. It passes on the broken and the fixed
tree alike, which is why this file has to exist.

WHY NOT A FLAT JOIN. `final_path.name` DISCARDS any subdirectory the filename
template created, so `download_dir / fn` finds flat files and misses nested
ones. Resolution therefore falls back to an index of the tree by basename.

UNRESOLVED IS A THIRD STATE, and it is the whole reason this cut is not a
one-line change. A name that matches TWO files under download_dir cannot be
attributed to a row: reporting it as missing would cry wolf and comparing its
size would compare against a guess. Such rows are reported as neither, and that
is asserted below so a future "simplification" to first-match-wins is caught.

SCOPE: resolution only. This cut does NOT change what audit() returns, so the
key set that test_library_audit_panel_contract.py and api-types.ts pin is
untouched.

KNOWN RESIDUE, deliberately not hidden: rows written before v3.66.820 recorded
a PRE-tag size, and no backfill exists anywhere in the tree (history.file_size
is never UPDATEd -- 8 `UPDATE history` sites, none touches it). Once resolution
works, those legacy rows surface as POSITIVE drift deltas. That is a truthful
report of a real mismatch and is left visible on purpose: a tolerance wide
enough to hide it would also hide a real truncation of the same magnitude.
Truncation shows as a NEGATIVE delta and sorts first.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def lib(clean_workdir):
    """Isolated DB + a download dir with a flat file, a nested one, and a
    truncated one -- the three cases the two checks must tell apart."""
    import bulk_downloader.db as db
    from bulk_downloader import library_final as lf

    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="cut25b_db_"), "q.db")
    db.db_init()

    dl = Path(tempfile.mkdtemp(prefix="cut25b_dl_"))
    (dl / "sub").mkdir()
    (dl / "flat.mp4").write_bytes(b"\0" * 5000)
    (dl / "sub" / "nested.mp4").write_bytes(b"\0" * 7000)
    (dl / "short.mp4").write_bytes(b"\0" * 100)

    rows = [("u1", "flat.mp4", 5000), ("u2", "nested.mp4", 7000),
            ("u3", "short.mp4", 9999)]
    for url, fn, sz in rows:
        db.db_log(site_id="s", site_name="S", url=url, status="done",
                  filename=fn, file_size=sz)
    try:
        yield lf, dl
    finally:
        db.DB_PATH = saved


def test_a_present_basename_row_is_not_reported_missing(lib):
    """RED. Every production row was reported missing: Path("flat.mp4") is
    resolved against the CWD, where nothing lives."""
    lf, dl = lib
    missing = [m["filename"] for m in lf.list_missing_from_disk(download_dir=str(dl))]
    assert missing == [], (
        "files that exist under download_dir were reported missing; the "
        f"basename was resolved against the CWD instead: {missing}")


def test_a_nested_file_is_found_not_just_a_flat_one(lib):
    """A flat join alone is not enough: the recorded basename has already lost
    the subdirectory the filename template created."""
    lf, dl = lib
    missing = [m["filename"] for m in lf.list_missing_from_disk(download_dir=str(dl))]
    assert "nested.mp4" not in missing, (
        "a file in a download_dir SUBDIRECTORY was reported missing, so "
        "resolution is a flat join and does not descend")


def test_a_genuinely_absent_row_is_still_reported_missing(lib):
    """The fix must not achieve silence by reporting nothing.

    Without this, `return []` passes both tests above.
    """
    import bulk_downloader.db as db
    lf, dl = lib
    db.db_log(site_id="s", site_name="S", url="u4", status="done",
              filename="never_downloaded.mp4", file_size=1234)
    missing = [m["filename"] for m in lf.list_missing_from_disk(download_dir=str(dl))]
    assert missing == ["never_downloaded.mp4"], (
        f"a row whose file is genuinely absent must still be reported: {missing}")


def test_size_drift_sees_a_real_truncation(lib):
    """RED. drift was [] because the lookup failed and the loop `continue`d --
    a check reporting clean because it could not see its subject."""
    lf, dl = lib
    drift = lf.list_size_drift(str(dl))
    hits = {d["filename"]: d for d in drift}
    assert "short.mp4" in hits, (
        f"a truncated download (recorded 9999, on disk 100) was not reported: {drift}")
    d = hits["short.mp4"]
    assert d["recorded_bytes"] == 9999 and d["disk_bytes"] == 100, d
    assert d["delta_bytes"] == -9899, d


def test_size_drift_does_not_cry_wolf_on_intact_files(lib):
    """The other half: files whose size matches must NOT be reported.

    A resolver that returned the wrong file would satisfy the test above and
    fail this one.
    """
    lf, dl = lib
    reported = {d["filename"] for d in lf.list_size_drift(str(dl))}
    assert "flat.mp4" not in reported and "nested.mp4" not in reported, (
        f"intact files were reported as drifted: {reported}")


def test_an_ambiguous_basename_is_reported_as_neither(lib):
    """UNRESOLVED IS A THIRD STATE.

    Two files share a basename under download_dir, so the row cannot be
    attributed to either. Reporting it missing would cry wolf; comparing its
    size would compare against a guess. It must be neither -- and this is what
    catches a future 'simplification' to first-match-wins.
    """
    import bulk_downloader.db as db
    lf, dl = lib
    (dl / "a").mkdir()
    (dl / "b").mkdir()
    (dl / "a" / "twin.mp4").write_bytes(b"\0" * 111)
    (dl / "b" / "twin.mp4").write_bytes(b"\0" * 222)
    db.db_log(site_id="s", site_name="S", url="u5", status="done",
              filename="twin.mp4", file_size=111)

    missing = {m["filename"] for m in lf.list_missing_from_disk(download_dir=str(dl))}
    assert "twin.mp4" not in missing, (
        "an ambiguous basename was reported missing; a file by that name does "
        "exist, so this is a cry-wolf")
    drift = {d["filename"] for d in lf.list_size_drift(str(dl))}
    assert "twin.mp4" not in drift, (
        "an ambiguous basename was size-compared against a guessed file; "
        "first-match-wins is not resolution")


def test_an_absolute_filename_still_resolves_verbatim(lib):
    """Rows written by batch_ops / storage_rebalance carry an ABSOLUTE path.

    Those must keep resolving exactly as before, including when they live
    outside download_dir entirely.
    """
    import bulk_downloader.db as db
    lf, dl = lib
    outside = Path(tempfile.mkdtemp(prefix="cut25b_out_"))
    (outside / "abs.mp4").write_bytes(b"\0" * 300)
    db.db_log(site_id="s", site_name="S", url="u6", status="done",
              filename=str(outside / "abs.mp4"), file_size=300)

    missing = {m["filename"] for m in lf.list_missing_from_disk(download_dir=str(dl))}
    assert str(outside / "abs.mp4") not in missing, (
        "an absolute path outside download_dir stopped resolving")
    drift = {d["filename"] for d in lf.list_size_drift(str(dl))}
    assert str(outside / "abs.mp4") not in drift, "absolute intact file reported as drifted"


def test_audit_key_set_is_unchanged(lib):
    """This cut changes resolution, not the contract.

    test_library_audit_panel_contract.py and api-types.ts pin audit()'s exact
    key set; a new key here would break the SPA panel silently.
    """
    lf, dl = lib
    rep = lf.audit(download_dir=str(dl))
    assert set(rep) == {
        "orphans", "missing", "duplicate_groups", "duplicate_reclaimable_gb",
        "size_drift", "orphan_size_gb", "sample_orphans", "sample_missing",
        "sample_duplicates", "sample_size_drift",
    }, sorted(rep)
