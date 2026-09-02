"""Row 544: the default-ON dedup preflight answers the ownership question itself.

THE DEFECT. ``SiteRunner._dedup_preflight`` (bulk_downloader/runner_integrity.py)
runs in ``_process_one`` (runner.py:3862) BEFORE anything opens the page, and its
exact-URL arm is default-ON -- ``self.config.get("dedup_exact_url", True)`` with
``dedup_exact_url`` absent from the shipped site config. It asked
``db_find_url_in_history``, whose whole test is

    SELECT ... FROM history WHERE url=? AND status='done' ORDER BY id DESC LIMIT 1

-- a status string and nothing about a transfer. Two consequences, both of them
the reason this row exists:

  1. THE SKIP ARM MANUFACTURES ITS OWN PROOF. runner_transport.py's "Already
     have" branch writes ``db_log(..., 'done', ..., bytes_fetched=0, 'already on
     disk')``. That row is 'done'. So one skip is enough to make every later run
     of the same URL answer "Duplicate of history #N" -- including a URL that
     produced no file at all. CLAUDE.md A7: do not derive the expected set from
     the artifact under test.

  2. ROW 479's HEADLINE ARM IS UNREACHABLE FOR A SAME-URL RE-RUN. ``db_skip_
     identity`` (db.py:1736) exists to name a fourth state, "unproven" -- a done
     row pointing at a file on disk with ``bytes_fetched`` 0 (certainly nothing
     transferred) or NULL (a pre-v8 row, unmeasurable). The needs_review row
     that surfaces it lives in ``_do_download``, and the preflight returns
     before ``_do_download`` is ever called.

THE CONTRACT. The preflight's ownership answer must come from the same evidence
``db_log`` names as the only column able to answer it. It skips only when SOME
'done' row for this URL records a REAL transfer.

WHY "SOME" AND NOT "THE NEWEST". ``db_skip_identity``'s own comment states the
healthy steady state: one real transfer followed by any number of
``bytes_fetched=0`` skip rows. Testing only the newest row -- which is what
``db_find_url_in_history``'s ``ORDER BY id DESC LIMIT 1`` hands back -- would
turn every legitimate second dedup into a full re-extract. Two tests below pin
that ordering explicitly so the fix cannot be satisfied by a newest-row check.

SCOPE NOTE. The transfer scan is done inside ``_dedup_preflight`` rather than by
calling ``db_skip_identity``, because that function refuses a caller with no
final path (``if not page_url or not final_path: return ("unknown", None)``) and
the preflight -- running before any filename template renders -- genuinely has
none. Relaxing that guard is a db.py change outside this cut's declared files.
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from bulk_downloader import db
from bulk_downloader import library as _library
from bulk_downloader import migrations as _migrations
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

# Unbound, called with a stub self: the preflight touches only self.config and
# self.log, so a full SiteRunner would add moving parts without adding subject.
_preflight = SiteRunner._dedup_preflight

_URL = "https://members.example.test/scene/row544"
_OTHER = "https://members.example.test/scene/row544-other"


class _Stub:
    """The exact surface ``_dedup_preflight`` touches, and nothing else."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.log = logging.getLogger("row544")


@pytest.fixture
def fresh_history(clean_workdir):
    """ROW 429: MIGRATED, so ``history.library_id`` exists.

    ``db_init`` alone builds the pre-migration shape. The exact-URL arm now
    also reads the file a row produced, through that column, so on the
    unmigrated shape every lookup would answer "not proven" and the row 544
    verdicts below would be indistinguishable from a schema accident.
    """
    db.db_init()
    result = _migrations.apply_pending(backup_first=False)
    assert result.get("errors", 1) == 0, result
    _library._ensure_schema()
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")
        cx.execute("DELETE FROM library")
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "library_id" in cols, sorted(cols)
    rows = _history()
    assert rows == [], f"the fixture did not start empty: {rows}"
    return clean_workdir


def _history(url=None):
    with db.db_conn() as cx:
        if url is None:
            cur = cx.execute(
                "SELECT id, url, status, bytes_fetched FROM history ORDER BY id")
        else:
            cur = cx.execute(
                "SELECT id, url, status, bytes_fetched FROM history "
                "WHERE url=? ORDER BY id", (url,))
        return [dict(r) for r in cur.fetchall()]


def _seed(url, *, bytes_fetched, filename="row544.mp4", size=4096,
          message="", site="row544site"):
    """ROW 429: the seeded row now also PRODUCES ITS FILE.

    Every row this file seeds means "a completion happened at this url", and a
    completion writes bytes and hands db_log an absolute path -- which is what
    makes ``library_record`` run and backfill ``history.library_id``. The old
    fixture recorded a bare basename, so no attribution existed and the file
    evidence row 429 added would have refused these rows for a reason this
    file is not about.

    It also makes every verdict below SHARPER. Row 544's rule is that a
    zero-transfer 'done' row is not ownership; with the file present and
    attributed, the file evidence PASSES and ``bytes_fetched`` is the only
    thing left that can refuse. A later refusal can no longer launder the
    result (CLAUDE.md A5).

    Rows sharing a filename share one library row on purpose -- ``file_path``
    is UNIQUE, and one transfer followed by skip rows at the same path is
    exactly the healthy steady state ``db_skip_identity`` describes.
    """
    target = (pathlib.Path.cwd() / filename).resolve()
    if not target.exists():
        target.write_bytes(b"\0" * size)
    db.db_log(site, "Row 544 Site", url, "done", target.name, size, message,
              bytes_fetched=bytes_fetched, file_path=str(target))
    return target


# ── The defect ──────────────────────────────────────────────────────────────

def test_a_skip_row_is_not_proof_that_the_url_was_ever_downloaded(fresh_history):
    """RED at base f993f654: the preflight returns "Duplicate of history #N".

    The seeded row is byte-for-byte the shape ``runner_transport``'s "Already
    have" branch writes -- status 'done', ``bytes_fetched=0``, message 'already
    on disk'. Nothing in the record says BD ever moved a byte for this URL.
    """
    _seed(_URL, bytes_fetched=0, message="already on disk")

    # PRECONDITIONS, asserted before any verdict.
    rows = _history(_URL)
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "done"
    assert rows[0]["bytes_fetched"] == 0, (
        "the fixture must seed a row that records NO transfer; it seeded "
        f"bytes_fetched={rows[0]['bytes_fetched']!r}")
    # The bare-status lookup the defect used still finds it: this proves the
    # test is discriminating between the two questions, not between two
    # different queries returning nothing.
    hit = db.db_find_url_in_history(_URL)
    assert hit is not None and hit["id"] == rows[0]["id"]

    stub = _Stub()
    assert "dedup_exact_url" not in stub.config, (
        "the shipped config leaves this key absent so the arm defaults ON; a "
        "fixture that sets it would not be measuring the live shape")

    msg = _preflight(stub, _URL, {})
    assert msg is None, (
        f"a zero-transfer 'done' row was accepted as proof of ownership: {msg!r}")


def test_a_pre_v8_null_row_is_not_proof_either(fresh_history):
    """The upgraded-host half of row 479's "unproven" state.

    ``bytes_fetched`` was added by migration 8; every row written before it is
    NULL. db_log's own contract says NULL is "UNKNOWN, and never proof of a
    download", and CLAUDE.md A2 says UNKNOWN is never permission.
    """
    _seed(_URL, bytes_fetched=None)

    rows = _history(_URL)
    assert len(rows) == 1 and rows[0]["status"] == "done"
    assert rows[0]["bytes_fetched"] is None, (
        f"the fixture must seed a NULL transfer column; got {rows[0]!r}")

    assert _preflight(_Stub(), _URL, {}) is None, (
        "a pre-v8 NULL transfer column was read as proof of a download")


# ── Negative controls: the guard was not removed, and not narrowed to the
#    newest row either ────────────────────────────────────────────────────────

def test_a_real_transfer_still_dedups(fresh_history):
    """NEGATIVE CONTROL. The fix must not switch exact-URL dedup off.

    Without this, deleting the arm entirely would pass every test above.
    """
    _seed(_URL, bytes_fetched=4096, filename="real.mp4")

    rows = _history(_URL)
    assert len(rows) == 1 and rows[0]["bytes_fetched"] == 4096, rows

    msg = _preflight(_Stub(), _URL, {})
    assert msg is not None, "a genuine prior download is no longer deduplicated"
    assert "history #" in msg, msg
    assert str(rows[0]["id"]) in msg, (
        f"the message must name the row it matched; got {msg!r}")
    assert "real.mp4" in msg, msg


def test_the_steady_state_of_one_transfer_then_skips_still_dedups(fresh_history):
    """NEGATIVE CONTROL for the ORDERING trap, and the reason the scan is
    over every row rather than the newest one.

    ``db_find_url_in_history`` orders ``id DESC LIMIT 1``, so the newest row for
    a healthy URL is a ``bytes_fetched=0`` skip row, not the transfer. A fix
    that tested only that row would re-download every file BD has ever skipped.
    """
    _seed(_URL, bytes_fetched=8192, filename="real.mp4")
    _seed(_URL, bytes_fetched=0, filename="real.mp4", message="already on disk")
    _seed(_URL, bytes_fetched=0, filename="real.mp4", message="already on disk")

    rows = _history(_URL)
    assert len(rows) == 3, rows
    assert [r["bytes_fetched"] for r in rows] == [8192, 0, 0], rows
    newest = db.db_find_url_in_history(_URL)
    assert newest["id"] == rows[-1]["id"], (
        "precondition: the NEWEST done row must be one of the zero-transfer "
        "skip rows, or this test is not exercising the ordering trap")

    msg = _preflight(_Stub(), _URL, {})
    assert msg is not None, (
        "the transfer scan stopped at the newest row: a URL with a real "
        "transfer followed by two skips is no longer recognised as a duplicate")
    assert "history #" in msg, msg


def test_the_scan_is_keyed_on_this_url(fresh_history):
    """NEGATIVE CONTROL. Another URL's real transfer must not license a skip.

    A scan written without the url predicate -- "is there ANY row with
    bytes_fetched>0" -- would pass every other test in this file.
    """
    _seed(_OTHER, bytes_fetched=99999, filename="somebody-elses.mp4")
    _seed(_URL, bytes_fetched=0, message="already on disk")

    rows = _history()
    assert len(rows) == 2, rows
    assert {r["url"] for r in rows} == {_URL, _OTHER}
    assert max(r["bytes_fetched"] for r in rows) == 99999, rows

    assert _preflight(_Stub(), _URL, {}) is None, (
        "a different URL's transfer was accepted as proof for this one")


def test_force_download_still_bypasses_and_the_arm_is_still_switchable(
    fresh_history,
):
    """NEGATIVE CONTROL for the two gates that were already there.

    The fix must not have been obtained by moving the early returns.
    """
    _seed(_URL, bytes_fetched=8192, filename="real.mp4")
    assert _preflight(_Stub(), _URL, {}) is not None  # precondition: it dedups

    assert _preflight(_Stub(), _URL, {"force_download": True}) is None, (
        "Approve no longer bypasses the preflight")
    assert _preflight(_Stub({"dedup_exact_url": False}), _URL, {}) is None, (
        "dedup_exact_url=False no longer switches the exact-URL arm off")


def test_the_fuzzy_arm_is_untouched_and_still_opt_in(fresh_history):
    """NEGATIVE CONTROL: the opt-in filename arm keeps its own semantics.

    The fuzzy arm matches a DIFFERENT url's basename+size, so the per-url
    transfer scan above cannot be its gate. Pinning it here keeps a future
    'tighten everything' edit from silently disabling it.
    """
    _seed(_OTHER, bytes_fetched=4096, filename="shared-name.mp4", size=4096)

    job = {"filename": "shared-name.mp4", "file_size": 4096}
    assert _preflight(_Stub(), _URL, job) is None, (
        "the fuzzy arm fired while opt-out (default off)")

    msg = _preflight(_Stub({"dedup_fuzzy": True}), _URL, job)
    assert msg is not None and "Likely duplicate" in msg, msg


def test_the_preflight_is_still_fail_soft(fresh_history, monkeypatch):
    """NEGATIVE CONTROL: a lookup failure must never block a download.

    The docstring's fail-soft contract has to survive the new query, so this
    breaks the new scan's transport and asserts the answer is None (proceed),
    not an exception and not a skip.
    """
    import bulk_downloader.db as _db

    _seed(_URL, bytes_fetched=8192)
    assert _preflight(_Stub(), _URL, {}) is not None  # precondition: it dedups

    boom = {"n": 0}

    def _explode(*a, **k):
        boom["n"] += 1
        raise RuntimeError("row544: history is unreadable")

    monkeypatch.setattr(_db, "db_conn", _explode)

    assert _preflight(_Stub(), _URL, {}) is None
    assert boom["n"] >= 1, (
        "the broken transport was never reached, so nothing was proven about "
        "fail-soft behaviour")
