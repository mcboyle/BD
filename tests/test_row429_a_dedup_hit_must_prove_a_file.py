"""Row 429: a 'done' row that produced no file must not dedup every future download.

THE QUESTION THIS FILE DECIDES.  ``_dedup_preflight`` converts a queued URL into
``skipped_duplicate`` before the browser is ever opened, so its evidence has to
answer "does BD already HAVE this?".  Row 544 (shipped) made it require a
MEASURED transfer -- ``bytes_fetched IS NOT NULL AND bytes_fetched > 0`` -- and
that closed the no-download-dir click arm, whose row records a measured zero.

IT DID NOT CLOSE THE PROBE ARM.  GCW probe mode (``runner_transport
._do_probe_fetch``) opens the media URL, reads at most 256 KB, aborts the stream
and WRITES NO FILE -- ``dl_dir`` is ``None`` on that path.  It then logs

    db_log(..., "done", suggested, recv, note, bytes_fetched=recv, ...)

with a nonempty filename and a POSITIVE byte count, because those bytes really
did cross the wire.  The call site's own comment says a consumer wanting "a file
was produced" must also require one.  ``_dedup_preflight`` is that consumer, and
requiring a measured transfer is not requiring a file: the probe row satisfies
row 544's predicate exactly as a real download does.  Every later queue of that
URL is answered "Duplicate of history #N" -- permanently, unless the operator
finds ``force_download`` -- so a URL that was only PROBED can never be
downloaded.  History says done, no file exists, and the dedup gate launders a
no-file record into proof of a prior successful download.

WHY NO COLUMN OVER ``history`` ALONE CAN SEPARATE THEM.  Measured across the
tree at v3.66.1432 by parsing every ``db_log(..., 'done', ...)`` call site (17 of
them): the probe row carries a nonempty ``filename``, a positive ``file_size``,
a positive ``bytes_fetched`` and a NULL ``transfer_mode``.  ``transfer_mode`` is
not the discriminator either -- four file-PRODUCING writers omit it
(``runner_extractors.py`` dl8, both ``runner_integrations.py`` backend arms, and
both ``runner_challenge.py`` fallbacks), and on the deploy host's live history 8
of 8 real pre-v9 downloads carry a NULL ``transfer_mode`` while 0 rows are
probe-shaped.  Gating on it would have refused those 8 genuine downloads.

THE EVIDENCE IS THE FILE.  ``db_log``'s done path calls ``library.library_record``
only when the caller handed it an ABSOLUTE path, and deliberately records nothing
for "a caller that ... produced no file at all, like the GCW probe".
``library_record`` backfills ``history.library_id`` on both its UPDATE and its
INSERT path.  So the record can say "this row produced THIS file", and the
contract this file pins is:

    A 'done' row is proof of a prior download only when it produced a file the
    library still attributes to it, that file is on disk NOW, and its recorded
    size still describes the bytes at that path.

The last clause is the shipped row 503 rule, applied at the second seam so the
two cannot disagree: ``db_skip_identity`` already refuses "same" when the
recorded ``library.file_size`` no longer matches ``os.path.getsize``.

EVERY REFUSAL DIRECTION IS A DOWNLOAD.  Missing ``library_id`` column, missing
``library`` table, unattributed row, empty ``file_path``, vanished file, failed
stat, non-integer or negative recorded size, size mismatch, and the outer
fail-soft ``except`` all return ``None``, and ``None`` means "proceed with the
download".  Nothing in the new predicate can fail toward a skip.  That asymmetry
is the point: a needless re-download costs bandwidth and is reversible; a
permanent silent skip of content the operator does not have is not.
"""
from __future__ import annotations

import ast
import importlib
import logging
import pathlib

import pytest

from bulk_downloader import db
from bulk_downloader import library as _library
from bulk_downloader import migrations as _migrations
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_preflight = SiteRunner._dedup_preflight  # unbound; called with a stub self

_SITE = "row429site"
_SITE_NAME = "Row 429 Site"
_URL = "https://members.example.test/scene/row429"
_OTHER = "https://members.example.test/scene/row429-other"

# The probe's own numbers: at most 256 KB sampled, nothing saved.
_PROBE_RECV = 262144


class _Stub:
    """Exactly the surface ``_dedup_preflight`` touches, and nothing else."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.log = logging.getLogger("row429")


@pytest.fixture
def fresh(clean_workdir):
    """A MIGRATED, empty database -- history WITH ``library_id``, plus library.

    ``db_init`` alone creates the pre-migration history shape, which has no
    ``library_id`` column at all.  A test running on that schema would see the
    new predicate answer "not proven" for EVERY url, and every assertion below
    would pass for a reason that has nothing to do with the probe.  The
    preconditions assert the migrated shape so the file cannot go green on an
    unrelated early refusal.
    """
    db.db_init()
    result = _migrations.apply_pending(backup_first=False)
    assert result.get("errors", 1) == 0, result
    _library._ensure_schema()
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")
        cx.execute("DELETE FROM library")
        hist_cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
        lib_cols = {r[1] for r in cx.execute("PRAGMA table_info(library)")}
    assert "library_id" in hist_cols, (
        "the fixture must build the MIGRATED history shape; without "
        f"library_id every verdict below is vacuous. cols={sorted(hist_cols)}")
    assert {"id", "file_path", "file_size"}.issubset(lib_cols), sorted(lib_cols)
    assert _history() == [], "the fixture did not start empty"
    return clean_workdir


def _history(url=None):
    with db.db_conn() as cx:
        sql = ("SELECT id, url, status, filename, file_size, bytes_fetched, "
               "transfer_mode, library_id FROM history")
        params = ()
        if url is not None:
            sql += " WHERE url=?"
            params = (url,)
        return [dict(r) for r in cx.execute(sql + " ORDER BY id", params)]


def _library_rows():
    with db.db_conn() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT id, file_path, file_size, history_id FROM library ORDER BY id")]


def _seed_probe(url=_URL, recv=_PROBE_RECV):
    """The shape ``_do_probe_fetch``'s ``outcome == "done"`` arm writes.

    Positional arguments mirror the production call exactly: a suggested
    basename, ``file_size=recv``, and NO ``file_path`` keyword -- the probe has
    no download_dir and produces nothing to record.
    """
    db.db_log(_SITE, _SITE_NAME, url, "done", "row429-probe.mp4", recv,
              f"probe ok: first {recv} of ?; video/mp4 (aborted - no file saved)",
              bytes_fetched=recv)


def _seed_real_download(tmp_path, url=_URL, name="row429-real.mp4",
                        payload=b"row429 real bytes" * 64, mode="http"):
    """A genuine completion: bytes on disk, an ABSOLUTE path handed to db_log.

    This is the shape ``runner_transport``'s success ``db_log`` writes, and it
    is what makes ``library_record`` run and backfill ``history.library_id``.
    """
    target = pathlib.Path(tmp_path) / name
    target.write_bytes(payload)
    db.db_log(_SITE, _SITE_NAME, url, "done", target.name, len(payload),
              "", bytes_fetched=len(payload), transfer_mode=mode,
              file_path=str(target))
    return target


# ── THE DEFECT ──────────────────────────────────────────────────────────────

def test_a_probe_row_is_not_proof_that_the_url_was_downloaded(fresh):
    """RED at v3.66.1432 (797aa3fe): preflight returns "Duplicate of history #N".

    Expected failure on the defective parent::

        AssertionError: a 'done' row that produced NO FILE was accepted as
        proof of a prior download: 'Duplicate of history #1 (row429-probe.mp4...)'
    """
    _seed_probe()

    # ── PRECONDITIONS, every one asserted before any verdict ──
    rows = _history(_URL)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == "done"
    assert row["filename"] == "row429-probe.mp4", (
        "the probe records a SUGGESTED filename, so a predicate over "
        "`filename` alone cannot see the defect")
    assert row["file_size"] == _PROBE_RECV and row["bytes_fetched"] == _PROBE_RECV
    assert row["transfer_mode"] is None, (
        "the probe runs no transport arm, so it stamps no transfer_mode")
    assert row["library_id"] is None, (
        "the probe hands db_log no absolute path, so library_record never "
        f"runs and no attribution exists; got library_id={row['library_id']!r}")
    assert _library_rows() == [], (
        "the probe must have produced NO library row -- that absence is the "
        "whole evidence this test is about")
    # No file was produced anywhere under the isolated work dir.
    assert list(pathlib.Path(fresh).glob("*.mp4")) == [], (
        "the probe fixture must not create a file")

    # THE LATER REFUSAL MUST NOT LAUNDER THE RESULT.  Row 544's shipped
    # predicate is `bytes_fetched IS NOT NULL AND bytes_fetched > 0`, and this
    # row SATISFIES it -- so a None below is the new file evidence refusing,
    # never row 544 refusing for us.
    with db.db_conn() as cx:
        assert cx.execute(
            "SELECT 1 FROM history WHERE url=? AND status='done' "
            "AND bytes_fetched IS NOT NULL AND bytes_fetched > 0 LIMIT 1",
            (_URL,)).fetchone() is not None, (
                "the seeded probe row must PASS row 544's transfer predicate, "
                "otherwise this test measures row 544 and not row 429")

    stub = _Stub()
    assert "dedup_exact_url" not in stub.config, (
        "the shipped config leaves this key absent so the arm defaults ON; a "
        "fixture that set it would not be measuring the live shape")

    msg = _preflight(stub, _URL, {})
    assert msg is None, (
        "a 'done' row that produced NO FILE was accepted as proof of a prior "
        f"download: {msg!r}")


def test_the_lookup_itself_refuses_the_probe_row(fresh):
    """The same verdict at the db seam, where row 429 locates the defect."""
    _seed_probe()
    assert _history(_URL)[0]["library_id"] is None
    assert db.db_find_url_in_history(_URL) is None, (
        "db_find_url_in_history's docstring promises 'the most recent "
        "successfully-downloaded row'; a probe downloaded nothing")


def test_the_probe_producer_really_writes_that_shape():
    """The seeded fixture is the SHIPPED shape, not a shape this file invented.

    A7: do not derive the expected set solely from the artifact under test.
    This parses the production call site instead of trusting the fixture.
    """
    src = pathlib.Path(
        _library.__file__).with_name("runner_transport.py").read_text("utf-8")
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "db_log":
            continue
        args = [ast.unparse(a) for a in node.args]
        kw = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
        if (len(args) > 3 and args[3].strip("'\"") == "done"
                and kw.get("bytes_fetched") == "recv"):
            found.append((node.lineno, args, kw))
    assert len(found) == 1, (
        f"expected exactly one probe 'done' call site in runner_transport.py, "
        f"found {len(found)}: {[f[0] for f in found]}")
    _lineno, args, kw = found[0]
    assert args[4] == "suggested", args
    assert args[5] == "recv", ("the probe records the sampled count as "
                               f"file_size too, so neither column is free: {args}")
    assert "file_path" not in kw, (
        "the probe now hands db_log a path; if it produces a file this test's "
        f"premise is gone and row 429 must be re-derived. kw={kw}")
    assert "transfer_mode" not in kw, kw


# ── ROW 429'S SECOND PRODUCER (already closed by row 544; pinned here) ──────

def test_a_click_with_no_download_dir_is_not_proof(fresh):
    """GREEN on the parent already -- row 544 closed this arm. Pinned so a
    change to the file evidence cannot silently re-open it."""
    db.db_log(_SITE, _SITE_NAME, _URL, "done", "", 0, "", bytes_fetched=0)
    rows = _history(_URL)
    assert len(rows) == 1 and rows[0]["filename"] == "" and rows[0]["bytes_fetched"] == 0
    assert _preflight(_Stub(), _URL, {}) is None


# ── NEGATIVE CONTROLS ───────────────────────────────────────────────────────

def test_negative_control_a_genuine_prior_download_still_dedups(fresh):
    """The fix must not turn a legitimate skip into a re-download."""
    target = _seed_real_download(fresh)

    rows = _history(_URL)
    assert len(rows) == 1 and rows[0]["bytes_fetched"] > 0
    assert rows[0]["library_id"] is not None, (
        "the fixture must have produced a library attribution; without it "
        "this control proves nothing about the fix")
    lib = _library_rows()
    assert len(lib) == 1 and lib[0]["file_path"] == str(target)
    assert target.is_file() and target.stat().st_size == lib[0]["file_size"]

    msg = _preflight(_Stub(), _URL, {})
    assert msg is not None and msg.startswith("Duplicate of history #"), (
        f"a genuine prior download stopped deduping: {msg!r}")
    assert str(rows[0]["id"]) in msg, msg


def test_negative_control_a_second_genuine_url_is_untouched(fresh):
    """A different URL is not laundered by the first URL's evidence."""
    _seed_real_download(fresh, url=_URL, name="row429-a.mp4")
    assert _preflight(_Stub(), _OTHER, {}) is None
    assert _preflight(_Stub(), _URL, {}) is not None


def test_negative_control_force_download_still_bypasses_dedup(fresh):
    _seed_real_download(fresh)
    assert _preflight(_Stub(), _URL, {}) is not None
    assert _preflight(_Stub(), _URL, {"force_download": True}) is None


def test_negative_control_exclude_site_still_excludes(fresh):
    _seed_real_download(fresh)
    assert db.db_find_url_in_history(_URL, exclude_site=_SITE) is None
    assert db.db_find_url_in_history(_URL) is not None


def test_negative_control_a_pre_v8_null_byte_count_is_never_proof(fresh):
    """Row 429's own clause: unmeasurable file evidence is UNKNOWN.

    The row is fully attributed and its file is on disk -- so the ONLY thing
    refusing is the NULL byte count. Green on the parent (row 544); pinned so
    the new file evidence cannot be read as permission on its own.
    """
    target = pathlib.Path(fresh) / "row429-legacy.mp4"
    target.write_bytes(b"legacy" * 100)
    db.db_log(_SITE, _SITE_NAME, _URL, "done", target.name, target.stat().st_size,
              "", file_path=str(target))  # bytes_fetched defaults to NULL
    rows = _history(_URL)
    assert len(rows) == 1 and rows[0]["bytes_fetched"] is None
    assert rows[0]["library_id"] is not None, (
        "the precondition of this control is an ATTRIBUTED row -- the file "
        "evidence must pass so the byte count is what refuses")
    assert target.is_file()
    assert _preflight(_Stub(), _URL, {}) is None


# ── ROW 429'S TITLE: A DONE ROW WITH NO FILE ────────────────────────────────

def test_a_prior_download_whose_file_is_gone_stops_deduping(fresh):
    """RED at v3.66.1432: preflight still answers "Duplicate of history #N".

    Expected failure on the defective parent::

        AssertionError: the attributed file is gone, so nothing proves BD has
        this content, yet the download was suppressed: 'Duplicate of history #1 ...'
    """
    target = _seed_real_download(fresh)
    assert _preflight(_Stub(), _URL, {}) is not None, (
        "precondition: it must dedup BEFORE the file is removed")

    target.unlink()
    assert not target.exists()
    assert len(_library_rows()) == 1, (
        "the library row must SURVIVE the file's removal -- that survival is "
        "exactly what used to be read as proof")
    assert _history(_URL)[0]["library_id"] is not None

    msg = _preflight(_Stub(), _URL, {})
    assert msg is None, (
        "the attributed file is gone, so nothing proves BD has this content, "
        f"yet the download was suppressed: {msg!r}")


def test_a_file_replaced_out_of_band_stops_deduping(fresh):
    """RED at v3.66.1432. The shipped row 503 rule, at the preflight seam.

    ``db_skip_identity`` already refuses "same" when the recorded
    ``library.file_size`` no longer describes the bytes at that path. Preflight
    decided the same question from ``isfile`` alone, so the two seams
    disagreed: the identity seam refused and the preflight seam skipped first,
    which meant the identity seam never ran.
    """
    target = _seed_real_download(fresh)
    recorded = _library_rows()[0]["file_size"]
    assert _preflight(_Stub(), _URL, {}) is not None, "precondition: dedups first"

    replacement = b"x" * (recorded + 4096)
    target.write_bytes(replacement)
    observed = target.stat().st_size
    assert observed - recorded == 4096, (
        f"the replacement must differ by a named nonzero delta: "
        f"recorded={recorded} observed={observed}")
    assert _library_rows()[0]["file_size"] == recorded, (
        "the library must still carry the CONTRARY measurement")

    msg = _preflight(_Stub(), _URL, {})
    assert msg is None, (
        f"a file replaced out of band was accepted as the recorded work: {msg!r}")

    # And the refusal must not have rewritten the contrary measurement away.
    assert _library_rows()[0]["file_size"] == recorded


# ── A7 SELF-AUDIT: THE NEW CHECK CANNOT FAIL OPEN ───────────────────────────

def test_an_unmeasurable_schema_refuses_rather_than_dedups(fresh):
    """No library table at all -> UNKNOWN -> download. Never permission."""
    _seed_real_download(fresh)
    assert _preflight(_Stub(), _URL, {}) is not None, "precondition: dedups first"
    with db.db_conn() as cx:
        cx.execute("DROP TABLE library")
        assert cx.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='library'").fetchone()[0] == 0
    assert db.db_find_url_in_history(_URL) is None
    assert _preflight(_Stub(), _URL, {}) is None


def test_a_pre_migration_history_shape_refuses_rather_than_dedups(clean_workdir):
    """``db_init`` alone has no ``library_id``: the schema cannot express the
    evidence, so nothing is proven and the download proceeds."""
    db.db_init()
    with db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "library_id" not in cols, (
        f"this control requires the PRE-migration shape; got {sorted(cols)}")
    db.db_log(_SITE, _SITE_NAME, _URL, "done", "x.mp4", 4096, "",
              bytes_fetched=4096)
    assert db.db_find_url_in_history(_URL) is None
    assert _preflight(_Stub(), _URL, {}) is None


def test_a_stat_failure_is_unknown_not_a_match(fresh, monkeypatch):
    """An unmeasurable size is UNKNOWN, never "matches"."""
    _seed_real_download(fresh)
    assert _preflight(_Stub(), _URL, {}) is not None, "precondition: dedups first"

    calls = {"n": 0}
    real_getsize = db._os.path.getsize

    def _boom(path):
        calls["n"] += 1
        raise OSError(5, "simulated EIO")

    monkeypatch.setattr(db._os.path, "getsize", _boom)
    assert db.db_find_url_in_history(_URL) is None
    assert calls["n"] > 0, (
        "the stat boundary must actually have been reached; a green here with "
        "zero calls would mean an earlier refusal answered instead")
    monkeypatch.setattr(db._os.path, "getsize", real_getsize)
    assert db.db_find_url_in_history(_URL) is not None


def test_the_fail_soft_except_still_never_blocks_a_download(fresh, monkeypatch):
    """The deliberate fail-soft stays intact: any error -> None -> download."""
    _seed_real_download(fresh)

    def _explode(*a, **k):
        raise RuntimeError("simulated connection failure")

    # bd_module_wipe drops bulk_downloader.* between tests, so the module
    # object this FILE imported is not necessarily the one
    # ``_dedup_preflight``'s call-time ``from .db import db_conn`` resolves.
    # Patch the live one, or the patch silently applies to nothing and the
    # test measures an unpatched call.
    live_db = importlib.import_module("bulk_downloader.db")
    monkeypatch.setattr(live_db, "db_conn", _explode)
    assert live_db.db_find_url_in_history(_URL) is None
    assert _preflight(_Stub(), _URL, {}) is None
