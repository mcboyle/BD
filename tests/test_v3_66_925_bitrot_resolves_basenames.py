"""bitrot.verify_one tested a bare recorded basename against the CWD -- and WROTE.

Item 12, the last producer and the only one that PERSISTS its wrong answer.
`runner.py:2040` records `extra["filename"]` into `provenance.final_filename`,
and `runner_transport.py:1297` shows that key is the bare BASENAME -- `path` is
the separate key carrying the full path. `verify_one` fed the basename straight
to `Path(path).exists()`, which resolves against the PROCESS CWD.

WHY THIS ONE IS WORSE THAN ITS FOUR SIBLINGS, which only mis-REPORTED:

  * It WRITES. Every relative row failed `.exists()` and took the branch that
    calls `_record_issue(kind="missing")`, so the `integrity_issues` table
    accumulates a false "your file is gone" row for a file that is on disk.
  * IT NEVER CONVERGES. The `missing` branch returns BEFORE `_mark_verified`,
    so `last_verified_ts` stays 0 and `_candidates` (:100) re-selects the very
    same rows on the next run -- which writes the same false rows again. Growth
    is unbounded in the number of scans, not in the size of the library.
  * IT IS SCHEDULED. `bg_scheduler.py:254` registers `bitrot.nightly_scan` at
    86400s, so it compounds nightly with nobody asking for it.
  * IT RAISES AN ALARM. `alerts_engine.py:75` defines rule `bitrot_growing` on
    metric `bd_bitrot_open_issues`. The defect's signature is precisely a
    monotonically growing open-issue count, so the system alerts the operator
    about its own bookkeeping.

AND THE SCANNER'S OTHER THREE VERDICTS WERE STRUCTURALLY UNREACHABLE. `missing`
returns first, so on a library recorded with relative names `truncated`,
`modified` and `intact` could never be produced. A scan reporting 100% missing
was not measuring the library at all -- CLAUDE.md section 0, in a nightly job.

THE FIX REUSES `library_final._resolve_recorded` rather than restating it. That
resolver was written for exactly this at cut25b and hardened at v3.66.916; a
sixth hand-rolled `download_dir / fn` join is how the four siblings drifted
apart in the first place. Its four states map onto this module as:

    resolved   -> run the real size/hash checks
    absent     -> record `missing`   (unchanged meaning: genuinely gone)
    ambiguous  -> record NOTHING, kind="ambiguous"
    unknown    -> record NOTHING, kind="unknown"

`ambiguous` and `unknown` deliberately do NOT write. A row the scanner cannot
place is not evidence of rot, and persisting it is the defect this cut removes.
The resolver's own docstring refuses to guess first-match-wins for the same
reason: a size comparison against the wrong file reports drift that is an
artefact of the guess.

`download_dir` is keyword-only and defaults to "" -- the precedent at
library_final.py:242. Omitted, every relative row lands in `unknown` and the
scanner writes nothing rather than claiming the library is gone. That makes the
unconfigured nightly run a no-op instead of a vandal.

BOTH DIRECTIONS, because a fix that simply stopped writing would satisfy every
assertion above and destroy the scanner. `test_a_genuinely_absent_file_is_still
_recorded` and `test_a_modified_file_is_still_detected` are the over-correction
guards: bit-rot detection that cannot report bit-rot is not a fix.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


_INTACT = b"intact-payload-0123456789"
_FLAT = b"flat-payload-0123456789"
_NESTED = b"nested-payload-0123456789"
_TWIN = b"twin-payload-0123456789"


@pytest.fixture
def lib(clean_workdir):
    """A provenance ledger + download dir covering all four resolver states.

    clean_workdir chdirs to a tmp dir AND sets BD_INSTALL_DIR (CLAUDE.md
    section 5: BD_HOME does not govern the database, BD_INSTALL_DIR does), so a
    bare basename cannot accidentally resolve against the repo checkout and
    make a broken resolver look like it works.

      flat.mp4        directly under the dir              -> resolved, intact
      sub/nested.mp4  reachable ONLY via the basename index -> resolved, intact
      twin.mp4        two copies in different subdirs      -> ambiguous
      gone.mp4        recorded, never on disk              -> absent
      rotted.mp4      on disk, recorded sha is wrong       -> resolved, modified
      <abs>/away.mp4  an ABSOLUTE recorded path            -> resolved, intact
    """
    import bulk_downloader.db as db
    from bulk_downloader import bitrot as br
    from bulk_downloader import provenance as prov

    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="bitrot925_db_"), "q.db")
    db.db_init()

    dl = Path(tempfile.mkdtemp(prefix="bitrot925_dl_"))
    (dl / "sub").mkdir()
    (dl / "dup").mkdir()
    (dl / "flat.mp4").write_bytes(_FLAT)
    (dl / "sub" / "nested.mp4").write_bytes(_NESTED)
    (dl / "sub" / "twin.mp4").write_bytes(_TWIN)
    (dl / "dup" / "twin.mp4").write_bytes(_TWIN)
    (dl / "rotted.mp4").write_bytes(_INTACT)

    away_dir = Path(tempfile.mkdtemp(prefix="bitrot925_away_"))
    away = away_dir / "away.mp4"
    away.write_bytes(_INTACT)

    # ts is backdated so _candidates' min_age_days filter cannot exclude these
    # rows -- a run_scan test whose candidate query returns nothing would pass
    # every assertion below while measuring an empty set.
    old = 1.0
    seeded = {}
    for fn, payload, sha in [
        ("flat.mp4", _FLAT, _sha(_FLAT)),
        ("nested.mp4", _NESTED, _sha(_NESTED)),
        ("twin.mp4", _TWIN, _sha(_TWIN)),
        ("gone.mp4", b"", _sha(b"gone")),
        # recorded hash deliberately does NOT match what is on disk
        ("rotted.mp4", _INTACT, _sha(b"a-different-payload")),
        (str(away), _INTACT, _sha(_INTACT)),
    ]:
        rid = prov.record(site_id="s", source_url="u+" + fn,
                          final_filename=fn, file_size=len(payload),
                          sha256=sha, ts_finished=old)
        # Backdate ts so the min_age_days window includes it.
        with db.db_conn() as cx:
            cx.execute("UPDATE provenance SET ts=? WHERE id=?", (old, rid))
        seeded[fn] = rid
    # AFTER the seeding, and the order is load-bearing. `integrity_issues` and
    # provenance.last_verified_ts are both LAZILY created -- db_init() knows
    # about neither. _ensure_integrity_table adds the column with an ALTER
    # wrapped in a bare `except: pass`, which cannot tell "column already
    # exists" from "the provenance table does not exist yet", so calling it
    # before the first prov.record() silently leaves the column off and every
    # read of it fails with "no such column". Measured here, not reasoned:
    # this fixture did exactly that and cost a full RED cycle.
    br._ensure_integrity_table()
    try:
        yield br, dl, away, seeded
    finally:
        db.DB_PATH = saved


def _rows(kind=None):
    import bulk_downloader.db as db
    sql = "SELECT path, kind FROM integrity_issues"
    params: list = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    with db.db_conn() as cx:
        return [dict(r) for r in cx.execute(sql, params).fetchall()]


def _row_for(br, seeded, fn):
    import bulk_downloader.db as db
    with db.db_conn() as cx:
        r = cx.execute("SELECT id, source_url, final_filename, file_size, "
                       "sha256, last_verified_ts, ts FROM provenance "
                       "WHERE id=?", (seeded[fn],)).fetchone()
    return dict(r)


def test_a_resolvable_basename_writes_no_missing_row(lib):
    """RED: the whole defect in one assertion.

    flat.mp4 is on disk with a matching hash. The old code resolved the bare
    basename against the CWD, missed, and PERSISTED an integrity_issues row
    saying the operator's file was gone.
    """
    br, dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "flat.mp4"), download_dir=str(dl))
    assert out["kind"] == "intact", (
        "flat.mp4 is on disk with a matching hash; verify_one said %r -- a bare "
        "basename is being tested against the CWD" % (out["kind"],))
    assert _rows() == [], (
        "verify_one PERSISTED %r for a file that exists. This is the half that "
        "makes item 12's last producer data corruption rather than a bad "
        "report." % (_rows(),))


def test_a_nested_basename_resolves_through_the_index(lib):
    """The recorded basename has already lost its subdirectory.

    A flat `download_dir / fn` join misses sub/nested.mp4 and would still pass
    the test above by luck. This is the assertion that forces the index.
    """
    br, dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "nested.mp4"), download_dir=str(dl))
    assert out["kind"] == "intact", out
    assert _rows() == [], _rows()


def test_an_absolute_recorded_path_still_works(lib):
    """Not every row is relative; the fix must not break the ones that worked."""
    br, dl, away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, str(away)), download_dir=str(dl))
    assert out["kind"] == "intact", out
    assert _rows() == [], _rows()


def test_an_ambiguous_basename_records_nothing(lib):
    """Two candidates is not evidence of rot.

    Folding ambiguous into missing is the shape the resolver's own docstring
    refuses: a size or hash comparison against the wrong twin reports a drift
    that is an artefact of the guess.
    """
    br, dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "twin.mp4"), download_dir=str(dl))
    assert out["kind"] == "ambiguous", out
    assert _rows() == [], (
        "an unplaceable row was persisted as %r" % (_rows(),))


def test_no_download_dir_records_nothing(lib):
    """The safe default, and the one the nightly scheduler actually hits.

    bg_scheduler.py:254 calls run_scan with no download_dir. Before this cut
    that meant "claim the entire library is missing, nightly". It must mean
    "nothing can be decided".
    """
    br, _dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "flat.mp4"))
    assert out["kind"] == "unknown", out
    assert _rows() == [], _rows()


def test_a_genuinely_absent_file_is_still_recorded(lib):
    """OVER-CORRECTION GUARD.

    A "fix" that simply stopped writing satisfies every assertion above. This
    is the one it fails. gone.mp4 is recorded and is not on disk anywhere under
    the download dir, so it is genuinely absent and MUST persist a row.
    """
    br, dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "gone.mp4"), download_dir=str(dl))
    assert out["kind"] == "missing", out
    rows = _rows("missing")
    assert len(rows) == 1, (
        "a genuinely absent file must still be recorded; got %r. Bit-rot "
        "detection that cannot report a missing file is not a fix." % (rows,))


def test_a_modified_file_is_still_detected(lib):
    """OVER-CORRECTION GUARD, and proof the scan is not short-circuiting.

    rotted.mp4 is on disk -- so it RESOLVES -- but its content hash does not
    match the recorded one. Before this cut this verdict was structurally
    unreachable: the `missing` branch returned first for every relative row, so
    `modified`, `truncated` and `intact` could never be produced at all.
    """
    br, dl, _away, seeded = lib
    out = br.verify_one(_row_for(br, seeded, "rotted.mp4"), download_dir=str(dl))
    assert out["kind"] == "modified", (
        "a resolved file with a mismatched hash must report modified, not %r "
        "-- resolution must hand off to the real checks, not replace them"
        % (out["kind"],))
    rows = _rows("modified")
    assert len(rows) == 1, rows


def test_an_intact_row_is_stamped_and_not_rescanned(lib):
    """The unbounded-growth half, asserted directly.

    `missing` returns before `_mark_verified`, so the old code left
    last_verified_ts at 0 and `_candidates` re-selected the same rows every
    night -- writing the same false rows again. An intact verdict must stamp,
    so the second scan does not re-examine it.
    """
    br, dl, _away, seeded = lib
    br.verify_one(_row_for(br, seeded, "flat.mp4"), download_dir=str(dl))
    after = _row_for(br, seeded, "flat.mp4")
    assert float(after["last_verified_ts"] or 0) > 0, (
        "an intact row was not stamped; _candidates will re-select it forever")


def test_run_scan_threads_download_dir_and_writes_no_false_rows(lib):
    """End-to-end: the scheduler's entry point, not just the leaf.

    Fixing verify_one while run_scan keeps calling it without a download_dir
    would leave the nightly job exactly as wrong as before. Only gone.mp4 and
    rotted.mp4 are real findings; the other four must produce nothing.
    """
    br, dl, _away, _seeded = lib
    summary = br.run_scan(scan_fraction=1.0, min_age_days=0, max_files=100,
                          download_dir=str(dl))
    assert summary["checked"] == 6, summary
    assert summary["missing"] == 1, summary
    assert summary["modified"] == 1, summary
    assert summary["intact"] == 3, summary
    assert summary["ambiguous"] == 1, summary
    assert len(_rows()) == 2, (
        "run_scan persisted %d rows; only gone.mp4 (missing) and rotted.mp4 "
        "(modified) are real findings" % (len(_rows()),))


def test_a_second_scan_does_not_duplicate_findings(lib):
    """The compounding property, at the scheduler's own granularity.

    Two nightly runs over an unchanged library must not double the open-issue
    count -- which is exactly what fired `alerts_engine`'s `bitrot_growing`
    rule on a healthy library.
    """
    br, dl, _away, _seeded = lib
    br.run_scan(scan_fraction=1.0, min_age_days=0, max_files=100,
                download_dir=str(dl))
    first = len(_rows())
    br.run_scan(scan_fraction=1.0, min_age_days=0, max_files=100,
                download_dir=str(dl))
    assert len(_rows()) == first, (
        "the second scan added %d rows over an unchanged library; open issues "
        "grow without bound and trip the bitrot_growing alert"
        % (len(_rows()) - first,))
