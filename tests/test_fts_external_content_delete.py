"""history_fts kept an orphaned index entry for every deleted history row.

`history_fts` is an FTS5 EXTERNAL-CONTENT table (content='history',
content_rowid='id', bulk_downloader/db.py). SQLite maintains NOTHING for
such a table: the application must issue the FTS5 'delete' command with
the row's OLD column values. Nothing did. There is no CREATE TRIGGER
anywhere in the app, and both history deleters -- db_prune (db.py) and
batch_ops.bulk_delete -- issued a bare DELETE FROM history.

WHY NOT A TRIGGER. The textbook FTS5 fix is an AFTER DELETE trigger.
Measured on SQLite 3.45.1 (the venv build): issuing 'delete' for a doc
the inverted index does not hold raises

    sqlite3.DatabaseError: database disk image is malformed

and from inside a trigger that aborts and rolls back the whole DELETE --
so a prune of a pre-FTS database would 500 instead of pruning. The
maintenance is therefore issued from Python, over a membership set
DERIVED from the index.

WHY MEMBERSHIP IS DERIVED, NOT INFERRED FROM AN EXCEPTION. Measured
(10 indexed rows, row 5 made stale by an UPDATE to an FTS-indexed
column, then a 'delete' issued for every row in id order): the
malformed-image error fired on row 10 -- which was correctly indexed and
was correctly removed -- and did NOT fire on row 5, the one row the
index could not part with. The exception class says nothing about which
row the index held, so counting it as "not indexed" reports a false
desync on a healthy database. Membership comes from fts5vocab, and the
applied count is verified by re-reading the index after the write.
"""
import sqlite3

import pytest

from bulk_downloader import batch_ops
from bulk_downloader import db


# -- helpers -------------------------------------------------------

def _indexed_docs():
    """Distinct rowids actually present in the inverted index.

    COUNT(*) FROM history_fts reads THROUGH to `history` on an
    external-content table and can never show a desync, so it is not
    usable here. fts5vocab reads the index itself.
    """
    with db.db_conn() as cx:
        cx.execute("DROP TABLE IF EXISTS temp._t820_vocab")
        cx.execute("CREATE VIRTUAL TABLE temp._t820_vocab "
                   "USING fts5vocab('main', 'history_fts', 'instance')")
        try:
            return {r[0] for r in cx.execute(
                "SELECT DISTINCT doc FROM temp._t820_vocab").fetchall()}
        finally:
            cx.execute("DROP TABLE IF EXISTS temp._t820_vocab")


def _history_ids():
    with db.db_conn() as cx:
        return {r[0] for r in cx.execute("SELECT id FROM history").fetchall()}


def _integrity():
    with db.db_conn() as cx:
        try:
            cx.execute("INSERT INTO history_fts(history_fts) "
                       "VALUES('integrity-check')")
            return "ok"
        except Exception as e:                       # pragma: no cover
            return f"FAILED: {type(e).__name__}: {e}"


def _age_all_rows():
    """Push every history row's ts far into the past so db_prune matches
    it. `ts` is not an FTS-indexed column, so this cannot perturb the
    index under test."""
    with db.db_conn() as cx:
        cx.execute("UPDATE history SET ts = '2000-01-01T00:00:00'")


def _seed_indexed(n, tag="alpha"):
    """n rows through the real db_log -- history AND the FTS mirror."""
    for i in range(n):
        db.db_log("siteA", "Acme Studio", f"https://a.example/{tag}{i}",
                  "done", filename=f"{tag}{i}.mp4",
                  message=f"marker{tag}{i} sunset cabin")


def _seed_unindexed(n, tag="ghost"):
    """n rows straight into `history`, bypassing the FTS mirror -- the
    desynced state a database whose index was recreated over existing
    history is in, and the state an AFTER DELETE trigger cannot
    survive."""
    with db.db_conn() as cx:
        for i in range(n):
            cx.execute(
                "INSERT INTO history(site_id,site_name,url,status,"
                "filename,message) VALUES(?,?,?,?,?,?)",
                ("siteA", "Acme Studio", f"https://a.example/{tag}{i}",
                 "done", f"{tag}{i}.mp4", f"marker{tag}{i} sunset cabin"))


def _make_stale(row_id):
    """Rewrite an FTS-INDEXED column of a LIVE row without refreshing the
    index -- exactly what the shipped bulk_move / storage_rebalance /
    status-update paths do today. `ts` is deliberately NOT used: it is
    not indexed and cannot perturb the subject. The replacement carries
    MORE tokens than the original, which is the shape measured to make
    FTS5 raise on a LATER, healthy row."""
    with db.db_conn() as cx:
        cx.execute("UPDATE history SET message = ? WHERE id = ?",
                   (f"renamed{row_id} winter lodge extra tokens here now",
                    row_id))


def _seed_orphan_directly(rowid, text):
    """Put a doc in the inverted index whose content row does not exist,
    WITHOUT going through the code under test. Needed so the join guard
    below has its subject in its denominator on a patched tree too."""
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history_fts(rowid, site_name, url, filename, "
            "message, status) VALUES(?,?,?,?,?,?)",
            (rowid, "Ghost Studio", f"https://g.example/{text}",
             f"{text}.mp4", f"{text} phantom", "done"))


# -- RED: the two deleters must maintain the index -----------------

def test_db_prune_removes_the_rows_from_the_fts_index(clean_workdir):
    db.db_init()
    _seed_indexed(4)
    assert _indexed_docs() == _history_ids(), "fixture: index must start in sync"
    keep = sorted(_history_ids())[-1]
    with db.db_conn() as cx:
        cx.execute("UPDATE history SET ts='2000-01-01T00:00:00' WHERE id != ?",
                   (keep,))
    removed = db.db_prune(1)
    assert removed == 3
    assert _history_ids() == {keep}
    assert _indexed_docs() == {keep}, (
        f"history_fts kept orphaned docs after db_prune: index holds "
        f"{sorted(_indexed_docs())}, history holds {sorted(_history_ids())}")
    assert _integrity() == "ok"


def test_bulk_delete_removes_the_rows_from_the_fts_index(clean_workdir):
    db.db_init()
    _seed_indexed(4)
    assert _indexed_docs() == _history_ids(), "fixture: index must start in sync"
    ids = sorted(_history_ids())
    doomed, survivors = ids[:2], set(ids[2:])
    out = batch_ops.bulk_delete({"id_in": doomed}, dry_run=False)
    assert out["processed"] == 2, out
    assert _history_ids() == survivors
    assert _indexed_docs() == survivors, (
        f"history_fts kept orphaned docs after bulk_delete: index holds "
        f"{sorted(_indexed_docs())}, history holds {sorted(_history_ids())}")
    assert _integrity() == "ok"


def test_rows_with_null_indexed_columns_are_forgotten_too(clean_workdir):
    """db_log() coalesces NULL to '' before indexing, so the index holds
    '' for a column history stores as NULL. The delete must supply the
    SAME values or it does not match the document."""
    db.db_init()
    db.db_log("siteA", None, "https://a.example/nulls", "done",
              filename=None, message=None)
    db.db_log("siteA", "Acme Studio", "https://a.example/kept", "done",
              filename="kept.mp4", message="markerkept")
    with db.db_conn() as cx:
        nulls = cx.execute("SELECT COUNT(*) FROM history "
                           "WHERE site_name IS NULL").fetchone()[0]
    assert nulls == 1, "fixture: the row must really carry NULLs"
    ids = sorted(_history_ids())
    batch_ops.bulk_delete({"id_in": ids[:1]}, dry_run=False)
    assert _indexed_docs() == set(ids[1:]), (
        f"NULL-column row left an orphan: index {sorted(_indexed_docs())}, "
        f"history {sorted(_history_ids())}")
    assert _integrity() == "ok"


def test_prune_on_a_desynced_index_still_deletes_every_history_row(
        clean_workdir):
    """The row the index never held must not stop the delete.

    An AFTER DELETE trigger fails this: FTS5 raises DatabaseError on a
    'delete' for an absent doc and the whole statement rolls back.
    """
    db.db_init()
    _seed_indexed(40, tag="live")
    _seed_unindexed(60, tag="ghost")
    assert len(_indexed_docs()) == 40, "fixture: 40 indexed, 60 not"
    assert len(_history_ids()) == 100
    _age_all_rows()
    removed = db.db_prune(1)
    assert removed == 100, (
        f"db_prune deleted {removed} of 100 rows -- a desynced index must "
        f"not abort the delete")
    assert _history_ids() == set()
    assert _indexed_docs() == set(), (
        f"orphans left behind: {sorted(_indexed_docs())}")
    assert _integrity() == "ok"


def test_a_stale_row_costs_maintenance_only_for_itself(clean_workdir):
    """R1. A row whose indexed text was updated in place cannot be
    removed from the index -- its OLD terms are gone from `history` and
    nothing remembers them. That is the honest limit of this cut. What
    must NOT happen is the maintenance failing for the CLEAN rows in the
    same batch: one stale row must cost exactly one orphan."""
    db.db_init()
    _seed_indexed(10)
    ids = sorted(_history_ids())
    assert set(ids) == _indexed_docs(), "fixture: index must start in sync"
    stale = ids[4]
    _make_stale(stale)
    _age_all_rows()
    removed = db.db_prune(1)
    assert removed == 10
    assert _history_ids() == set()
    left = _indexed_docs()
    assert left == {stale}, (
        f"one stale row ({stale}) cost {len(left)} orphan(s) {sorted(left)}: "
        f"FTS maintenance cascaded onto CLEAN rows")
    assert _integrity() == "ok"


# -- RED: the helper's own reporting contract ----------------------

def test_db_fts_forget_reports_rows_the_index_did_not_hold(clean_workdir):
    """Unknown is a third state. A maintenance pass that gives up on 60
    of 100 rows must say so, and must not report the 60 as removed."""
    db.db_init()
    _seed_indexed(40, tag="live")
    _seed_unindexed(60, tag="ghost")
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT id, site_name, url, filename, message, status "
            "FROM history").fetchall()
        res = db.db_fts_forget(cx, rows)
    assert res["present"] is True, res
    assert res["verified"] is True, res
    assert res["requested"] == 100, res
    assert res["applied"] == 40, res
    assert res["unindexed"] == 60, res
    assert res["remaining"] == 0, res
    assert res["failed"] == 0, res


def test_db_fts_forget_says_so_when_there_is_no_index(clean_workdir):
    db.db_init()
    _seed_indexed(3)
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT id, site_name, url, filename, message, status "
            "FROM history").fetchall()
        cx.execute("DROP TABLE history_fts")
        res = db.db_fts_forget(cx, rows)
    assert res["present"] is False, res
    assert res["applied"] == 0, res
    # NOT reported as 3 desynced rows -- an absent index and a desynced
    # index are different states and must not be conflated.
    assert res["unindexed"] == 0, res


class _FailingDeleteConn:
    """Delegates to a real connection but makes the FTS5 'delete' command
    raise OperationalError -- the shape of FTS5 going away mid-batch (the
    table dropped, the module unloaded). Nothing further can work on the
    index, so the pass must stop rather than raise once per row."""

    def __init__(self, cx):
        self._cx = cx
        self.delete_attempts = 0

    def execute(self, sql, *args):
        if "VALUES('delete'" in sql:
            self.delete_attempts += 1
            raise sqlite3.OperationalError("no such module: fts5")
        return self._cx.execute(sql, *args)


def test_db_fts_forget_stops_when_the_index_goes_away_mid_batch(clean_workdir):
    """OperationalError is a SUBCLASS of DatabaseError, so it has to be
    caught first or this branch is unreachable. Nothing is reported as
    removed, and nothing is reported as unindexed -- the index held all
    five rows, we just could not act on them."""
    db.db_init()
    _seed_indexed(5)
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT id, site_name, url, filename, message, status "
            "FROM history ORDER BY id").fetchall()
        stub = _FailingDeleteConn(cx)
        res = db.db_fts_forget(stub, rows)
    assert stub.delete_attempts == 1, (
        f"kept issuing deletes after the index went away: "
        f"{stub.delete_attempts} attempts")
    assert res["failed"] == 1, res
    assert res["applied"] == 0, res
    assert res["unindexed"] == 0, res
    assert res["remaining"] == 5, res
    assert len(_indexed_docs()) == 5


class _BlindReReadConn:
    """Delegates to a real connection, but the SECOND derivation of the
    index doc set -- the one that verifies what actually left -- fails."""

    def __init__(self, cx):
        self._cx = cx
        self.vocab_creates = 0

    def execute(self, sql, *args):
        if "fts5vocab" in sql:
            self.vocab_creates += 1
            if self.vocab_creates > 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
        return self._cx.execute(sql, *args)


def test_db_fts_forget_reports_unknown_when_it_cannot_re_read(clean_workdir):
    """Unknown is a third state and it fails. If the index cannot be
    re-read, the pass does not know what it removed -- and an unverified
    guess of "all of them" is precisely the counter that made the first
    draft of this cut report work it had not done."""
    db.db_init()
    _seed_indexed(3)
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT id, site_name, url, filename, message, status "
            "FROM history ORDER BY id").fetchall()
        stub = _BlindReReadConn(cx)
        res = db.db_fts_forget(stub, rows)
    assert stub.vocab_creates == 2, stub.vocab_creates
    assert res["present"] is True, res
    assert res["verified"] is False, (
        "the index was never re-read; the counts below are a guess")
    assert res["applied"] == 0, (
        f"claimed {res['applied']} doc(s) removed without confirming any "
        f"of them: {res}")
    assert res["remaining"] == 0, res


def test_db_fts_forget_applied_is_verified_against_the_index(clean_workdir):
    """R3. `applied` must mean "left the index", not "execute() did not
    raise". Measured on the uncorrected form: it returned applied=2 on a
    4-row batch while all 4 docs were still indexed."""
    db.db_init()
    _seed_indexed(6)
    ids = sorted(_history_ids())
    _make_stale(ids[1])
    _make_stale(ids[3])
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT id, site_name, url, filename, message, status "
            "FROM history ORDER BY id").fetchall()
        res = db.db_fts_forget(cx, rows)
    left = _indexed_docs()
    assert res["verified"] is True, res
    assert res["applied"] == len(ids) - len(left), (
        f"reported applied={res['applied']} of {len(ids)}, but {len(left)} "
        f"doc(s) remain: {sorted(left)} ({res})")
    assert res["remaining"] == len(left), res
    assert left == {ids[1], ids[3]}, sorted(left)


# -- RED: denominator gate over every history deleter --------------

_EXPECTED_DELETERS = {
    ("bulk_downloader/batch_ops.py", "bulk_delete"),
    ("bulk_downloader/db.py", "db_prune"),
}


def _history_deleters():
    """AST walk over bulk_downloader/**/*.py. Subject: every function
    that passes a string literal matching `DELETE FROM history` (word
    boundary -- history_tags does not match) to a .execute() call.
    Docstrings are excluded because only Call arguments are inspected."""
    import ast
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "bulk_downloader"
    pat = re.compile(r"\bdelete\s+from\s+history\b", re.I)
    found = {}
    scanned = 0
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        scanned += 1
        tree = ast.parse(p.read_text(encoding="utf-8"))
        stack = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()
            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == "execute":
                    for a in node.args:
                        if (isinstance(a, ast.Constant)
                                and isinstance(a.value, str)
                                and pat.search(a.value)):
                            key = (str(p.relative_to(root.parent)),
                                   stack[-1] if stack else "<module>")
                            found.setdefault(key, []).append(node.lineno)
                self.generic_visit(node)
        V().visit(tree)
    return scanned, found


def test_the_deleter_scan_can_see_its_subject():
    """A gate that finds nothing reports OK. Prove the denominator is
    non-empty and contains the two known deleters before trusting any
    verdict built on it."""
    scanned, found = _history_deleters()
    assert scanned > 100, f"only {scanned} modules scanned -- scan is blind"
    assert set(found) == _EXPECTED_DELETERS, (
        f"the set of functions that DELETE FROM history changed: {set(found)}")


def test_every_history_deleter_maintains_the_fts_index():
    """Adding a third deleter without FTS maintenance re-opens this bug.
    The denominator is derived, not listed."""
    import ast
    from pathlib import Path
    _, found = _history_deleters()
    root = Path(__file__).resolve().parent.parent
    missing = []
    for (relpath, funcname) in sorted(found):
        tree = ast.parse((root / relpath).read_text(encoding="utf-8"))
        target = None
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name == funcname:
                target = n
        assert target is not None, f"{relpath}:{funcname} not found"
        calls = {
            (n.func.attr if isinstance(n.func, ast.Attribute)
             else getattr(n.func, "id", None))
            for n in ast.walk(target) if isinstance(n, ast.Call)}
        if "db_fts_forget" not in calls:
            missing.append(f"{relpath}::{funcname}")
    assert not missing, (
        f"these functions delete history rows without maintaining the "
        f"external-content FTS5 index: {missing}")


# -- GUARDS (pass on pristine AND patched -- not counted as RED) ----

def test_db_prune_evaluates_its_cutoff_exactly_once():
    """STRUCTURAL pin, and it says so. db_prune must select the doomed
    rows and delete them over the same instant. Two separate
    `datetime('now', ...)` evaluations can straddle a second boundary
    and select different sets, which would leave a LIVE history row
    stripped from the index -- unsearchable, the opposite and worse
    failure. The race window is sub-second, so this is pinned on the
    source rather than pretended to be pinned by a runtime test."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "bulk_downloader" / "db.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "db_prune")
    lits = [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "datetime('now'" in n.value]
    assert len(lits) == 1, (
        f"db_prune evaluates datetime('now', ...) {len(lits)} times; the "
        f"cutoff must be computed once and bound into both statements: "
        f"{lits}")


def test_guard_deleting_does_not_evict_live_rows_sharing_a_term(
        clean_workdir):
    """CRY-WOLF guard. Every seeded row carries the terms 'sunset' and
    'cabin' and site_name 'Acme Studio'. Deleting some rows must remove
    exactly those docs -- an over-broad 'delete-all'/'rebuild' or a
    wrong rowid would silently strip the survivors too."""
    db.db_init()
    _seed_indexed(6)
    ids = sorted(_history_ids())
    doomed, survivors = ids[:3], set(ids[3:])
    batch_ops.bulk_delete({"id_in": doomed}, dry_run=False)
    hits = {r["id"] for r in db.db_search_fts("sunset")}
    assert hits == survivors, (
        f"shared-term search after delete returned {sorted(hits)}, "
        f"expected the survivors {sorted(survivors)}")
    assert {r["id"] for r in db.db_search_fts("cabin")} == survivors
    assert {r["id"] for r in db.db_search_fts("Acme")} == survivors


def test_guard_search_still_finds_a_row_logged_after_a_prune(clean_workdir):
    """CRY-WOLF guard. The maintenance pass must leave the index usable
    for subsequent writes."""
    db.db_init()
    _seed_indexed(3)
    _age_all_rows()
    db.db_prune(1)
    db.db_log("siteB", "Beta", "https://b.example/new", "done",
              filename="new.mp4", message="freshmarker afterwards")
    hits = db.db_search_fts("freshmarker")
    assert len(hits) == 1, hits
    assert hits[0]["url"] == "https://b.example/new"


def test_guard_prune_is_unaffected_when_the_index_is_absent(clean_workdir):
    """FTS5 fail-open: db_init logs a warning and skips index creation on
    a build without FTS5, and db_log swallows the mirror write. Prune
    must behave identically."""
    db.db_init()
    _seed_indexed(3)
    with db.db_conn() as cx:
        cx.execute("DROP TABLE history_fts")
    _age_all_rows()
    assert db.db_prune(1) == 3
    assert _history_ids() == set()


def test_guard_an_orphaned_doc_is_not_searchable(clean_workdir):
    """The INNER JOIN in db_search_fts is what keeps an orphaned doc out
    of results (and keeps snippet() off it). The orphan is seeded
    DIRECTLY into the index here, bypassing the code under test, so the
    guard's subject survives on a patched tree -- seeding it through a
    delete would let the fix remove the very thing the guard is asked
    about, and the guard would then certify the join while unable to
    observe it. Flips when the join is loosened."""
    db.db_init()
    _seed_indexed(2)
    live = sorted(_history_ids())
    _seed_orphan_directly(9001, "orphantoken")
    assert 9001 in _indexed_docs(), "fixture: the orphan must really be indexed"
    assert 9001 not in _history_ids()
    assert db.db_search_fts("orphantoken") == [], (
        "an index entry with no history row surfaced in search results")
    assert {r["id"] for r in db.db_search_fts("sunset")} == set(live)


def test_guard_no_desync_warning_after_a_move_then_prune(
        clean_workdir, capsys):
    """CRY-WOLF FLOOR, and the reason the first draft of this cut was a
    measured regression. A database whose index is perfectly in sync can
    be pushed into stale-terms territory by one ordinary bulk_move. The
    maintenance pass must not then announce a desync it cannot actually
    diagnose: the rows ARE indexed, and full-text search is not missing
    anything. Measured on the uncorrected form: '4 of 6 pruned row(s)
    were absent from the history_fts index' on a healthy database."""
    import sys
    db.db_init()
    _seed_indexed(6)
    ids = sorted(_history_ids())
    _make_stale(ids[0])
    _make_stale(ids[2])
    capsys.readouterr()
    _age_all_rows()
    assert db.db_prune(1) == 6
    err = capsys.readouterr().err
    # positive control: prove this capture can see stderr at all, so a
    # silent capture is not mistaken for a silent deleter.
    sys.stderr.write("t820-probe\n")
    assert "t820-probe" in capsys.readouterr().err, (
        "stderr capture is blind -- the assertion below proves nothing")
    lowered = err.lower()
    for phrase in ("absent from", "desync", "missing rows", "index issue"):
        assert phrase not in lowered, (
            f"routine prune printed a desync warning it cannot substantiate: "
            f"{err!r}")


def test_a_row_that_survived_the_delete_keeps_its_index_entry(clean_workdir):
    """The maintenance follows the rows that actually LEFT `history`, not
    the rows the filter matched. Stripping a live row's terms is the
    opposite and worse failure: the row still exists and is no longer
    findable. A BEFORE DELETE trigger makes one row's delete fail, which
    is the only way the two sets differ."""
    db.db_init()
    _seed_indexed(2)
    ids = sorted(_history_ids())
    gone, survivor = ids[0], ids[1]
    with db.db_conn() as cx:
        cx.execute(f"CREATE TRIGGER t820_block BEFORE DELETE ON history "
                   f"WHEN OLD.id = {survivor} BEGIN "
                   f"SELECT RAISE(ABORT, 'blocked'); END")
    out = batch_ops.bulk_delete({"id_in": ids}, dry_run=False)
    assert out["processed"] == 1 and out["errors"] == 1, out
    assert _history_ids() == {survivor}
    assert _indexed_docs() == {survivor}, (
        f"index holds {sorted(_indexed_docs())}, history holds "
        f"{sorted(_history_ids())}")
    assert {r["id"] for r in db.db_search_fts("sunset")} == {survivor}, (
        "a row that was NOT deleted lost its index entry and is no longer "
        "findable by full-text search")
    assert gone not in _indexed_docs()


def test_guard_batch_delete_response_shape_is_unchanged(clean_workdir):
    """POST /api/batch/delete returns this dict verbatim (app_batch.py).
    The index maintenance must not add, drop, or rename a key."""
    db.db_init()
    _seed_indexed(3)
    ids = sorted(_history_ids())
    live = batch_ops.bulk_delete({"id_in": ids[:1]}, dry_run=False)
    assert sorted(live) == ["candidates_matched", "dry_run", "errors",
                            "files_deleted", "ok", "processed"], sorted(live)
    assert live == {"ok": True, "candidates_matched": 1, "processed": 1,
                    "files_deleted": 0, "errors": 0, "dry_run": False}
    preview = batch_ops.bulk_delete({"id_in": ids[1:]}, dry_run=True)
    assert sorted(preview) == ["candidates_matched", "dry_run", "errors",
                               "files_deleted", "ok", "processed", "sample",
                               "total_size_gb"], sorted(preview)


def test_guard_db_prune_still_returns_a_plain_int(clean_workdir):
    """POST /api/history/prune puts this straight in the JSON body."""
    db.db_init()
    _seed_indexed(2)
    _age_all_rows()
    removed = db.db_prune(1)
    assert type(removed) is int and removed == 2
