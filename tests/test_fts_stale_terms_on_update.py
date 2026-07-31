"""Updating an FTS-indexed column leaves the OLD terms in the index forever.

THE DEFECT, measured on a temp database at v3.66.821:

    baseline      search 'ORIGINALNAME' -> ['ORIGINALNAME.mp4']
    after rename  search 'ORIGINALNAME' -> ['RENAMED.mp4']    <- wrong row returned
    after rename  search 'RENAMED'      -> []                 <- current name not findable
    after requeue search 'first'        -> ['RENAMED.mp4']    <- cleared message still matches

Both directions are wrong, which is what separates this from #39a. That cut
made the two DELETERS maintain the index, and its finding was bloat: orphaned
docs cannot surface in search, because the search join is INNER and history's
id is AUTOINCREMENT so a freed rowid is never reused. Nothing about that
argument applies here. These rows are LIVE, the join finds them, and the terms
the index holds are terms the row no longer has.

history_fts is FTS5 EXTERNAL-CONTENT (content='history', content_rowid='id'),
so SQLite maintains NOTHING: on UPDATE of an indexed column the application
must issue the FTS5 'delete' command with the row's OLD values and re-insert
the new ones. db_fts_forget already exists for the delete half and its own
docstring names this condition -- `remaining` means "the index holds terms
nobody remembers because an FTS-indexed column was updated in place".

THE THREE PATHS, derived at decision time rather than quoted. Predicate: a
string constant matching `UPDATE history SET <clause>` where <clause> assigns
an indexed column. status is UNINDEXED and does not count; library_id and
retention_excluded are not in the index at all.

    batch_ops.py:232          SET filename = ?               (bulk_move)
    batch_ops.py:138          SET status = ?, message = ''   (bulk_retry)
    storage_rebalance.py:226  SET filename = ?               (execute_plan)

A first derivation matched `(\\w+)\\s*=\\s*\\?` and MISSED batch_ops.py:138,
whose `message = ''` is a literal assignment rather than a parameter. The
predicate has to read the SET clause, not the parameter markers.

ORDERING IS THE WHOLE DIFFICULTY. The 'delete' command needs the values the
row had BEFORE the update; issued afterwards it removes nothing and silently
leaves the stale terms while reporting success. Every test below therefore
drives the REAL shipped function rather than a reimplementation, so a fix that
captures the snapshot at the wrong moment fails here.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bulk_downloader import batch_ops
from bulk_downloader import db


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed(site_name="Acme Studio", url="https://a.example/1",
          filename="ORIGINALNAME.mp4", message="alpha marker text",
          status="done") -> int:
    """One indexed history row, index in sync. Returns its id."""
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id, site_name, url, status, filename, message) "
            "VALUES(?,?,?,?,?,?)",
            ("siteA", site_name, url, status, filename, message))
        rid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
        cx.execute(
            "INSERT INTO history_fts(rowid, site_name, url, filename, message, status) "
            "SELECT id, COALESCE(site_name,''), COALESCE(url,''), "
            "COALESCE(filename,''), COALESCE(message,''), COALESCE(status,'') "
            "FROM history WHERE id = ?", (rid,))
    return rid


def _search(term: str) -> list[int]:
    """Row ids the FTS index returns for `term`, through the shipped join."""
    with db.db_conn() as cx:
        return [r[0] for r in cx.execute(
            "SELECT h.id FROM history_fts JOIN history h ON h.id = history_fts.rowid "
            "WHERE history_fts MATCH ?", (term,)).fetchall()]


# ── RED: the three update paths ──────────────────────────────────────────────

def test_bulk_move_does_not_leave_the_old_filename_searchable(clean_workdir, tmp_path):
    """batch_ops.bulk_move: the old filename must stop matching, the new one start.

    Drives the shipped function with a real file on disk, because the update is
    reached only after shutil.move succeeds.
    """
    db.db_init()
    # The DIRECTORY carries the distinguishing token, not the basename:
    # bulk_move keeps the basename (os.path.join(target, basename(src))), so
    # asserting the basename stops matching would assert something false. The
    # tokenizer splits on '/' and '.' (separators '-_./:?#&='), so each path
    # segment is its own term.
    src_dir = tmp_path / "OLDPLACEALPHA"; src_dir.mkdir()
    dst_dir = tmp_path / "NEWPLACEBETA"; dst_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"x" * 16)
    rid = _seed(filename=str(src))

    assert _search("OLDPLACEALPHA") == [rid], "fixture: index must start in sync"
    assert _search("NEWPLACEBETA") == [], "fixture: target must not match yet"

    out = batch_ops.bulk_move({"id_in": [rid]}, target_dir=str(dst_dir),
                              dry_run=False)
    assert out.get("processed") == 1, f"bulk_move did not move the row: {out}"

    assert _search("OLDPLACEALPHA") == [], (
        "the OLD path still matches after bulk_move -- searching a value the "
        "row no longer has returns that row")
    assert _search("NEWPLACEBETA") == [rid], (
        "the row is not findable by its CURRENT path -- the index was left "
        "pointing at where the file used to be")


def test_bulk_retry_does_not_leave_the_cleared_message_searchable(clean_workdir):
    """batch_ops.bulk_retry sets message = '' -- an INDEXED column.

    This is the path the first derivation missed: `message = ''` is a literal
    assignment, so a predicate keyed on `= ?` does not see it.
    """
    db.db_init()
    rid = _seed(message="alpha marker text", status="error")
    assert _search("alpha") == [rid], "fixture: index must start in sync"

    out = batch_ops.bulk_retry({"id_in": [rid]}, dry_run=False)
    assert out.get("processed") == 1, f"bulk_retry did not touch the row: {out}"

    with db.db_conn() as cx:
        msg = cx.execute("SELECT message FROM history WHERE id = ?",
                         (rid,)).fetchone()[0]
    assert msg == "", f"fixture: bulk_retry should have cleared message, got {msg!r}"

    assert _search("alpha") == [], (
        "the CLEARED message still matches -- the row has no message at all "
        "now, yet searching its old text returns it")


def test_storage_rebalance_does_not_leave_the_old_filename_searchable(
        clean_workdir, tmp_path):
    """storage_rebalance.execute_plan: same UPDATE, third call site.

    Banded separately from bulk_move because it is a different module with its
    own connection handling; a fix applied to batch_ops only would pass the
    first test and fail this one.
    """
    from bulk_downloader import storage_rebalance

    db.db_init()
    src_dir = tmp_path / "DISKONEGAMMA"; src_dir.mkdir()
    dst_dir = tmp_path / "DISKTWODELTA"; dst_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"y" * 16)
    rid = _seed(filename=str(src))
    assert _search("DISKONEGAMMA") == [rid], "fixture: index must start in sync"

    # execute_plan reads "from"/"to", not "src"/"dst" -- a plan with the wrong
    # keys is SKIPPED, not moved, and the test would then pass for the wrong
    # reason on a broken tree.
    plan = {"moves": [{"history_id": rid, "from": str(src),
                       "to": str(dst_dir / "clip.mp4"),
                       "size_bytes": 16}]}
    out = storage_rebalance.execute_plan(plan, dry_run=False)
    assert out.get("moved") == 1, f"execute_plan did not move the row: {out}"

    assert _search("DISKONEGAMMA") == [], (
        "the OLD disk path still matches after a rebalance move")
    assert _search("DISKTWODELTA") == [rid], (
        "the row is not findable on the disk it now lives on")


# ── cry-wolf floors: green now, and they must stay green ────────────────────

def test_an_update_to_a_non_indexed_column_leaves_the_index_alone(clean_workdir):
    """file_size and transfer_mode are not in the index.

    Re-syncing on every UPDATE would be correct but wasteful, and worse, it
    would make the maintenance counters move on rows nothing changed about --
    noise that trains an operator to ignore them. These columns must not
    trigger any index work, and the row must stay findable throughout.

    Both are in history's BASE schema. library_id and retention_excluded are
    deliberately NOT used, though they are the real non-indexed updaters:
    library.py and retention.py each lazy-add their column in their own
    _ensure_tables(), so naming either would make this floor depend on which
    module happened to run first rather than on the property under test. Two
    fixture corrections here were exactly that mistake.
    """
    db.db_init()
    rid = _seed(message="beta marker text")
    assert _search("beta") == [rid]

    with db.db_conn() as cx:
        cx.execute("UPDATE history SET file_size = ? WHERE id = ?", (4096, rid))
        cx.execute("UPDATE history SET transfer_mode = ? WHERE id = ?",
                   ("stream", rid))

    assert _search("beta") == [rid], (
        "a non-indexed update disturbed the index; the row stopped matching a "
        "term it still has")


def test_a_neighbour_row_is_untouched_by_another_rows_resync(clean_workdir, tmp_path):
    """Maintenance must be scoped to the rows that changed.

    The failure this pins is a resync that rebuilds or over-deletes: neighbour
    rows keep their own terms and stay findable.
    """
    db.db_init()
    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst_dir = tmp_path / "dst"; dst_dir.mkdir()
    moved = src_dir / "MOVEDONE.mp4"; moved.write_bytes(b"z" * 8)  # noqa: E702

    rid_move = _seed(filename=str(moved), message="gamma one")
    rid_keep = _seed(filename=str(src_dir / "UNTOUCHED.mp4"),
                     message="delta two", url="https://a.example/2")

    batch_ops.bulk_move({"id_in": [rid_move]}, target_dir=str(dst_dir),
                        dry_run=False)

    assert _search("delta") == [rid_keep], (
        "the neighbour row lost its terms when another row was re-synced")
    assert _search("UNTOUCHED") == [rid_keep], (
        "the neighbour row's filename stopped matching")


def test_resync_on_a_row_the_index_never_held_is_not_an_error(clean_workdir, tmp_path):
    """A desynced database must not turn a move into a 500.

    #39a settled this for the deleters -- 'delete' for a doc the index does not
    hold raises DatabaseError, and membership is DERIVED from the index rather
    than inferred from an exception class. The same must hold here: a history
    row that was never indexed (e.g. inserted while FTS5 was unavailable) is
    skipped, not an error.
    """
    db.db_init()
    src_dir = tmp_path / "src"; src_dir.mkdir()
    dst_dir = tmp_path / "dst"; dst_dir.mkdir()
    orphan = src_dir / "NEVERINDEXED.mp4"; orphan.write_bytes(b"q" * 8)

    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id, site_name, url, status, filename, message) "
            "VALUES(?,?,?,?,?,?)",
            ("siteA", "Acme", "https://a.example/9", "done",
             str(orphan), "epsilon never indexed"))
        rid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
        # deliberately NOT inserted into history_fts

    out = batch_ops.bulk_move({"id_in": [rid]}, target_dir=str(dst_dir),
                              dry_run=False)
    assert out.get("errors") == 0, (
        f"moving a row the index never held was reported as an error: {out}")
    assert out.get("processed") == 1, (
        f"the move itself did not happen: {out}")
