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

from bulk_downloader import db
from bulk_downloader.runner import SiteRunner

_preflight = SiteRunner._dedup_preflight  # unbound; call with a stub self


class _Stub:
    def __init__(self, config):
        self.config = config
        self.log = logging.getLogger("dedup_test")


def _fresh_db():
    db.db_init()
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")


# ── db.db_find_url_in_history (F1.5 exact-URL) ───────────────────────

def test_exact_url_match_returns_prior_row():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/a", "done", "a.mp4", 1000, "")
    hit = db.db_find_url_in_history("https://ex.com/a")
    assert hit and hit["filename"] == "a.mp4"
    assert hit["id"] >= 1


def test_exact_url_miss_returns_none():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/a", "done", "a.mp4", 1000, "")
    assert db.db_find_url_in_history("https://ex.com/other") is None


def test_exact_url_only_matches_done_status():
    _fresh_db()
    # A failed/needs_review row for the URL must NOT count as a duplicate.
    db.db_log("s", "S", "https://ex.com/f", "failed", "", 0, "boom")
    assert db.db_find_url_in_history("https://ex.com/f") is None


def test_exact_url_empty_and_failsoft():
    _fresh_db()
    assert db.db_find_url_in_history("") is None
    assert db.db_find_url_in_history(None) is None


def test_exact_url_exclude_site():
    _fresh_db()
    db.db_log("siteA", "A", "https://ex.com/x", "done", "x.mp4", 1000, "")
    assert db.db_find_url_in_history("https://ex.com/x", exclude_site="siteA") is None
    assert db.db_find_url_in_history("https://ex.com/x") is not None


# ── SiteRunner._dedup_preflight gating ───────────────────────────────

def test_preflight_exact_default_on():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/dup", "done", "dup.mp4", 1000, "")
    stub = _Stub({})  # no flags -> exact dedup defaults ON
    msg = _preflight(stub, "https://ex.com/dup", {})
    assert msg and "history #" in msg


def test_preflight_no_match_proceeds():
    _fresh_db()
    stub = _Stub({})
    assert _preflight(stub, "https://ex.com/fresh", {}) is None


def test_preflight_force_download_bypasses():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/dup", "done", "dup.mp4", 1000, "")
    stub = _Stub({})
    # An explicit Approve / re-download must not be skipped.
    assert _preflight(stub, "https://ex.com/dup", {"force_download": 1}) is None


def test_preflight_exact_can_be_disabled():
    _fresh_db()
    db.db_log("s", "S", "https://ex.com/dup", "done", "dup.mp4", 1000, "")
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
