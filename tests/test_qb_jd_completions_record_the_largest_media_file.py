"""15.11 (option b): qB/JD completions record the LARGEST MEDIA FILE.

The qBittorrent and JDownloader bridges report completion with a bare NAME --
qb_bridge.py poll() returns ``t.get("name")``, jd_bridge.py poll() returns
``row.get("name")`` -- never a path, and for a multi-file torrent that name is
a DIRECTORY. v3.66.837 keyed db_log's forward-path library record on
ABSOLUTENESS, so these two done-sites (runner_integrations.py, the only
db_log callers on the qB/JD paths) record NO library row today. Measured on
this tree before the fix: both bridges driven to done -> 2 history rows,
0 library rows.

The fix: ``library.library_path_for_completion(dl_dir, name)`` resolves the
name to the absolute path of the file a library row should record --
  * dl_dir/name is a FILE      -> that path, ONLY if its extension is in
                                  _VIDEO_EXTS. Without the predicate a
                                  single-file .rar (JD's common case) mints a
                                  row the next scan flips to file_exists=0
                                  while the file exists (measured: pre-scan
                                  (rar, 1) -> post-scan (rar, 0), file still
                                  on disk) -- the exact v3.66.837 ghost.
  * dl_dir/name is a DIRECTORY -> the largest _VIDEO_EXTS file inside, walked
                                  with the scanner's own skip predicate; ties
                                  break to the lexicographically smallest
                                  path so the answer cannot depend on
                                  os.walk order.
  * neither                    -> None; a wrong row is worse than no row.
-- and the done-sites pass it as db_log(..., file_path=...), resolved in its
OWN try so a resolution failure degrades to file_path=None and never costs
the history row.

WHY THE CONVERGENCE TEST IS PER-ROW, NOT A TABLE COUNT. A later scan
legitimately adds every OTHER media file in the torrent directory as its own
library row (measured: [big, small] post-scan). Asserting "still exactly one
row" fails on the CORRECT tree, and weakening it to "at least one" guts it.
The invariants that distinguish a correct tree from a broken one are:
exactly ONE row whose path is the large file (the forward row and the scan
row COLLIDE on UNIQUE file_path instead of duplicating), the full set of
paths is exactly {big, small}, and the forward row keeps file_exists=1 and
its history_id through the scan (library_record COALESCEs history_id, and
the scanner's LIKE match on the torrent NAME cannot find the basename, so
only COALESCE preserves the link).

ISOLATION: clean_workdir sets BD_INSTALL_DIR and chdirs (db writes stay in
tmp); bd_module_wipe drops bulk_downloader.* so every test imports fresh
modules -- which is also why all bulk_downloader imports live INSIDE the
tests, never at module scope.

NO fixed-width source windows in this file (test_source_windows_do_not_shift
ratchets their count).
"""
from __future__ import annotations

import os
import threading
import time

import pytest

pytestmark = pytest.mark.bd_module_wipe

# Distinct, non-round sizes: a doubled or substituted value cannot collide.
_BIG_BYTES = 9000
_SMALL_BYTES = 3000
_JUNK_BYTES = 50000   # biggest file in the pack, deliberately NOT media
_SOLO_BYTES = 5000
_RAR_BYTES = 7777
_PACK_TOTAL = 12345   # what the backend reports as bytes_total; NOT on-disk


# ── Harness ─────────────────────────────────────────────────────────────────

class _FakeQBClient:
    """Stands in for qb_bridge.QBittorrentClient. poll() immediately reports
    done with the configured name, so _try_qb_download's loop exits on its
    first iteration and never sleeps."""
    host, port = "127.0.0.1", 8080

    def __init__(self, name):
        self._name = name

    def is_reachable(self):
        return True

    def submit(self, url, dest_dir=None):
        return "cafe" * 10

    def poll(self, torrent_hash, timeout=10):
        return {"status": "done", "bytes_done": _PACK_TOTAL,
                "bytes_total": _PACK_TOTAL, "filename": self._name,
                "speed": 0, "error": ""}

    def cancel(self, torrent_hash, delete_files=False):
        return True


class _FakeJDClient:
    host, port = "127.0.0.1", 3128

    def __init__(self, name):
        self._name = name

    def is_reachable(self):
        return True

    def submit(self, url, cookies=None, dest_dir=None):
        return "7"

    def poll(self, link_id, timeout=10):
        return {"status": "done", "bytes_done": _PACK_TOTAL,
                "bytes_total": _PACK_TOTAL, "filename": self._name,
                "speed": 0, "error": ""}

    def cancel(self, link_id):
        return True


def _make_runner(kind, reported_name):
    """A minimal IntegrationsMixin host. Overrides exactly the collaborators
    the qB/JD paths touch; db_log stays REAL -- that is the subject."""
    from bulk_downloader.runner_integrations import IntegrationsMixin

    class _Runner(IntegrationsMixin):
        def __init__(self):
            self.site_id = "site1"
            self.config = {"name": "Test Site"}
            self._stop = threading.Event()

        def log_event(self, *a, **k):
            pass

        def _update_job(self, *a, **k):
            pass

        def login_async(self):
            pass

        def _read_cookies_for_jd(self):
            return ""

        def _get_qb_client(self):
            return _FakeQBClient(reported_name)

        def _get_jd_client(self):
            return _FakeJDClient(reported_name)

    r = _Runner()
    if kind == "qb":
        return r, (lambda dl: r._try_qb_download("magnet:?xt=urn:btih:aa", dl))
    return r, (lambda dl: r._try_jd_download("https://example.invalid/f", dl))


def _boot_db():
    from bulk_downloader.db import db_init, db_conn
    from bulk_downloader.migrations import apply_pending
    db_init()
    apply_pending()
    return db_conn


def _history_done_rows(db_conn):
    with db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, filename, file_size FROM history "
            "WHERE status='done' ORDER BY id").fetchall()]


def _run_scan(lib, root):
    started = lib.scan_start([str(root)])
    assert started.get("ok") is True, f"scan did not start: {started}"
    for _ in range(200):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    status = lib.scan_status()
    assert status.get("running") is False, "scan never finished; probe invalid"
    return status


def _make_pack(dl):
    """A torrent-shaped directory: two media files and a LARGER non-media
    file, so 'largest file' and 'largest media file' give different answers."""
    pack = dl / "Some.Torrent.Pack"
    pack.mkdir(parents=True)
    big = pack / "big.mkv"
    big.write_bytes(b"\0" * _BIG_BYTES)
    small = pack / "small.mp4"
    small.write_bytes(b"\0" * _SMALL_BYTES)
    (pack / "readme.nfo").write_bytes(b"\0" * _JUNK_BYTES)
    return str(big), str(small)


# ── The forward path records the largest media file ────────────────────────

def test_qb_directory_completion_records_the_largest_media_file(clean_workdir):
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    big, small = _make_pack(dl)
    runner, drive = _make_runner("qb", "Some.Torrent.Pack")
    ok, reason = drive(str(dl))
    assert ok is True, f"harness invalid: qB path returned {ok}, {reason!r}"

    hist = _history_done_rows(db_conn)
    assert len(hist) == 1 and hist[0]["filename"] == "Some.Torrent.Pack", (
        f"canary: the done-site must still write its history row: {hist}")

    rows, _cursor = lib.library_browse(limit=50)
    assert len(rows) == 1, (
        f"expected exactly one forward-path library row, got "
        f"{[(r['file_path'], r['file_exists']) for r in rows]}")
    assert rows[0]["file_path"] == big, (
        f"the row must name the LARGEST MEDIA file (not the larger .nfo, "
        f"not {small}): {rows[0]['file_path']}")
    assert rows[0]["file_exists"] == 1
    assert rows[0]["history_id"] == hist[0]["id"], (
        "the forward row must carry its history back-reference")


def test_jd_single_file_media_completion_records_that_file(clean_workdir):
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    dl.mkdir()
    solo = dl / "solo.mp4"
    solo.write_bytes(b"\0" * _SOLO_BYTES)
    runner, drive = _make_runner("jd", "solo.mp4")
    ok, reason = drive(str(dl))
    assert ok is True, f"harness invalid: JD path returned {ok}, {reason!r}"

    assert len(_history_done_rows(db_conn)) == 1
    rows, _cursor = lib.library_browse(limit=50)
    assert [r["file_path"] for r in rows] == [str(solo)], (
        f"single-file media completion must record exactly that file: "
        f"{[r['file_path'] for r in rows]}")
    assert rows[0]["file_exists"] == 1


# ── Per-row convergence with the scanner (the corrected R4) ────────────────

def test_forward_row_and_scan_converge_per_row(clean_workdir):
    """A scan AFTER the completion legitimately adds small.mp4 as a second
    row -- so no table-count assertion can hold. The invariants are per-row:
    one row at big (UNIQUE collision, not duplication), the path set is
    exactly {big, small}, and the forward row survives with file_exists=1,
    its history_id, and a file_size healed to the on-disk value."""
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    big, small = _make_pack(dl)
    runner, drive = _make_runner("qb", "Some.Torrent.Pack")
    ok, reason = drive(str(dl))
    assert ok is True, f"harness invalid: {reason!r}"
    hist = _history_done_rows(db_conn)
    assert len(hist) == 1, f"canary: {hist}"

    status = _run_scan(lib, dl)
    assert status["seen"] == 2, (
        f"canary: the scan must have walked both media files: {status}")

    rows, _cursor = lib.library_browse(limit=50)
    paths = sorted(r["file_path"] for r in rows)
    assert paths == sorted([big, small]), (
        f"expected exactly the two media files {sorted([big, small])}, "
        f"got {paths}")
    big_rows = [r for r in rows if r["file_path"] == big]
    assert len(big_rows) == 1, (
        f"the forward row and the scan row must COLLIDE on UNIQUE(file_path) "
        f"-- exactly one row at {big}, got {len(big_rows)}")
    assert big_rows[0]["file_exists"] == 1
    assert big_rows[0]["history_id"] == hist[0]["id"], (
        "the scan must PRESERVE the forward row's history_id (COALESCE), "
        f"got {big_rows[0]['history_id']}")
    assert big_rows[0]["file_size"] == _BIG_BYTES, (
        f"the scan must heal file_size to the on-disk value, got "
        f"{big_rows[0]['file_size']}")


# ── The fast-path media predicate (pins the .rar decision) ─────────────────

@pytest.mark.parametrize("kind", ["qb", "jd"])
def test_single_file_non_media_completion_records_no_row(clean_workdir, kind):
    """A predicate-less fast path would mint a row for pack.rar; the next
    scan only 'sees' _VIDEO_EXTS files, so its missing-pass flips that row
    to file_exists=0 WHILE THE FILE EXISTS (measured on this tree) -- the
    v3.66.837 ghost, reintroduced. Pin: no row, before AND after a scan."""
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    dl.mkdir()
    rar = dl / "pack.rar"
    rar.write_bytes(b"\0" * _RAR_BYTES)
    runner, drive = _make_runner(kind, "pack.rar")
    ok, reason = drive(str(dl))
    assert ok is True, f"harness invalid: {reason!r}"

    assert len(_history_done_rows(db_conn)) == 1, (
        "the history row must be written regardless of the library decision")
    rows, _cursor = lib.library_browse(limit=50)
    assert rows == [], (
        f"a non-media single file must record NO library row: "
        f"{[(r['file_path'], r['file_exists']) for r in rows]}")

    _run_scan(lib, dl)
    rows, _cursor = lib.library_browse(limit=50)
    assert rows == [], (
        f"post-scan there must still be no row -- a file_exists=0 row here "
        f"is the ghost class: "
        f"{[(r['file_path'], r['file_exists']) for r in rows]}")
    assert rar.is_file(), "canary: the file must still exist for this to pin"


# ── Negatives, through BOTH bridges ────────────────────────────────────────

@pytest.mark.parametrize("kind", ["qb", "jd"])
def test_resolution_failure_never_costs_the_history_row(clean_workdir,
                                                        monkeypatch, kind):
    """Kills the merged-try mutant. If path resolution shares db_log's try,
    a raise here skips the insert and the completed download is lost from
    history. On pristine source this test is red for a second reason:
    library_path_for_completion does not exist, so setattr raises."""
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    def _boom(dl_dir, name):
        raise RuntimeError("resolution blew up")

    monkeypatch.setattr(lib, "library_path_for_completion", _boom)

    dl = clean_workdir / "dl"
    dl.mkdir()
    (dl / "real.mp4").write_bytes(b"\0" * _SOLO_BYTES)
    runner, drive = _make_runner(kind, "real.mp4")
    ok, reason = drive(str(dl))
    assert ok is True, f"the done path must still succeed: {reason!r}"

    assert len(_history_done_rows(db_conn)) == 1, (
        "resolution failure must degrade to file_path=None -- the history "
        "row is the record that the download HAPPENED and must survive")
    rows, _cursor = lib.library_browse(limit=50)
    assert rows == [], "no library row on resolution failure -- and no ghost"


@pytest.mark.parametrize("kind", ["qb", "jd"])
def test_nonexistent_name_records_history_but_no_row(clean_workdir, kind):
    """The backend can report a name that is not on disk (moved, renamed,
    deleted mid-poll). A wrong row is worse than no row."""
    db_conn = _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    dl.mkdir()
    runner, drive = _make_runner(kind, "Never.Materialised")
    ok, reason = drive(str(dl))
    assert ok is True, f"harness invalid: {reason!r}"

    assert len(_history_done_rows(db_conn)) == 1
    rows, _cursor = lib.library_browse(limit=50)
    assert rows == [], (
        f"nothing on disk -> no library row: "
        f"{[r['file_path'] for r in rows]}")


# ── The resolver itself ────────────────────────────────────────────────────

def test_resolver_picks_largest_media_not_largest_file(clean_workdir):
    _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    big, small = _make_pack(dl)
    got = lib.library_path_for_completion(str(dl), "Some.Torrent.Pack")
    assert got == big, f"largest MEDIA file, not the larger .nfo: {got}"
    # File fast path: media yes, non-media no.
    solo = dl / "solo.mp4"
    solo.write_bytes(b"\0" * _SOLO_BYTES)
    (dl / "pack.rar").write_bytes(b"\0" * _RAR_BYTES)
    assert lib.library_path_for_completion(str(dl), "solo.mp4") == str(solo)
    assert lib.library_path_for_completion(str(dl), "pack.rar") is None
    # Nothing there, or nothing media inside: None, not an invented row.
    assert lib.library_path_for_completion(str(dl), "No.Such.Name") is None
    junk = dl / "JunkOnly"
    junk.mkdir()
    (junk / "x.rar").write_bytes(b"\0" * _RAR_BYTES)
    assert lib.library_path_for_completion(str(dl), "JunkOnly") is None
    # A name that escapes dl_dir must not resolve (the name comes from the
    # remote side).
    outside = clean_workdir / "outside"
    outside.mkdir()
    (outside / "esc.mp4").write_bytes(b"\0" * _SOLO_BYTES)
    assert lib.library_path_for_completion(
        str(dl), os.path.join("..", "outside")) is None


def test_resolver_tie_break_is_independent_of_walk_order(clean_workdir,
                                                         monkeypatch):
    """Two equal-size media files: the answer must not depend on the order
    os.walk yields entries (which varies by filesystem). Tie-break is the
    lexicographically smallest path, asserted under a REVERSED walk."""
    _boot_db()
    from bulk_downloader import library as lib

    dl = clean_workdir / "dl"
    tie = dl / "Tie.Pack"
    tie.mkdir(parents=True)
    a = tie / "a_ep1.mp4"
    a.write_bytes(b"\0" * _SMALL_BYTES)
    (tie / "b_ep2.mp4").write_bytes(b"\0" * _SMALL_BYTES)

    first = lib.library_path_for_completion(str(dl), "Tie.Pack")

    real_walk = os.walk

    def reversed_walk(top, **kw):
        for dirpath, dirnames, filenames in real_walk(top, **kw):
            yield dirpath, list(reversed(dirnames)), list(reversed(filenames))

    monkeypatch.setattr(os, "walk", reversed_walk)
    second = lib.library_path_for_completion(str(dl), "Tie.Pack")

    assert first == second == str(a), (
        f"tie-break depends on walk order: forward={first}, "
        f"reversed={second}, expected {a}")
