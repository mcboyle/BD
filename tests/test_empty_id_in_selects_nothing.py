"""An explicitly-empty `id_in` matches EVERY row instead of none.

THE DEFECT. `bulk_downloader/batch_ops.py:57` opens with

    if id_in:

so an `id_in` that was SUPPLIED BUT EMPTY is indistinguishable from one that was
never supplied at all. No `id IN (...)` clause is appended, `where` stays empty,
and the composed SQL degenerates to an unfiltered

    SELECT * FROM history ORDER BY id DESC LIMIT 500

Measured over HTTP on the deploy box:

    POST /api/batch/delete {"filter":{"id_in":[]},"dry_run":false}
      -> 200, processed: 7, history 7 -> 0, INCLUDING organic rows

"Select nothing" and "select everything" are the two most different answers the
function can give, and the one it gives is the destructive one.

WHY THE DENOMINATOR IS THREE OPERATIONS, NOT ONE. `_matching_rows` is shared, so
the same empty list drives all of them:

    bulk_delete  -> DELETE FROM history for every row (and with
                    delete_files=True, os.unlink on every file)
    bulk_retry   -> UPDATE every row back to 'pending', re-queueing the
                    operator's entire download history
    bulk_move    -> relocate every file on disk to target_dir

A test that pinned only `bulk_delete` would pass forever while `bulk_retry` kept
re-queueing everything -- a gate whose denominator structurally excludes two
thirds of its subject. So every destructive consumer is asserted below, and the
`_build_query` layer is asserted separately, because a fix applied at one call
site rather than in the shared composer would leave the others live.

WHY NOT JUST `if id_in is not None and not id_in: return no rows` AT THE CALLER.
Because the endpoint stays live for the next caller. tools/live_seed.py's
teardown is about to become the first caller that can legitimately hand over an
empty list (a seeded run with no history rows to remove), and the guard has to
hold for whatever calls it after that.

THE DISTINCTION THAT MUST SURVIVE. An ABSENT `id_in` -- the whole-table filter
behind "delete everything older than 90 days" -- is a legitimate, documented
operation and must keep working. The fix separates "not supplied" (None, or the
key absent) from "supplied and empty" ([]). A fix that made both match zero rows
would break real functionality while passing a naive version of this test, so
that case is pinned too.

RED-first: every assertion below fails on pristine source.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def history_db(tmp_path, monkeypatch):
    """An isolated history DB seeded with rows that must not be touched.

    Isolation is by ABSOLUTE DB_PATH -- resolution order 1 in
    `db._resolve_db_path`. Deterministic by construction: it does not depend on
    cwd, on BD_INSTALL_DIR, or on whether this module's import beat conftest's
    chdir. BD_HOME is deliberately not used -- `_resolve_db_path` never consults
    it, so a test that set BD_HOME would silently operate on the repository's
    own downloader_history.db, which is gitignored and would therefore leave
    `git status` clean while the check could not see its own subject.
    """
    from bulk_downloader import db as _db

    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "history.db"))
    _db.db_init()
    for i in range(5):
        _db.db_log(f"site{i}", f"Site {i}", f"https://example.invalid/{i}",
                   "done", f"file{i}.mp4", 1024)
    with _db.db_conn() as cx:
        assert cx.execute("SELECT count(*) FROM history").fetchone()[0] == 5
    return _db


def _row_count(_db) -> int:
    with _db.db_conn() as cx:
        return cx.execute("SELECT count(*) FROM history").fetchone()[0]


# ── the composer, where the defect lives ─────────────────────────────────────

def _execute(_db, sql, params) -> int:
    """Run a composed query and return the row count.

    Deliberately NOT via `_matching_rows`, whose blanket `except Exception:
    return []` cannot be distinguished from a correctly-composed empty
    selection. Executing directly is what makes the difference observable.
    """
    with _db.db_conn() as cx:
        return len(cx.execute(sql, params).fetchall())


def test_an_empty_id_in_selects_zero_rows(history_db):
    """The narrowest statement of the bug.

    Forces the condition rather than awaiting it: the list is supplied and
    empty, which is the only input that distinguishes the two behaviours.

    Asserts over EXECUTED ROWS, not over the composed SQL text. An earlier
    version of this test asserted `"WHERE" in sql`, and mutation showed that
    passes for `WHERE 1 = 1` and for `WHERE id IN ()` -- both of which still
    select everything or crash. A predicate on the query string is a predicate
    on prose; the subject is what the query returns.
    """
    from bulk_downloader import batch_ops

    sql, params = batch_ops._build_query(id_in=[])
    assert _execute(history_db, sql, params) == 0, (
        f"_build_query(id_in=[]) composed {sql!r}, which selects rows from a "
        f"table of 5. An explicitly supplied empty selection must match zero "
        f"rows, not every row."
    )


def test_the_empty_selection_is_valid_sql_not_a_swallowed_error(history_db):
    """Zero rows must come from the FILTER, not from a caught exception.

    `id IN ()` is a SQLite syntax error. Composed that way, `_matching_rows`
    swallows it and returns [] -- the right answer by the wrong mechanism, and
    one that silently becomes a data-loss bug again the moment that blanket
    except is narrowed. Executing outside the try is the only way to see it.
    """
    from bulk_downloader import batch_ops

    sql, params = batch_ops._build_query(id_in=[])
    try:
        _execute(history_db, sql, params)
    except Exception as e:
        raise AssertionError(
            f"_build_query(id_in=[]) composed SQL that does not execute: "
            f"{type(e).__name__}: {e}\n  sql={sql!r}\n"
            f"_matching_rows would swallow this and return [], so the empty "
            f"selection would appear to work while resting on a crash."
        ) from None


def test_an_absent_id_in_still_selects_the_whole_table(history_db):
    """The behaviour that must NOT change.

    'Delete everything older than 90 days' is a real operation. A fix that
    conflated absent with empty would break it -- and mutation proved a
    string-shaped version of this canary (`assert "id IN" not in sql`) passes
    against exactly that mutant, because `WHERE 1 = 0` contains no `id IN`.
    """
    from bulk_downloader import batch_ops

    sql, params = batch_ops._build_query(status="done")
    assert _execute(history_db, sql, params) == 5, (
        f"_build_query(status='done') composed {sql!r}, which selected "
        f"{_execute(history_db, sql, params)} of 5 seeded rows. Omitting "
        f"id_in must leave the selection unrestricted by id -- only an "
        f"explicitly supplied empty list means 'nothing'."
    )


# ── every destructive consumer, because they share _matching_rows ────────────

def test_empty_id_in_deletes_nothing(history_db):
    from bulk_downloader import batch_ops

    out = batch_ops.bulk_delete({"id_in": []}, dry_run=False)
    assert _row_count(history_db) == 5, (
        f"bulk_delete(id_in=[]) removed "
        f"{5 - _row_count(history_db)} of 5 rows and reported "
        f"processed={out.get('processed')}. An empty selection deleted the "
        f"operator's history."
    )
    assert out.get("candidates_matched") == 0, (
        f"bulk_delete(id_in=[]) matched {out.get('candidates_matched')} "
        f"candidates for an empty selection."
    )


def test_empty_id_in_deletes_no_files(history_db, tmp_path):
    """The worst case: delete_files unlinks from disk, which no undo covers."""
    from bulk_downloader import batch_ops

    victim = tmp_path / "precious.mp4"
    victim.write_bytes(b"x" * 16)
    with history_db.db_conn() as cx:
        cx.execute("UPDATE history SET filename = ? WHERE id = 1", (str(victim),))

    batch_ops.bulk_delete({"id_in": []}, dry_run=False, delete_files=True)
    assert victim.exists(), (
        "bulk_delete(id_in=[], delete_files=True) unlinked a file on disk for "
        "an empty selection. There is no undo for that path."
    )


def test_empty_id_in_requeues_nothing(history_db):
    """Same composer, different consumer -- the one a delete-only gate misses."""
    from bulk_downloader import batch_ops

    batch_ops.bulk_retry({"id_in": []}, dry_run=False)
    with history_db.db_conn() as cx:
        pending = cx.execute(
            "SELECT count(*) FROM history WHERE status = 'pending'").fetchone()[0]
    assert pending == 0, (
        f"bulk_retry(id_in=[]) reset {pending} of 5 rows to 'pending' for an "
        f"empty selection, re-queueing the operator's entire history."
    )


def test_empty_id_in_moves_nothing(history_db, tmp_path):
    from bulk_downloader import batch_ops

    src = tmp_path / "stay.mp4"
    src.write_bytes(b"y" * 16)
    dest = tmp_path / "dest"
    dest.mkdir()
    with history_db.db_conn() as cx:
        cx.execute("UPDATE history SET filename = ? WHERE id = 1", (str(src),))

    batch_ops.bulk_move({"id_in": []}, target_dir=str(dest), dry_run=False)
    assert src.exists() and not (dest / "stay.mp4").exists(), (
        "bulk_move(id_in=[]) relocated a file for an empty selection."
    )


# ── the preview lies before the apply does ───────────────────────────────────

def test_the_dry_run_preview_does_not_offer_the_whole_table(history_db):
    """The operator's last line of defence.

    BatchOps.tsx shows a dry-run preview and requires a typed confirmation
    before applying. If the preview itself reports the whole table for an empty
    selection, the confirmation ritual is confirming the wrong number -- and a
    fix applied only to the apply path would leave the operator being shown a
    destructive preview that no longer matches what runs.
    """
    from bulk_downloader import batch_ops

    out = batch_ops.bulk_delete({"id_in": []}, dry_run=True)
    assert out.get("candidates_matched") == 0, (
        f"the dry-run preview for an empty selection reported "
        f"{out.get('candidates_matched')} candidates. The operator would type "
        f"a confirmation against a count that describes their entire history."
    )
