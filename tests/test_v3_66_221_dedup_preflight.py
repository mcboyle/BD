"""v3.66.221 — F1.5 pre-download history-match dedup.

Source-true scope: F1.6 (per-job ETA/rate) and F1.7 (priority-high-first
selection) were ALREADY shipped in the tree; the only genuinely-new F1-C
piece is the pre-download history match. NEW db.db_find_url_in_history
(exact-URL) + SiteRunner._dedup_preflight hook -> status skipped_duplicate.

Custom runner: zero-arg functions, no pytest builtins. The preflight method
is exercised through a lightweight stub (config + log) so we test the gating
logic without booting a full SiteRunner.
"""

import logging
import os
import shutil
import tempfile

from bulk_downloader import db
from bulk_downloader.runner import SiteRunner

_preflight = SiteRunner._dedup_preflight  # unbound; call with a stub self


class _Stub:
    def __init__(self, config):
        self.config = config
        self.log = logging.getLogger("dedup_test")


# Row 429: the exact-URL gate now requires FILE-PRODUCED evidence -- a
# 'done' row attributed through history.library_id to a library file that is
# still on disk -- because two producers write 'done' rows for a page url
# having saved nothing at all (the GCW probe, and the no-download-dir click).
# The fixtures below therefore seed what a genuine download really writes: an
# ABSOLUTE path plus the file itself, which is what makes db_log's done path
# call library_record at all. A bare basename never produced a library row
# even before this cut, so these fixtures were describing a row shape no real
# download has; this is a fidelity fix, not a weakened assertion.
_TMPDIRS = []


def _fresh_db():
    from bulk_downloader.migrations import apply_pending

    db.db_init()
    result = apply_pending(backup_first=False)
    assert result["errors"] == 0, result
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")
        # library.file_path is UNIQUE, so rows must not bleed across tests.
        cx.execute("DELETE FROM library")
    while _TMPDIRS:
        shutil.rmtree(_TMPDIRS.pop(), ignore_errors=True)


def _seed_download(site_id, site_name, url, name, size):
    """Seed the history+library shape a REAL completed download writes."""
    d = tempfile.mkdtemp(prefix="bd-dedup-")
    _TMPDIRS.append(d)
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)
    db.db_log(site_id, site_name, url, "done", name, size, "",
              bytes_fetched=size, file_path=path)
    # Precondition: the evidence the gate reads really was recorded.
    with db.db_conn() as cx:
        row = cx.execute(
            "SELECT h.id AS hid, l.file_path AS fp FROM history h "
            "JOIN library l ON l.id = h.library_id "
            "WHERE h.url=? ORDER BY h.id DESC LIMIT 1", (url,)).fetchone()
    assert row is not None and row["fp"] == path, (
        f"fixture did not produce an attributed download row for {url}")
    assert os.path.isfile(path)
    return path


# ── db.db_find_url_in_history (F1.5 exact-URL) ───────────────────────

def test_exact_url_match_returns_prior_row():
    _fresh_db()
    _seed_download("s", "S", "https://ex.com/a", "a.mp4", 1000)
    hit = db.db_find_url_in_history("https://ex.com/a")
    assert hit and hit["filename"] == "a.mp4"
    assert hit["id"] >= 1


def test_exact_url_miss_returns_none():
    _fresh_db()
    _seed_download("s", "S", "https://ex.com/a", "a.mp4", 1000)
    assert db.db_find_url_in_history("https://ex.com/other") is None


def test_exact_url_only_matches_done_status():
    _fresh_db()
    # A failed/needs_review row for the URL must NOT count as a duplicate.
    db.db_log("s", "S", "https://ex.com/f", "failed", "", 0, "boom")
    assert db.db_find_url_in_history("https://ex.com/f") is None
    # ...and status is the ONLY reason it did not: the same URL with a real
    # completed download does match, so the None above is not being
    # manufactured by the row-shape requirement the Row 429 fix added.
    _seed_download("s", "S", "https://ex.com/f", "f.mp4", 1000)
    assert db.db_find_url_in_history("https://ex.com/f") is not None


def test_exact_url_empty_and_failsoft():
    _fresh_db()
    assert db.db_find_url_in_history("") is None
    assert db.db_find_url_in_history(None) is None


def test_exact_url_exclude_site():
    _fresh_db()
    _seed_download("siteA", "A", "https://ex.com/x", "x.mp4", 1000)
    assert db.db_find_url_in_history("https://ex.com/x", exclude_site="siteA") is None
    assert db.db_find_url_in_history("https://ex.com/x") is not None


# ── SiteRunner._dedup_preflight gating ───────────────────────────────

def test_preflight_exact_default_on():
    _fresh_db()
    _seed_download("s", "S", "https://ex.com/dup", "dup.mp4", 1000)
    stub = _Stub({})  # no flags -> exact dedup defaults ON
    msg = _preflight(stub, "https://ex.com/dup", {})
    assert msg and "history #" in msg


def test_preflight_no_match_proceeds():
    _fresh_db()
    stub = _Stub({})
    assert _preflight(stub, "https://ex.com/fresh", {}) is None


def test_preflight_force_download_bypasses():
    _fresh_db()
    _seed_download("s", "S", "https://ex.com/dup", "dup.mp4", 1000)
    stub = _Stub({})
    # Precondition: without the flag this row really does dedup, so the None
    # below is force_download and not an absent duplicate.
    assert _preflight(stub, "https://ex.com/dup", {})
    # An explicit Approve / re-download must not be skipped.
    assert _preflight(stub, "https://ex.com/dup", {"force_download": 1}) is None


def test_preflight_exact_can_be_disabled():
    _fresh_db()
    _seed_download("s", "S", "https://ex.com/dup", "dup.mp4", 1000)
    # Precondition: with the flag ON this row dedups, so the None below is
    # the disable and not an absent duplicate.
    assert _preflight(_Stub({}), "https://ex.com/dup", {})
    stub = _Stub({"dedup_exact_url": False})
    assert _preflight(stub, "https://ex.com/dup", {}) is None


def test_preflight_fuzzy_opt_in():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/orig", "done", "movie.mp4", 5000, "")
    stub = _Stub({"dedup_exact_url": False, "dedup_fuzzy": True})
    # Different URL, same filename + size -> fuzzy match (opt-in).
    msg = _preflight(stub, "https://other.com/copy", {"filename": "movie.mp4",
                                                       "file_size": 5000})
    assert msg and "history #" in msg


def test_preflight_fuzzy_off_by_default():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/orig", "done", "movie.mp4", 5000, "")
    stub = _Stub({"dedup_exact_url": False})  # fuzzy not enabled
    assert _preflight(stub, "https://other.com/copy", {"filename": "movie.mp4",
                                                       "file_size": 5000}) is None
