"""Row 607: a history row proves a REAL transfer, not merely a current identity.

THE QUESTION THIS FILE DECIDES. ``db_skip_identity`` (bulk_downloader/db.py) must
separate two ``done`` rows that agree in every column an earlier design looked
at.  Both are ``status='done'``, both carry ``bytes_fetched=0``, both hand
``db_log`` an ABSOLUTE ``file_path``, and both therefore reach
``library_record(..., history_id=...)`` whose

    history_id = COALESCE(?, history_id)

UPDATE makes the row that wrote it the library row's CURRENT owner.

  THE SKIP SHAPE   runner_transport.py's "Already have" arm --
                   ``db_log(..., 'done', name, size, "already on disk",
                   bytes_fetched=0, file_path=<abs>)``.  No transport ran.  This
                   row is the artifact under test manufacturing its own proof,
                   which is why row 544 (SHIPPED in v3.66.1397, deployed) rules
                   that a zero-byte done row is not ownership.

  THE 416 SHAPE    runner_transport.py's HTTP arm after ``_http_download``
                   returned ``(final_path.stat().st_size, 0)`` from the
                   ``resp.status_code == 416`` branch -- a fully downloaded,
                   on-disk file whose final call moved no bytes because the
                   server said the requested range was already satisfied.  The
                   db_log at runner_transport.py's success path stamps
                   ``transfer_mode="http"``, because an arm of the transfer
                   chain really did run.  Rows 560 and 561: reading this as
                   non-ownership produces a false needs_review row and a
                   full-size duplicate at ``name_1.mp4``.

WHY "CURRENT" CANNOT BE THE DISCRIMINATOR.  ``test_both_shapes_become_the_current
_library_owner`` below measures it rather than asserting it in prose: the skip
arm's own row wins the ``history_id`` COALESCE exactly as the 416 arm's does.  A
rule of "a CURRENT url/path identity plus a recorded zero is ownership" would
accept the skip row and reintroduce the defect row 544 closed.

WHY "A PRIOR POSITIVE-TRANSFER ROW" CANNOT BE THE DISCRIMINATOR EITHER.  It gets
both cases backwards.  A healthy repeated skip HAS such a row (so the rule adds
nothing over the shipped code), and the 416 shape has NONE: the interrupted run
that left the ``.part`` raised ``_HTTPDownloadFailed("stopped")`` and was logged
``status='failed'``, never ``done``.  ``test_the_416_shape_has_no_prior_positive
_done_row`` pins that so the exclusion is a measurement.

THE DISCRIMINATOR.  ``transfer_mode`` -- WHICH transport moved the bytes, stated
by the branch that performed it.  ``db_log``'s own contract names it, and no
no-transfer arm in the tree passes it: the skip arm, the Stash dedup arm
(runner_integrations.py) and the clicked-with-no-download-dir arm (runner.py) all
omit the keyword and record NULL.  So the proof is:

    bytes_fetched > 0                                  a measured transfer
    bytes_fetched = 0 AND transfer_mode IS NOT NULL     a transport ran and
                                                        measured zero (416)

and NOT ``transfer_mode`` alone: ``runner_extractors.py`` has a done path that
stamps ``transfer_mode='http'`` with ``bytes_fetched=None``, and NULL bytes stay
UNKNOWN, which is never permission (CLAUDE.md A2).
``test_a_named_transport_with_an_unmeasured_byte_count_is_still_unproven`` is the
control for that.

SCOPE.  Rows 547, 560, 561 and 563, governed by row 607.  Row 563's literal
wording -- a healthy repeated skip stays usable after pruning removes its older
positive-transfer row -- cannot be satisfied inside ``db_skip_identity`` without
weakening row 544, because the surviving rows ARE the self-manufactured ones.  It
is resolved on the retention side instead: ``db_prune`` keeps the newest
transfer-proving ``done`` row per URL.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bulk_downloader import db

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE = "row607site"
_SITE_NAME = "Row 607 Site"
_URL_416 = "https://members.example.test/scene/row607-416"
_URL_SKIP = "https://members.example.test/scene/row607-skip"
_URL_OTHER = "https://members.example.test/scene/row607-other"


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
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")
        try:
            cx.execute("DELETE FROM library")
        except Exception:
            pass
    assert _history() == [], "the fixture did not start empty"
    return clean_workdir


def _history(url=None):
    with db.db_conn() as cx:
        sql = ("SELECT id, url, status, filename, file_size, message, "
               "bytes_fetched, transfer_mode, library_id FROM history")
        params = ()
        if url is not None:
            sql += " WHERE url=?"
            params = (url,)
        sql += " ORDER BY id"
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def _library():
    with db.db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, file_path, history_id, title FROM library "
            "ORDER BY id").fetchall()]


def _make_file(workdir, name, payload=b"row607-payload-bytes"):
    p = pathlib.Path(workdir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _log_416_shape(path, url=_URL_416, title="416 scene"):
    """EXACTLY the kwargs runner_transport.py's success db_log passes after the
    416 resume-complete arm returned ``(size, 0)``: transfer_mode is bound by the
    ``elif use_http:`` branch, bytes_fetched by the helper's second return."""
    db.db_log(_SITE, _SITE_NAME, url, "done", path.name,
              path.stat().st_size, "",
              bytes_fetched=0, transfer_mode="http",
              file_path=str(path), title=title, title_source="page")


def _log_skip_shape(path, url=_URL_SKIP, title="skip scene"):
    """EXACTLY the kwargs runner_transport.py's "Already have" arm passes.  No
    transfer_mode keyword: no arm of the transfer chain ran."""
    db.db_log(_SITE, _SITE_NAME, url, "done", path.name,
              path.stat().st_size, "already on disk",
              bytes_fetched=0,
              file_path=str(path), title=title, title_source="page")


def _log_real_transfer(path, url, moved=None, title="real scene"):
    db.db_log(_SITE, _SITE_NAME, url, "done", path.name,
              path.stat().st_size, "",
              bytes_fetched=moved if moved is not None else path.stat().st_size,
              transfer_mode="http",
              file_path=str(path), title=title, title_source="page")


# ── The measurement that parks the "current identity" design ────────────────

def test_both_shapes_become_the_current_library_owner(fresh):
    """PRECONDITION FOR EVERY VERDICT BELOW, measured rather than asserted in
    prose: the skip arm's own row wins library.history_id just as the 416 arm's
    does, so "is this row the CURRENT owner" cannot separate them."""
    p416 = _make_file(fresh, "row607-416.mp4")
    pskip = _make_file(fresh, "row607-skip.mp4")
    _log_416_shape(p416)
    _log_skip_shape(pskip)

    hist = _history()
    assert len(hist) == 2, hist
    by_url = {r["url"]: r for r in hist}
    lib = {r["file_path"]: r for r in _library()}
    assert len(lib) == 2, lib

    assert lib[str(p416)]["history_id"] == by_url[_URL_416]["id"], lib
    assert lib[str(pskip)]["history_id"] == by_url[_URL_SKIP]["id"], (
        "the skip arm's row is NOT the current library owner, so this file is "
        "not measuring the trap row 607 names")

    # They agree in every column the parked design inspected.
    assert by_url[_URL_416]["status"] == by_url[_URL_SKIP]["status"] == "done"
    assert by_url[_URL_416]["bytes_fetched"] == 0
    assert by_url[_URL_SKIP]["bytes_fetched"] == 0
    # And differ in exactly the column this cut reads.
    assert by_url[_URL_416]["transfer_mode"] == "http"
    assert by_url[_URL_SKIP]["transfer_mode"] is None, (
        "the skip arm recorded a transport; the discriminator would be "
        "self-manufactured")


def test_the_416_shape_has_no_prior_positive_done_row(fresh):
    """The other candidate row 607 names, measured and excluded.

    An operator STOP landing after the final chunk raises
    ``_HTTPDownloadFailed("stopped")``, which is logged ``status='failed'``.
    The only ``done`` row the 416 sequence ever produces is its own zero-byte
    one, so "a PRIOR positive-transfer row exists" answers NO for the shape it
    would have to accept."""
    p416 = _make_file(fresh, "row607-416.mp4")
    db.db_log(_SITE, _SITE_NAME, _URL_416, "failed", "", 0, "stopped",
              bytes_fetched=12345)
    _log_416_shape(p416)

    rows = _history(_URL_416)
    assert len(rows) == 2, rows
    assert [r["status"] for r in rows] == ["failed", "done"], rows
    positive_done = [r for r in rows
                     if r["status"] == "done" and (r["bytes_fetched"] or 0) > 0]
    assert positive_done == [], (
        "the 416 sequence produced a positive-transfer done row, which would "
        "make the rejected candidate viable after all")

    # And a healthy repeated skip DOES have one, so the candidate is backwards
    # in both directions.
    preal = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(preal, _URL_SKIP)
    _log_skip_shape(preal)
    skip_rows = _history(_URL_SKIP)
    assert len([r for r in skip_rows
                if r["status"] == "done" and (r["bytes_fetched"] or 0) > 0]) == 1


# ── THE CENTREPIECE ─────────────────────────────────────────────────────────

def test_the_416_shape_and_the_skip_shape_get_different_verdicts(fresh):
    """RED on the defective base: both answer ("unproven", path).

    The 416 shape is a complete, on-disk file this URL really did fetch; the
    skip shape is a row the skip arm wrote about a file it never fetched.  One
    predicate must accept the first and refuse the second."""
    p416 = _make_file(fresh, "row607-416.mp4")
    pskip = _make_file(fresh, "row607-skip.mp4")
    _log_416_shape(p416)
    _log_skip_shape(pskip)

    # PRECONDITIONS: both files exist, both rows are done, both recorded zero.
    assert p416.is_file() and pskip.is_file()
    by_url = {r["url"]: r for r in _history()}
    assert by_url[_URL_416]["bytes_fetched"] == 0
    assert by_url[_URL_SKIP]["bytes_fetched"] == 0
    assert {r["file_path"] for r in _library()} == {str(p416), str(pskip)}

    verdict_416 = db.db_skip_identity(_URL_416, str(p416))
    verdict_skip = db.db_skip_identity(_URL_SKIP, str(pskip))

    assert verdict_416 != verdict_skip, (
        "the two shapes got the SAME verdict, so nothing in the record "
        f"separated them: 416={verdict_416!r} skip={verdict_skip!r}")
    assert verdict_416 == ("same", str(p416)), (
        "a completed 416 resume was not read as ownership (rows 560, 561): "
        f"{verdict_416!r}")
    assert verdict_skip == ("unproven", str(pskip)), (
        "the skip arm's self-manufactured row was read as ownership, which is "
        f"the defect row 544 closed: {verdict_skip!r}")


# ── Negative controls ───────────────────────────────────────────────────────

def test_negative_control_a_genuine_skip_alone_is_still_refused(fresh):
    """Row 544's SHIPPED rule, restated at this seam.  A URL whose ONLY done row
    is a skip row proves nothing, however current that row is."""
    pskip = _make_file(fresh, "row607-skip.mp4")
    _log_skip_shape(pskip)
    _log_skip_shape(pskip)  # and a second one does not accumulate into proof

    rows = _history(_URL_SKIP)
    assert len(rows) == 2 and all(r["bytes_fetched"] == 0 for r in rows), rows
    assert all(r["transfer_mode"] is None for r in rows), rows
    assert _library()[0]["history_id"] == rows[-1]["id"], (
        "the newest skip row must be the current owner, or this control does "
        "not exercise the trap")

    assert db.db_skip_identity(_URL_SKIP, str(pskip)) == ("unproven", str(pskip))


def test_negative_control_the_healthy_steady_state_still_skips(fresh):
    """One real transfer, then skip rows.  The newest owned row is a skip, so a
    fix that inspected only the current owner turns every legitimate skip into
    a re-download."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    _log_skip_shape(p)
    _log_skip_shape(p)

    rows = _history(_URL_SKIP)
    assert [r["bytes_fetched"] for r in rows] == [p.stat().st_size, 0, 0], rows
    assert _library()[0]["history_id"] == rows[-1]["id"], (
        "the NEWEST row must be the current owner and a no-transfer skip, or "
        "this control does not separate a current-row fix from a scanning one")

    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("same", str(p))


def test_negative_control_a_named_transport_with_an_unmeasured_count_is_unproven(
        fresh):
    """``runner_extractors.py`` has a done path stamping ``transfer_mode='http'``
    with ``bytes_fetched=None``.  NULL is UNKNOWN and never proof (A2), so the
    rule must be "a MEASURED zero plus a named transport", not "a named
    transport"."""
    p = _make_file(fresh, "row607-nullbytes.mp4")
    db.db_log(_SITE, _SITE_NAME, _URL_416, "done", p.name, p.stat().st_size, "",
              bytes_fetched=None, transfer_mode="http",
              file_path=str(p), title="null bytes", title_source="page")

    rows = _history(_URL_416)
    assert len(rows) == 1, rows
    assert rows[0]["bytes_fetched"] is None and rows[0]["transfer_mode"] == "http"

    assert db.db_skip_identity(_URL_416, str(p)) == ("unproven", str(p)), (
        "a NULL byte count was laundered into ownership by the transport name")


def test_negative_control_a_pre_v8_null_row_is_still_unproven(fresh):
    """A pre-v8 row carries NULL bytes_fetched and NULL transfer_mode."""
    p = _make_file(fresh, "row607-prev8.mp4")
    db.db_log(_SITE, _SITE_NAME, _URL_SKIP, "done", p.name, p.stat().st_size,
              "pre-v8 row", bytes_fetched=None,
              file_path=str(p), title="pre v8", title_source="page")
    rows = _history(_URL_SKIP)
    assert rows[0]["bytes_fetched"] is None and rows[0]["transfer_mode"] is None
    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("unproven", str(p))


def test_negative_control_another_urls_file_is_still_different(fresh):
    """The "different" arm is untouched: a file the library attributes to
    another page url is provably not this work."""
    p = _make_file(fresh, "row607-other.mp4")
    _log_real_transfer(p, _URL_OTHER)
    stray = _make_file(fresh, "row607-stray.mp4")

    assert _library()[0]["file_path"] == str(p)
    assert db.db_skip_identity(_URL_416, str(p)) == ("different", None)
    assert db.db_skip_identity(_URL_416, str(stray)) == ("unknown", None)


# ── Row 547: a stale attribution over another url's current bytes ───────────

def test_row547_a_stale_positive_row_over_another_urls_bytes_is_not_ownership(
        fresh):
    """Row 547.  ``library.file_path`` is UNIQUE and reused in place, so after a
    name collision the FIRST url's positive-transfer row still points, through
    ``history.library_id``, at a library row whose bytes are now the SECOND
    url's.  Reading only that historical direction answers "same" and skips over
    the wrong file -- the 2026-08-29 wrong-file-right-title shape."""
    p = _make_file(fresh, "row607-collide.mp4", b"scene-A-bytes")
    _log_real_transfer(p, _URL_416, title="Scene A")
    lib_id = _library()[0]["id"]

    # Scene B lands on the same UNIQUE library path (the file was replaced).
    p.write_bytes(b"scene-B-bytes-which-are-longer")
    _log_real_transfer(p, _URL_OTHER, title="Scene B")

    lib = _library()
    assert len(lib) == 1 and lib[0]["id"] == lib_id, lib
    b_row = _history(_URL_OTHER)[0]
    assert lib[0]["history_id"] == b_row["id"], (
        "the second url did not become the current owner, so this test is not "
        "measuring row 547")
    a_row = _history(_URL_416)[0]
    assert a_row["library_id"] == lib_id, (
        "the first url's row must still carry the historical link, or the "
        "defect cannot be reproduced")
    assert (a_row["bytes_fetched"] or 0) > 0, a_row

    verdict = db.db_skip_identity(_URL_416, str(p))
    assert verdict[0] != "same", (
        "scene A skipped over scene B's bytes on the strength of a stale "
        f"historical link: {verdict!r}")
    assert verdict == ("different", None), (
        "A STALE ROW THAT DOES PROVE A TRANSFER GETS THE 'different' VERDICT, "
        "not 'unproven'. Both refuse the skip, but the needs_review row that "
        "'unproven' produces says the attribution RECORDS NO TRANSFER -- false "
        "of a row that recorded one, and A7 says a diagnostic collapsing "
        f"distinct failures costs the investigation. Got {verdict!r}")


def test_row547_an_unproving_stale_row_is_still_operator_visible(fresh):
    """The other half of the fallback, so both its branches are exercised.

    The state an upgraded host carries: a ``bytes_fetched=0`` done row over
    someone else's file. It never became the current owner, so the identity
    prong cannot see it -- and falling straight through to "different" would
    make row 479's needs_review diagnostic unreachable on exactly the databases
    it was written for."""
    p = _make_file(fresh, "row607-collide.mp4", b"scene-A-bytes")
    _log_real_transfer(p, _URL_OTHER, title="Scene A")
    lib_id = _library()[0]["id"]

    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history (site_id, site_name, url, status, filename, "
            "file_size, message, bytes_fetched, library_id, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))",
            (_SITE, _SITE_NAME, _URL_416, "done", p.name, p.stat().st_size,
             "already on disk", 0, lib_id))

    lib = _library()
    assert len(lib) == 1, lib
    assert lib[0]["history_id"] == _history(_URL_OTHER)[0]["id"], (
        "the seeded row must NOT be the current owner, or the identity prong "
        "handles it and the fallback is never reached")
    seeded = _history(_URL_416)[0]
    assert seeded["bytes_fetched"] == 0 and seeded["transfer_mode"] is None
    assert seeded["library_id"] == lib_id

    assert db.db_skip_identity(_URL_416, str(p)) == ("unproven", str(p)), (
        "row 479's needs_review diagnostic became unreachable for the upgraded "
        "host shape it exists for")


# ── Row 563: pruning must not delete the only proof ────────────────────────

def _age_rows(url, days=90):
    with db.db_conn() as cx:
        n = cx.execute(
            "UPDATE history SET ts = datetime('now', ?) WHERE url = ?",
            (f"-{days} days", url)).rowcount
    return n


def test_row563_pruning_keeps_the_newest_transfer_proving_row_per_url(fresh):
    """Row 563.  The healthy steady state's ONLY proof is its oldest row, so an
    age-based prune deletes exactly the evidence and turns a healthy repeated
    skip into a re-download plus a duplicate."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    _log_skip_shape(p)
    _log_skip_shape(p)
    # Unrelated old rows that prune MUST still remove.
    for i in range(3):
        db.db_log(_SITE, _SITE_NAME, f"{_URL_OTHER}/{i}", "failed", "", 0,
                  "boom", bytes_fetched=0)

    assert _age_rows(_URL_SKIP) == 3
    assert _age_rows(f"{_URL_OTHER}/0") == 1
    assert _age_rows(f"{_URL_OTHER}/1") == 1
    assert _age_rows(f"{_URL_OTHER}/2") == 1
    assert len(_history()) == 6

    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("same", str(p)), (
        "precondition: the url is skippable BEFORE the prune")

    removed = db.db_prune(30)

    assert removed > 0, "the prune deleted nothing, so it proves nothing"
    survivors = _history(_URL_SKIP)
    assert len(survivors) == 1, (
        f"exactly the transfer-proving row must survive: {survivors}")
    assert (survivors[0]["bytes_fetched"] or 0) > 0, survivors
    assert _history(f"{_URL_OTHER}/0") == [], (
        "the retention carve-out swallowed rows it had no business keeping")
    assert removed == 5, (
        f"expected 5 deletions (2 skips + 3 unrelated), got {removed}")

    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("same", str(p)), (
        "pruning turned a healthy repeated skip into a re-download (row 563)")


def test_row563_negative_control_prune_still_empties_a_dead_history(fresh):
    """Retention is keyed on a URL that can still be asked about, not a blanket
    refusal: rows whose proof value is nil are still deleted."""
    for i in range(4):
        db.db_log(_SITE, _SITE_NAME, f"{_URL_OTHER}/{i}", "failed", "", 0, "x",
                  bytes_fetched=0)
        assert _age_rows(f"{_URL_OTHER}/{i}") == 1
    assert len(_history()) == 4
    assert db.db_prune(30) == 4
    assert _history() == []


def test_row563_negative_control_a_recent_row_is_untouched(fresh):
    """The cutoff still decides: nothing younger than `days` is considered."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    _log_skip_shape(p)
    assert len(_history()) == 2
    assert db.db_prune(30) == 0
    assert len(_history()) == 2


# ── A7: the discriminator must not be manufacturable by the arms it judges ──

_NO_TRANSFER_DONE_SITES = [
    ("bulk_downloader/runner_transport.py", "already on disk"),
    ("bulk_downloader/runner_integrations.py", "Skipped (in Stash as scene"),
]


def test_row607_no_transfer_arm_may_never_stamp_a_transfer_mode():
    """A7: do not derive the expected set from the artifact under test.

    ``transfer_mode`` can only be trusted while every arm that transfers NOTHING
    omits it.  This parses the source (comments and docstrings are inside a text
    scan's denominator, so they must not count) and asserts a nonzero, exact
    denominator of no-transfer ``done`` db_log calls, each stamping no
    transfer_mode."""
    root = pathlib.Path(__file__).resolve().parents[1]
    found = 0
    for rel, marker in _NO_TRANSFER_DONE_SITES:
        src = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            if name != "db_log":
                continue
            args = [ast.unparse(a) for a in node.args]
            if len(args) < 4 or "'done'" not in args[3]:
                continue
            if not any(marker in a for a in args):
                continue
            hits.append(node)
        assert len(hits) == 1, (
            f"{rel}: expected exactly 1 no-transfer done db_log matching "
            f"{marker!r}, found {len(hits)} -- the denominator moved and this "
            f"gate no longer sees its subject")
        node = hits[0]
        kw = {k.arg for k in node.keywords}
        assert "transfer_mode" not in kw, (
            f"{rel}:{node.lineno} stamps a transfer_mode on a done row that "
            f"transferred nothing; db_skip_identity's proof is now "
            f"self-manufactured")
        bf = [k for k in node.keywords if k.arg == "bytes_fetched"]
        assert bf and ast.unparse(bf[0].value) == "0", (
            f"{rel}:{node.lineno} no longer records a measured zero")
        found += 1
    assert found == len(_NO_TRANSFER_DONE_SITES) == 2, found


def test_row607_the_success_arm_must_keep_stamping_what_it_transferred():
    """THE ACCEPT SIDE OF THE SAME GATE, and the one this file's own fixtures
    cannot see.

    Every 416 shape below is SEEDED through ``db_log`` with the kwargs measured
    from ``runner_transport.py``'s success path. If that call ever stopped
    passing ``transfer_mode``, real 416 rows would go back to NULL, the
    duplicate-on-resume defect would return -- and every test in this file
    would stay green, because the fixture manufactures a shape production no
    longer writes. That is the artifact-under-test problem on the accept side,
    so the production call site is pinned here."""
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "bulk_downloader/runner_transport.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", getattr(node.func, "attr", None)) != "db_log":
            continue
        args = [ast.unparse(a) for a in node.args]
        if len(args) < 4 or "'done'" not in args[3]:
            continue
        kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
        if kw.get("file_path") != "str(final_path)":
            continue
        hits.append((node, kw))
    assert len(hits) == 1, (
        "expected exactly 1 success-path done db_log in runner_transport.py "
        f"(file_path=str(final_path)), found {len(hits)} -- the denominator "
        "moved and this gate no longer sees its subject")
    node, kw = hits[0]
    assert kw.get("transfer_mode") == "transfer_mode", (
        f"runner_transport.py:{node.lineno} no longer stamps the transport it "
        f"used; db_skip_identity's 416 evidence would silently become NULL")
    assert kw.get("bytes_fetched") == "bytes_fetched", (
        f"runner_transport.py:{node.lineno} no longer records what it moved")

    # And the branch that BINDS 'http' must still be the one the 416 arm
    # returns through, or the name above carries nothing.
    assert 'transfer_mode="http"' in src, (
        "the http arm no longer names its transport")
    assert "if resp.status_code==416:" in src, (
        "the 416 resume-complete arm is gone; rows 560 and 561 no longer "
        "describe this transport")


# ── A dangling current link is not permission ──────────────────────────────

def test_an_orphaned_library_row_is_not_ownership(fresh):
    """Ported from the parked attempt's control, which was right.

    ``batch_ops.bulk_delete`` (POST /api/batch/delete) deletes history rows and
    does not repair ``library.history_id``. An unclaimed library row must not be
    read as this url's: an older url would then skip over a newer one's bytes
    whenever the newer url's row was deleted -- row 547 by another door, and
    UNKNOWN converted into permission."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    row_id = _history(_URL_SKIP)[0]["id"]
    lib = _library()
    assert len(lib) == 1 and lib[0]["history_id"] == row_id
    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("same", str(p)), (
        "precondition: the url owns the file BEFORE the out-of-band delete")

    with db.db_conn() as cx:
        cx.execute("DELETE FROM history WHERE id = ?", (row_id,))

    assert _history() == []
    lib = _library()
    assert len(lib) == 1 and lib[0]["history_id"] == row_id, (
        "the library row must still name the DELETED history row, or this "
        "control is not measuring a dangling link")

    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("unknown", None), (
        "a bare library row was accepted after its current identity was "
        "deleted out of band")


def test_row563_the_prune_repoints_the_link_it_breaks(fresh):
    """Retaining the evidence row is not enough on its own: the retained row
    must also be the one ``library.history_id`` names, because db_skip_identity
    refuses a dangling link (the control directly above)."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    _log_skip_shape(p)
    rows = _history(_URL_SKIP)
    assert len(rows) == 2
    proof_id, skip_id = rows[0]["id"], rows[1]["id"]
    assert _library()[0]["history_id"] == skip_id, (
        "precondition: the SKIP row is the current owner and is the row the "
        "prune will delete")
    assert _age_rows(_URL_SKIP) == 2

    assert db.db_prune(30) == 1
    assert [r["id"] for r in _history(_URL_SKIP)] == [proof_id]
    assert _library()[0]["history_id"] == proof_id, (
        "the prune left the library naming a deleted row, so the evidence it "
        "carefully retained is unreadable")
    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("same", str(p))


def test_row563_negative_control_a_prune_that_empties_a_path_leaves_it_unknown(
        fresh):
    """The repair repoints to a SURVIVOR; it does not invent one. A library row
    with no surviving completion stays unreadable, which is the safe
    direction."""
    p = _make_file(fresh, "row607-nulled.mp4")
    db.db_log(_SITE, _SITE_NAME, _URL_SKIP, "done", p.name, p.stat().st_size,
              "already on disk", bytes_fetched=0,
              file_path=str(p), title="no proof", title_source="page")
    row_id = _history(_URL_SKIP)[0]["id"]
    assert _library()[0]["history_id"] == row_id
    assert _age_rows(_URL_SKIP) == 1

    assert db.db_prune(30) == 1, "an unproving row must not be retained"
    assert _history() == []
    assert _library()[0]["history_id"] == row_id, (
        "the repair invented an owner for a library row with no survivor")
    assert db.db_skip_identity(_URL_SKIP, str(p)) == ("unknown", None)


# ── The degraded (pre-v9) schema arm, proved reachable ──────────────────────

def test_the_proof_predicate_degrades_to_the_shipped_rule_without_the_column():
    """``transfer_mode`` arrives with migration 9.  Naming a column that does
    not exist would raise inside ``db_skip_identity``'s bare handler and turn
    EVERY url on an unmigrated host into "unknown" -- a whole history silently
    converted into re-downloads.  Both arms of ``_transfer_proof_sql`` are
    exercised here, and a connection that cannot answer at all degrades too."""
    import sqlite3 as _sqlite3

    pre_v9 = _sqlite3.connect(":memory:")
    pre_v9.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, "
                   "status TEXT, url TEXT, bytes_fetched INTEGER)")
    assert db._transfer_proof_sql(pre_v9) is db._TRANSFER_PROOF_NO_MODE
    assert "transfer_mode" not in db._TRANSFER_PROOF_NO_MODE

    modern = _sqlite3.connect(":memory:")
    modern.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, status TEXT, "
                   "url TEXT, bytes_fetched INTEGER, transfer_mode TEXT)")
    assert db._transfer_proof_sql(modern) is db._TRANSFER_PROOF_WITH_MODE
    assert "transfer_mode" in db._TRANSFER_PROOF_WITH_MODE

    class _Blind:
        def execute(self, *_a, **_k):
            raise _sqlite3.OperationalError("no such table: history")

    assert db._transfer_proof_sql(_Blind()) is db._TRANSFER_PROOF_NO_MODE, (
        "an unmeasurable schema must degrade to the shipped row 544 rule, not "
        "raise into the caller's bare handler")
    pre_v9.close()
    modern.close()


def test_an_unmigrated_history_still_answers_the_shipped_row_544_rule(fresh):
    """End to end on a table rebuilt WITHOUT transfer_mode: the 416 shape loses
    its proof (correctly -- the column that carried it is gone) and the real
    transfer still skips.  Nothing raises, and db_prune still runs."""
    p416 = _make_file(fresh, "row607-416.mp4")
    preal = _make_file(fresh, "row607-real.mp4")
    _log_416_shape(p416)
    _log_real_transfer(preal, _URL_SKIP)

    with db.db_conn() as cx:
        cx.execute(
            "CREATE TABLE history_old AS SELECT id, site_id, site_name, url, "
            "status, filename, file_size, message, screenshot, honeypot_score, "
            "bytes_fetched, library_id, ts FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_old RENAME TO history")
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    assert "transfer_mode" not in cols, cols
    assert "bytes_fetched" in cols and "library_id" in cols, cols

    assert db.db_skip_identity(_URL_416, str(p416)) == ("unproven", str(p416))
    assert db.db_skip_identity(_URL_SKIP, str(preal)) == ("same", str(preal))
    assert db.db_prune(30) == 0


# ── The retention table must not survive its own prune ─────────────────────

def test_row563_a_second_prune_does_not_reuse_the_first_retention_table(fresh):
    """The history connection is pooled.  A surviving ``_bd_prune_keep`` would
    let a stale artifact decide a destructive operation on the next call."""
    p = _make_file(fresh, "row607-real.mp4")
    _log_real_transfer(p, _URL_SKIP)
    _log_skip_shape(p)
    assert _age_rows(_URL_SKIP) == 2
    assert db.db_prune(30) == 1
    assert len(_history(_URL_SKIP)) == 1

    # A different url, proving nothing, seeded AFTER the first prune.
    for i in range(2):
        db.db_log(_SITE, _SITE_NAME, f"{_URL_OTHER}/{i}", "done", "x.mp4", 1,
                  "already on disk", bytes_fetched=0)
        assert _age_rows(f"{_URL_OTHER}/{i}") == 1
    assert db.db_prune(30) == 2, (
        "the second prune retained rows the first prune's table named, or "
        "refused rows it should have deleted")
    assert len(_history(_URL_SKIP)) == 1, (
        "the surviving proof was deleted by the second prune")

    with db.db_conn() as cx:
        leftovers = cx.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='table' "
            "AND name='_bd_prune_keep'").fetchall()
    assert leftovers == [], "the retention temp table outlived the prune"
