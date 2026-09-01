"""A prune repairs only the links it broke, and only back to the url that held them.

THE QUESTION THIS FILE DECIDES.  ``db_prune`` (bulk_downloader/db.py) deletes
history rows, and a deleted row leaves ``library.history_id`` naming nothing.
``db_skip_identity`` refuses to read such a row -- "A DANGLING CURRENT LINK IS
NOT PERMISSION" -- so the prune repairs the link it breaks.  This file decides
the SCOPE of that repair, which is a different question from whether it happens.

WHAT SHIPPED IN v3.66.1404.  The repair UPDATE was gated on three predicates and
no more: ``history_id IS NOT NULL``, ``NOT EXISTS(current owner)``, and
``EXISTS(any history row carrying this library_id)``.  It therefore had

  * no restriction to links THIS prune broke -- a library row left dangling by
    ``batch_ops.bulk_delete`` (POST /api/batch/delete) was "repaired" too, and
  * no url constraint -- ``MAX(h.id)`` selected the newest surviving row at that
    path REGARDLESS OF WHICH URL WROTE IT, and
  * no status constraint -- any row carrying the library id would do.

The code's own comment claimed the repair applied "the same rule library_record
applies on every write" and that "db_prune repairs the link it breaks itself;
nothing else may assume it was repaired".  Neither was true of what it did.

THE COST, MEASURED, NOT ARGUED.  ``test_a_prune_that_deletes_nothing_repoints_
a_link_it_did_not_break`` below reproduces the reviewed probe verbatim: url A
fetched /shared.mp4, url B later overwrote it, the operator deleted B's row
through the batch API, and a prune that removes ZERO rows then hands the library
row to A.  ``db_skip_identity(A)`` flips from ("unknown", None) -- the deliberate
answer -- to ("same", path).  A skips over B's bytes, reports "Already have",
writes a done row, and ``library_record``'s

    title = CASE WHEN ?<>'' THEN ? ELSE title END

retitles B's library row to A's title.  Wrong file, right title: the shape
CLAUDE.md A7 records for 2026-08-29, arriving through the prune door.

THE RULE THIS FILE PINS.  For each library row whose CURRENT OWNER this prune is
about to delete -- and only those -- repoint to the newest surviving ``done`` row
OF THE URL THAT OWNED IT.  When that url has no surviving done row, the link
STAYS DANGLING.  Dangling is a legitimate terminal state, not a hole to be
filled: ``db_skip_identity`` answers "unknown" for it, and "unknown" is never
permission (CLAUDE.md A2).  Repointing to SOMETHING is what this defect cost.

SECOND SUBJECT, SAME CUT AND SAME FUNCTION FAMILY: ``_transfer_proof_sql``'s
degraded arm.  When ``bytes_fetched`` is absent it returned
``_TRANSFER_PROOF_NO_MODE``, a predicate that NAMES ``h.bytes_fetched`` -- the
very column whose absence selected it.  Executing it raises ``no such column``,
which ``db_skip_identity``'s bare handler converts into ("unknown", None) for
EVERY url (verbatim the failure that degradation exists to prevent) and which
``db_prune`` does not catch at all, so POST /api/history/prune 500s.  The
missing-``bytes_fetched`` half needs its own predicate: without that column
nothing in the record can measure a transfer, so the honest predicate is FALSE,
not a reference to a column that is not there.

SCOPE.  This narrows a repair.  It does not revisit row 544's shipped rule or
row 607's discriminator, and the controls below assert both still hold.
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from bulk_downloader import db

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE = "pruneslink"
_SITE_NAME = "Prune Link Site"
# Two DIFFERENT works that render one filename -- the population the whole
# row 544/547/607 family exists for.
_URL_A = "https://members.example.test/scene/prune-link-a"
_URL_B = "https://members.example.test/scene/prune-link-b"


@pytest.fixture
def fresh(clean_workdir):
    from bulk_downloader import library as _library
    from bulk_downloader import migrations as _migrations

    db.db_init()
    result = _migrations.apply_pending(backup_first=False)
    assert result["errors"] == 0, result
    _library._SCHEMA_READY = False
    _library._ensure_schema()
    with db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
        assert {"bytes_fetched", "transfer_mode", "library_id"} <= cols, (
            f"the fixture did not build the schema this file measures: {cols}")
        cx.execute("DELETE FROM history")
        cx.execute("DELETE FROM library")
    assert _history() == [], "the fixture did not start empty"
    assert _library_rows() == [], "the fixture did not start empty"
    return clean_workdir


# ── measurement helpers ────────────────────────────────────────────────────

def _history(url=None):
    with db.db_conn() as cx:
        sql = ("SELECT id, url, status, bytes_fetched, transfer_mode, "
               "library_id FROM history")
        params = ()
        if url is not None:
            sql += " WHERE url=?"
            params = (url,)
        sql += " ORDER BY id"
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def _library_rows():
    with db.db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, file_path, history_id, title FROM library "
            "ORDER BY id").fetchall()]


def _make_file(workdir, name, payload=b"prune-link-payload"):
    p = pathlib.Path(workdir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _log_real_transfer(path, url, moved, title):
    """runner_transport.py's success db_log after a transport really ran."""
    db.db_log(_SITE, _SITE_NAME, url, "done", path.name, path.stat().st_size,
              "", bytes_fetched=moved, transfer_mode="http",
              file_path=str(path), title=title, title_source="page")


def _log_no_transport_done(path, url, title, message="already on disk"):
    """A 'done' row no transport produced.

    Two shipped arms write exactly this shape and neither passes
    ``transfer_mode``: runner_transport.py's "Already have" arm, and
    runner_integrations.py's Stash-dedup hit.  db.py's own comment names both.
    It is a real completion record that proves no transfer, which is why row 544
    refuses to read it as ownership.
    """
    db.db_log(_SITE, _SITE_NAME, url, "done", path.name, path.stat().st_size,
              message, bytes_fetched=0,
              file_path=str(path), title=title, title_source="page")


def _age(url, days=90):
    with db.db_conn() as cx:
        return cx.execute(
            "UPDATE history SET ts = datetime('now', ?) WHERE url = ?",
            (f"-{days} days", url)).rowcount


def _batch_delete(history_id):
    """The write ``batch_ops.bulk_delete`` performs at POST /api/batch/delete:
    a history DELETE that does not touch ``library``."""
    with db.db_conn() as cx:
        return cx.execute("DELETE FROM history WHERE id = ?",
                          (history_id,)).rowcount


# ── F07: the repair must not cross urls, and must not fire at all on a link
#         this prune did not break ────────────────────────────────────────────

def test_a_prune_that_deletes_nothing_repoints_a_link_it_did_not_break(fresh):
    """THE REVIEWED PROBE, reproduced.  A prune that removes ZERO rows must
    change nothing at all -- and above all must not convert the deliberate
    "unknown" of an out-of-band delete into permission for a DIFFERENT url."""
    p = _make_file(fresh, "shared.mp4")
    _log_real_transfer(p, _URL_A, 1000, "A scene")
    _log_real_transfer(p, _URL_B, 2000, "B scene")

    # PRECONDITIONS, measured. Two history rows, one library row, B is current.
    hist = _history()
    assert [r["url"] for r in hist] == [_URL_A, _URL_B], hist
    a_id, b_id = hist[0]["id"], hist[1]["id"]
    assert hist[0]["bytes_fetched"] == 1000 and hist[1]["bytes_fetched"] == 2000
    lib = _library_rows()
    assert len(lib) == 1 and lib[0]["file_path"] == str(p), lib
    assert lib[0]["history_id"] == b_id, (
        "precondition: B's row must be the CURRENT owner, or this test is not "
        "measuring a cross-url repoint")
    assert lib[0]["title"] == "B scene", lib
    assert db.db_skip_identity(_URL_A, str(p)) == ("different", None), (
        "precondition: A must not own the file while B's row is alive")

    # The operator deletes B's row through POST /api/batch/delete.
    assert _batch_delete(b_id) == 1
    assert [r["id"] for r in _history()] == [a_id]
    assert _library_rows()[0]["history_id"] == b_id, (
        "precondition: the library row must still name the DELETED row, or "
        "there is no dangling link to mis-repair")
    assert db.db_skip_identity(_URL_A, str(p)) == ("unknown", None), (
        "precondition: a dangling current link is not permission -- this is "
        "the deliberate answer the prune must not overwrite")

    # A prune with nothing to do.
    assert db.db_prune(3650) == 0, (
        "the fixture aged a row; this case is only meaningful when the prune "
        "deletes nothing")

    assert _library_rows()[0]["history_id"] == b_id, (
        "a prune that deleted NOTHING repointed a library row it never broke")
    assert db.db_skip_identity(_URL_A, str(p)) == ("unknown", None), (
        "url A was handed ownership of url B's bytes by a prune that deleted "
        "nothing -- it would skip, report 'Already have', and retitle B's "
        "library row to A's title")


def test_the_repair_does_not_hand_a_library_row_to_a_different_url(fresh):
    """The prune DOES break this link, so the repair door is open -- and it must
    still refuse to walk through it to another url.

    B's only row at the path is a no-transport 'done' row, so ``keep_ids``
    retains nothing for B and the prune deletes it.  A's older proven row
    survives and carries the same ``library_id``.  ``MAX(h.id)`` over that
    library id therefore selects A."""
    p = _make_file(fresh, "cross.mp4")
    _log_real_transfer(p, _URL_A, 1000, "A scene")
    _log_no_transport_done(p, _URL_B, "B scene")

    hist = _history()
    assert [r["url"] for r in hist] == [_URL_A, _URL_B], hist
    a_id, b_id = hist[0]["id"], hist[1]["id"]
    assert hist[0]["library_id"] == hist[1]["library_id"] is not None, (
        "precondition: both rows must carry the SAME library_id, or MAX(h.id) "
        "could not cross urls here")
    lib = _library_rows()
    assert len(lib) == 1 and lib[0]["history_id"] == b_id, lib
    assert lib[0]["title"] == "B scene", lib
    assert db.db_skip_identity(_URL_A, str(p)) == ("different", None), (
        "precondition: A does not own the file while B's row is alive")

    assert _age(_URL_A) == 1 and _age(_URL_B) == 1
    removed = db.db_prune(30)
    assert removed == 1, (
        f"expected exactly B's unproven row to be deleted, got {removed}")
    survivors = _history()
    assert [r["id"] for r in survivors] == [a_id], survivors
    assert _history(_URL_B) == [], (
        "precondition: url B must have NO surviving row, which is what makes "
        "leaving the link dangling the only honest answer")

    lib = _library_rows()
    assert lib[0]["history_id"] == b_id, (
        "the prune handed a library row whose owner was url B to url A -- "
        f"history_id {b_id} -> {lib[0]['history_id']}")
    assert lib[0]["title"] == "B scene", (
        "the library row still describes B's work; an A skip would retitle it")
    assert db.db_skip_identity(_URL_A, str(p)) == ("unknown", None), (
        "A was given ownership of a path the record attributes to B")


def test_an_out_of_band_delete_stays_unrepaired_across_a_later_prune(fresh):
    """SAME url this time, so the url constraint alone cannot decide it.

    ``db_skip_identity``'s comment states the contract: "db_prune repairs the
    link it breaks itself; nothing else may assume it was repaired."  A prune
    that repairs an out-of-band dangle makes the answer depend on whether a
    scheduled prune happened to run since -- ownership decided by timing."""
    p = _make_file(fresh, "same-url.mp4")
    _log_real_transfer(p, _URL_B, 2000, "B scene")
    _log_no_transport_done(p, _URL_B, "B scene")

    hist = _history()
    assert [r["url"] for r in hist] == [_URL_B, _URL_B], hist
    proof_id, skip_id = hist[0]["id"], hist[1]["id"]
    assert hist[0]["bytes_fetched"] == 2000 and hist[0]["transfer_mode"] == "http"
    assert hist[1]["bytes_fetched"] == 0 and hist[1]["transfer_mode"] is None
    assert _library_rows()[0]["history_id"] == skip_id, (
        "precondition: the newest (no-transport) row is the current owner")

    assert _batch_delete(skip_id) == 1
    assert [r["id"] for r in _history()] == [proof_id]
    assert _library_rows()[0]["history_id"] == skip_id, (
        "precondition: the library row still names the deleted row")
    assert db.db_skip_identity(_URL_B, str(p)) == ("unknown", None), (
        "precondition: the deliberate answer for an out-of-band dangle")

    assert db.db_prune(3650) == 0, "this case needs a prune that deletes nothing"

    assert _library_rows()[0]["history_id"] == skip_id, (
        "a prune repaired a link that batch_ops.bulk_delete broke, so whether "
        "an out-of-band delete is honoured now depends on prune timing")
    assert db.db_skip_identity(_URL_B, str(p)) == ("unknown", None)


def test_the_repair_will_not_name_a_row_that_is_not_a_completion(fresh):
    """The new owner must be a row that could legitimately HAVE been the owner.

    ``library_record`` runs only from ``db_log``'s ``status == "done"`` arm, so
    product code does not normally give a non-done row a ``library_id`` -- but
    ``library.library_restore`` (library.py, the undo of a library delete) writes
    ``history.library_id`` from a snapshot with no status check.  The shape is
    therefore reachable, and a repair that could select it would install an
    owner ``db_skip_identity``'s prong-1 JOIN (``h.status = 'done'``) cannot
    read.  Seeded directly here because that is the writer's own shape."""
    p = _make_file(fresh, "not-done.mp4")
    _log_real_transfer(p, _URL_B, 2000, "B scene")
    _log_no_transport_done(p, _URL_B, "B scene")
    hist = _history()
    proof_id, skip_id = hist[0]["id"], hist[1]["id"]
    lib_id = _library_rows()[0]["id"]
    # Age the two done rows BEFORE seeding the failed one, so the failed row
    # keeps a current ``ts`` and SURVIVES the prune. Without that it is deleted
    # too and MAX(h.id) can never select it -- the case would pass vacuously.
    assert _age(_URL_B) == 2

    # A later FAILED attempt of the same url, linked exactly as
    # ``library_restore`` links one: a higher id than every done row here.
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id, site_name, url, status, filename, "
            "file_size, message, library_id) VALUES(?,?,?,?,?,?,?,?)",
            (_SITE, _SITE_NAME, _URL_B, "failed", p.name, 0, "boom", lib_id))
        failed_id = cx.execute("SELECT MAX(id) FROM history").fetchone()[0]
    assert failed_id > skip_id > proof_id, (failed_id, skip_id, proof_id)
    assert _library_rows()[0]["history_id"] == skip_id, (
        "precondition: the done skip row is still the current owner")

    assert db.db_prune(30) == 1, "expected exactly the aged skip row to go"
    assert [r["id"] for r in _history()] == [proof_id, failed_id], (
        "precondition: the failed row must SURVIVE and outrank the proof row, "
        "or MAX(h.id) could not select it")

    assert _library_rows()[0]["history_id"] == proof_id, (
        "the repair installed a non-completion as the library row's owner -- a "
        "row db_skip_identity's prong-1 JOIN (h.status = 'done') cannot read")
    assert db.db_skip_identity(_URL_B, str(p)) == ("same", str(p))


# ── Negative controls: the narrowing must not become a refusal ──────────────

def test_the_prune_still_repoints_the_link_it_breaks_itself(fresh):
    """THE LEGITIMATE REPAIR, which this cut must leave intact.  Row 563's
    healthy steady state: one real transfer, then no-transport skip rows, each
    becoming the current owner in turn.  An age-based prune deletes the skip
    rows and retains the proof row, so the library must follow to the proof
    row -- of the SAME url, which is the only url involved."""
    p = _make_file(fresh, "healthy.mp4")
    _log_real_transfer(p, _URL_B, 2000, "B scene")
    _log_no_transport_done(p, _URL_B, "B scene")
    _log_no_transport_done(p, _URL_B, "B scene")

    hist = _history()
    assert len(hist) == 3, hist
    proof_id, newest_id = hist[0]["id"], hist[2]["id"]
    assert _library_rows()[0]["history_id"] == newest_id, (
        "precondition: the newest skip row owns the library row and is doomed")
    assert db.db_skip_identity(_URL_B, str(p)) == ("same", str(p)), (
        "precondition: the url is skippable BEFORE the prune")

    assert _age(_URL_B) == 3
    assert db.db_prune(30) == 2, "the two skip rows must go"
    assert [r["id"] for r in _history()] == [proof_id]

    assert _library_rows()[0]["history_id"] == proof_id, (
        "the prune left the library naming a deleted row, so the evidence it "
        "retained is unreadable and a healthy skip became a re-download")
    assert db.db_skip_identity(_URL_B, str(p)) == ("same", str(p))


def test_a_prune_that_deletes_nothing_leaves_the_library_table_unchanged(fresh):
    """The whole table, not one column.  A prune with nothing to delete is a
    no-op on ``library`` -- including the healthy rows whose links are intact,
    which the shipped repair also scanned."""
    p_a = _make_file(fresh, "healthy-a.mp4")
    p_b = _make_file(fresh, "healthy-b.mp4")
    _log_real_transfer(p_a, _URL_A, 1000, "A scene")
    _log_real_transfer(p_b, _URL_B, 2000, "B scene")
    before = _library_rows()
    assert len(before) == 2, before
    assert all(r["history_id"] is not None for r in before), before

    assert db.db_prune(3650) == 0
    assert _library_rows() == before, (
        "a prune that deleted nothing rewrote the library table")
    assert db.db_skip_identity(_URL_A, str(p_a)) == ("same", str(p_a))
    assert db.db_skip_identity(_URL_B, str(p_b)) == ("same", str(p_b))


def test_a_broken_link_with_no_surviving_row_of_its_url_stays_dangling(fresh):
    """The repair repoints to a survivor OF THAT URL; it does not invent one,
    and it does not NULL the column either.  Dangling is the terminal state
    ``db_skip_identity`` already answers "unknown" for."""
    p = _make_file(fresh, "emptied.mp4")
    _log_no_transport_done(p, _URL_B, "B scene")
    row_id = _history()[0]["id"]
    assert _library_rows()[0]["history_id"] == row_id

    assert _age(_URL_B) == 1
    assert db.db_prune(30) == 1, "an unproving row must not be retained"
    assert _history() == []

    lib = _library_rows()
    assert len(lib) == 1, lib
    assert lib[0]["history_id"] == row_id, (
        "the repair either invented an owner or NULLed the column; the "
        "dangling id must survive verbatim")
    assert db.db_skip_identity(_URL_B, str(p)) == ("unknown", None)


def test_the_repair_survives_a_history_only_database(fresh):
    """``db_init`` without the library schema is a supported caller shape.  The
    snapshot the repair takes reads ``library``, and it must not be able to
    abort or skip the DELETE the prune exists to perform."""
    p = _make_file(fresh, "nolib.mp4")
    _log_no_transport_done(p, _URL_B, "B scene")
    assert _age(_URL_B) == 1
    with db.db_conn() as cx:
        cx.execute("DROP TABLE IF EXISTS library_tags")
        cx.execute("DROP TABLE library")
        names = {r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "library" not in names, names
    assert "history" in names, names

    assert db.db_prune(30) == 1, (
        "the prune must still delete when there is no library to repair")
    assert _history() == []


# ── F16: the degraded predicate must not name the column it degraded for ────

def test_the_proof_predicate_without_bytes_fetched_names_no_missing_column():
    """The pre-migration-8 arm.  ``_TRANSFER_PROOF_NO_MODE`` names
    ``h.bytes_fetched``, so returning it for a table that LACKS that column
    produces a predicate that cannot be executed -- the guard selecting a
    statement that reintroduces exactly what the guard detected."""
    pre_v8 = sqlite3.connect(":memory:")
    pre_v8.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, status TEXT, "
                   "url TEXT, library_id INTEGER)")
    cols = {r[1] for r in pre_v8.execute(
        "PRAGMA table_info(history)").fetchall()}
    assert "bytes_fetched" not in cols and "transfer_mode" not in cols, cols

    pred = db._transfer_proof_sql(pre_v8)
    assert "bytes_fetched" not in pred, (
        f"the predicate names the very column whose absence selected it: {pred}")
    assert "transfer_mode" not in pred, pred
    # It must EXECUTE, and it must never prove a transfer: without the column
    # there is no measured byte count anywhere, and UNKNOWN is not permission.
    pre_v8.execute(
        "INSERT INTO history(status, url) VALUES('done', 'u')")
    rows = pre_v8.execute(
        "SELECT id FROM history h WHERE " + pred).fetchall()
    assert rows == [], (
        f"the degraded predicate proved a transfer it cannot measure: {pred}")

    # The other half of the same guard is untouched: a table that HAS
    # bytes_fetched but lacks transfer_mode keeps the shipped row 544 rule.
    pre_v9 = sqlite3.connect(":memory:")
    pre_v9.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, status TEXT, "
                   "url TEXT, bytes_fetched INTEGER)")
    assert db._transfer_proof_sql(pre_v9) is db._TRANSFER_PROOF_NO_MODE
    pre_v8.close()
    pre_v9.close()


def test_an_unmigrated_history_answers_unproven_instead_of_raising(fresh):
    """End to end on the shape that reaches it: an upgraded database whose
    history table predates migration 8.

    On the defective base the interpolated predicate raises ``no such column``.
    ``db_skip_identity``'s bare handler turns that into ("unknown", None) for
    EVERY url -- verbatim the failure its docstring says the degradation exists
    to prevent -- and ``db_prune`` does not catch it at all, so
    POST /api/history/prune returns 500."""
    p = _make_file(fresh, "pre-v8.mp4")
    _log_real_transfer(p, _URL_B, 4096, "B scene")
    assert _library_rows()[0]["history_id"] == _history()[0]["id"]

    with db.db_conn() as cx:
        cx.execute(
            "CREATE TABLE history_old AS SELECT id, site_id, site_name, url, "
            "status, filename, file_size, message, screenshot, "
            "honeypot_score, library_id, ts FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_old RENAME TO history")
        cols = {r[1] for r in cx.execute(
            "PRAGMA table_info(history)").fetchall()}
    assert "bytes_fetched" not in cols, cols
    assert "transfer_mode" not in cols, cols
    assert "library_id" in cols, cols

    # The url is still the CURRENT owner of a file that is on disk, so this is
    # the "unproven" state, not "unknown": nothing in the record can prove the
    # transfer any more, and naming that separately is what surfaces it.
    assert db.db_skip_identity(_URL_B, str(p)) == ("unproven", str(p)), (
        "the whole history collapsed to 'unknown' because the degraded "
        "predicate raised inside the bare handler")

    def _ids():
        # The file-level helper names columns this table no longer has.
        with db.db_conn() as cx:
            return [r[0] for r in cx.execute(
                "SELECT id FROM history ORDER BY id").fetchall()]

    assert db.db_prune(30) == 0, (
        "POST /api/history/prune raised OperationalError on an unmigrated "
        "database")
    assert len(_ids()) == 1, _ids()

    # And the honest consequence, measured rather than left implicit: with no
    # byte count in the schema, retention has nothing to key on, so an aged done
    # row is NOT retained. That is the same answer "unproven" gives -- this
    # database cannot prove a transfer -- and it is why the link below is left
    # dangling rather than handed to the older row.
    lib_id_before = _library_rows()[0]["history_id"]
    with db.db_conn() as cx:
        assert cx.execute(
            "UPDATE history SET ts = datetime('now', '-90 days')").rowcount == 1
    assert db.db_prune(30) == 1
    assert _ids() == []
    assert _library_rows()[0]["history_id"] == lib_id_before, (
        "the repair invented an owner on a database that can prove nothing")
    assert db.db_skip_identity(_URL_B, str(p)) == ("unknown", None)
