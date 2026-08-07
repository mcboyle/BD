"""The forward path feeds a BASENAME into library_record(), whose contract is
an absolute filesystem path -- so every downloaded file gets TWO library rows.

THE DEFECT, measured against the real functions with production-shaped input.

`runner_transport.py:961-989` is the dominant done-record path:

    final_path = dl_dir / rendered      # absolute
    final_path = safe_dest(final_path)
    filename   = final_path.name        # <- BASENAME ONLY
    db_log(..., filename, file_size_on_disk, ...)

`db.py:1036-1038` stores that string in `history.filename`, and `db.py:1067-1072`
then forwards the SAME string to `library.library_record(...)`:

    if status == "done" and filename:
        _library.library_record(filename, history_id=..., site_id=..., file_size=...)

`library.library_record` (`library.py:90`) documents `file_path` as the path of a
file on disk. It never resolves it (`:107` `file_path = str(file_path)`), stats it
verbatim (`:112`), and stores it verbatim (`:166`) into a column declared
`file_path TEXT NOT NULL UNIQUE` (`migrations.py:384`). A bare basename stat'd
from the service CWD raises OSError, so the row is born `file_exists=0` with an
unresolvable path.

`library._scan_worker` (`:812`, `:841`) later records the SAME physical file under
its ABSOLUTE path. `'flat.mp4' != '/.../dl/flat.mp4'`, so the UNIQUE key does not
collide and a SECOND row is inserted. The ghost is never reclaimed: the
missing-pass query is `SELECT ... WHERE file_exists=1` (`:859`), so
`file_exists=0` ghosts are structurally outside its denominator, and
`_fp_under_root` (`:765-772`) is False for a relative path anyway.

Consumers then double-count: `library_browse` (`:229-267`) applies no
`file_exists` filter by default, and `library_stats` (`:537-563`) SUMs over the
whole table.

WHY THE EXISTING FORWARD-PATH TEST IS BLIND. `test_v3_50_phase3.py:534`
(`test_db_log_done_creates_library_row`) calls db_log with `/tmp/dl/scene.mp4` --
an ABSOLUTE path -- and asserts one row. Absolute paths behave identically before
and after any fix, so the input form production actually produces (a basename) is
structurally outside that test's denominator. It is green on the broken tree.
That is CLAUDE.md section 0 applied to a test, and it is why this file exists.

THE SUBDIRECTORY CASE IS INCLUDED ON PURPOSE. `final_path.name` discards any
subdirectory the filename template created, so the defect is not confined to bare
basenames -- a template-relative `Studio/nested.mp4` is equally unresolvable, and
equally duplicated. It is also the reason a flat `download_dir / filename` join is
not a correct fix (the same point `library_final.py:208-216` already makes).

WHAT IS DELIBERATELY *NOT* ASSERTED HERE: site attribution. The ghost carries the
real `site_id` while the scanned row carries `''` (`_scan_worker:841` passes no
`site_id`), so today the per-site view sees only unopenable rows. But a fix that
records NO library row when no absolute path is available -- the third-state
option -- leaves the scanned row's `site_id` empty, which would make any
"rows with a site_id name a real file" assertion pass over a ZERO denominator.
A vacuous assertion is worse than none (section 0), so this file asserts only
invariants that both candidate fixes must satisfy non-vacuously.

ISOLATION: `db._resolve_db_path()` consults BD_INSTALL_DIR, never BD_HOME, and
falls back to a CWD-relative path -- so `clean_workdir` (which sets BOTH, per
CLAUDE.md section 5) is required or the probe writes `downloader_history.db` into
the repo and the next run reads its rows.
"""
from __future__ import annotations

import os
import time

import pytest

import scan_wait


@pytest.fixture(autouse=True)
def _bd_boot_app():
    """v3.66.926 (item 11): the DB boot is no longer an import side effect.

    Tests in this file used to get a migrated schema because `import
    bulk_downloader.app` did db_init() + migrations.apply_pending() at MODULE
    scope -- which is exactly what raced 64 ways across xdist workers and
    destroyed the operator's live history. The work still happens; it is now
    explicit. Production does the same thing (downloader_ui.py calls
    boot_once() before serving), so this asks for the real contract rather
    than re-creating the side effect.

    boot_once() is idempotent and keyed on the RESOLVED DB PATH, so a file
    whose fixtures give each test its own tmpdir gets a boot per database
    rather than one for the whole file.
    """
    from bulk_downloader.app import boot_once
    boot_once()

pytestmark = pytest.mark.bd_module_wipe

# Distinct, non-round sizes so a doubled SUM cannot coincide with the true one.
_FLAT_BYTES = 4096
_NESTED_BYTES = 7000


@pytest.fixture
def forward_path_probe(clean_workdir):
    """Drive the real production sequence once:

        1. two real video files land in a download dir
        2. db_log(..., 'done', <basename-or-template-relative>, <size>) -- exactly
           what runner_transport.py:1210 passes
        3. library.scan_start([download_dir]) -- the backward path, joined

    Returns a dict of module handles + the two absolute paths.
    """
    from bulk_downloader.db import db_init, db_log, db_conn
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader import library as lib

    db_init()
    apply_pending()

    dl = clean_workdir / "dl"
    (dl / "Studio").mkdir(parents=True)
    flat = dl / "flat.mp4"
    flat.write_bytes(b"\0" * _FLAT_BYTES)
    nested = dl / "Studio" / "nested.mp4"
    nested.write_bytes(b"\0" * _NESTED_BYTES)

    # The producer's own shapes: a bare basename, and a template-relative path.
    db_log("site1", "Site One", "https://example.invalid/a", "done",
           "flat.mp4", _FLAT_BYTES, "")
    db_log("site1", "Site One", "https://example.invalid/b", "done",
           os.path.join("Studio", "nested.mp4"), _NESTED_BYTES, "")

    # Was a 10s poll that checked `running is False` afterwards. That check
    # is unsound on its own -- scan_cancel() clears `running` while the worker
    # keeps walking -- and start_and_wait waits on `finished_at` instead.
    status = scan_wait.start_and_wait(lib, [str(dl)])

    return {
        "lib": lib,
        "db_conn": db_conn,
        "dl": dl,
        "flat": str(flat),
        "nested": str(nested),
        "scan_status": status,
    }


# ── Canaries: a zero denominator would make every rule below vacuous ────────

def test_the_probe_actually_wrote_two_done_history_rows(forward_path_probe):
    """Canary. If db_log did not insert, the library assertions below would be
    asserting over an empty table and would pass for the wrong reason."""
    with forward_path_probe["db_conn"]() as cx:
        rows = [dict(r) for r in cx.execute(
            "SELECT filename, file_size FROM history WHERE status='done' "
            "ORDER BY id").fetchall()]
    assert len(rows) == 2, f"expected 2 done history rows, got {rows}"
    assert rows[0]["filename"] == "flat.mp4"
    assert rows[1]["filename"] == os.path.join("Studio", "nested.mp4")


def test_the_probe_actually_put_two_real_files_on_disk(forward_path_probe):
    """Canary. The duplicate only exists because a REAL file is also reachable
    by absolute path; if the files were absent the scan would find nothing and
    'one row per file' would hold trivially."""
    flat = forward_path_probe["flat"]
    nested = forward_path_probe["nested"]
    assert os.path.isfile(flat) and os.path.getsize(flat) == _FLAT_BYTES
    assert os.path.isfile(nested) and os.path.getsize(nested) == _NESTED_BYTES


def test_the_scan_actually_walked_the_download_dir(forward_path_probe):
    """Canary. `seen` is the scan's denominator. A scan that walked 0 files
    would leave only the forward-path rows and hide the duplication."""
    s = forward_path_probe["scan_status"]
    assert s["seen"] == 2, f"scan saw {s['seen']} files, expected 2: {s}"
    assert s["errors"] == 0, f"scan reported errors: {s.get('error_samples')}"


# ── The defect ─────────────────────────────────────────────────────────────

def test_every_library_row_names_an_absolute_path(forward_path_probe):
    """library_record's file_path is a filesystem path. A relative string is
    resolved against whatever CWD happens to be, which is never the download
    dir -- so it names nothing."""
    rows, _ = forward_path_probe["lib"].library_browse(limit=100)
    paths = [r["file_path"] for r in rows]
    assert paths, "zero library rows -- denominator empty, rule below is vacuous"
    relative = [p for p in paths if not os.path.isabs(p)]
    assert relative == [], (
        f"library rows carry non-absolute file_path values: {relative} "
        f"(all rows: {paths})")


def test_each_downloaded_file_gets_exactly_one_library_row(forward_path_probe):
    """The forward path and the scanner must converge on ONE row per physical
    file. Today they key on different strings, so the UNIQUE index does not
    collide and the table permanently double-counts every download."""
    rows, _ = forward_path_probe["lib"].library_browse(limit=100)
    paths = sorted(r["file_path"] for r in rows)
    expected = sorted([forward_path_probe["flat"], forward_path_probe["nested"]])
    assert paths == expected, (
        f"expected one row per physical file {expected}, got {len(paths)} "
        f"rows: {paths}")


def test_every_browsable_library_row_names_a_file_that_exists(forward_path_probe):
    """library_browse applies no file_exists filter by default, so anything in
    the table is shown to the operator. A row the operator cannot open is a
    defect, not a display concern."""
    rows, _ = forward_path_probe["lib"].library_browse(limit=100)
    assert rows, "zero library rows -- denominator empty, rule below is vacuous"
    unopenable = [r["file_path"] for r in rows
                  if not os.path.isfile(r["file_path"])]
    assert unopenable == [], (
        f"library_browse returned rows naming no file on disk: {unopenable}")


def test_library_stats_counts_each_file_once(forward_path_probe):
    """library_stats SUMs the whole table, so a duplicate row inflates both the
    file count and the reported disk usage."""
    stats = forward_path_probe["lib"].library_stats()
    assert stats.get("error") is None, stats.get("error")
    assert stats["total_files"] == 2, (
        f"total_files={stats['total_files']}, expected 2 (two files on disk)")
    assert stats["total_size"] == _FLAT_BYTES + _NESTED_BYTES, (
        f"total_size={stats['total_size']}, expected "
        f"{_FLAT_BYTES + _NESTED_BYTES}")
    assert stats["missing_files"] == 0, (
        f"missing_files={stats['missing_files']}, but both files are on disk")


def test_the_forward_path_itself_records_an_absolute_row(clean_workdir):
    """The green half of the contract, which the fixture above cannot see.

    Every row the other tests assert over is produced by the SCANNER, so an
    implementation that deleted db_log's forward-path library_record outright
    would pass them all. Measured with runtime mutants: dropping the file_path
    kwarg, and deleting the forward path entirely, both left this file 7/7
    green. That is CLAUDE.md section 0 applied to this gate itself -- a
    denominator (library rows) that structurally excludes its subject
    (forward-path rows).

    So: record via db_log ONLY, over a file the scanner never walks, and
    assert the row exists at the absolute path.
    """
    from bulk_downloader import app as _app  # boot triggers migrations
    from bulk_downloader.db import db_log
    from bulk_downloader import library as lib

    unscanned = clean_workdir / "elsewhere"
    unscanned.mkdir(parents=True)
    target = unscanned / "forward_only.mp4"
    target.write_bytes(b"\0" * 4096)

    db_log("site2", "Site Two", "https://example.invalid/fwd", "done",
           target.name, 4096, "", file_path=str(target))

    rows, _ = lib.library_browse(limit=50)
    matches = [r for r in rows if r["file_path"] == str(target)]
    assert len(matches) == 1, (
        "the forward path did not record a row at the absolute path. No scan "
        f"ran here, so this row can only come from db_log itself. rows={rows!r}"
    )
    assert matches[0]["history_id"] is not None, (
        "the forward-path row must carry its history back-reference")


def test_a_basename_only_producer_still_records_nothing(clean_workdir):
    """The negative half, isolated from the scanner for the same reason."""
    from bulk_downloader import app as _app
    from bulk_downloader.db import db_log
    from bulk_downloader import library as lib

    unscanned = clean_workdir / "elsewhere2"
    unscanned.mkdir(parents=True)
    (unscanned / "ghosty.mp4").write_bytes(b"\0" * 2048)

    db_log("site3", "Site Three", "https://example.invalid/ghost", "done",
           "ghosty.mp4", 2048, "")  # no file_path, basename only

    rows, _ = lib.library_browse(limit=50)
    ghosts = [r for r in rows if not os.path.isabs(r["file_path"])]
    assert not ghosts, (
        f"a basename-only producer minted a non-absolute library row: {ghosts!r}")
