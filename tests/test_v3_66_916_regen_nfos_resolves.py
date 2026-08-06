"""regen_nfos_from_history tested a bare recorded basename against the CWD.

Item 12(d). `runner_transport.py` records `final_path.name` -- a bare BASENAME,
not a path. `regen_nfos_from_history` fed that straight to `Path(fn).exists()`,
which resolves against the PROCESS CWD, so on the box every relative row failed
the check and was banked as `missing_files`. The sibling readers in this module
stopped doing that at cut25b; this one never did.

THE DEFECT HAS TWO EXPRESSIONS, and the register only records the first:

    :521   if not fn or not Path(fn).exists():      -> missing_files
    :524   nfo_path = Path(fn).with_suffix(".nfo")  -> the SKIP check

Routing only the first through the resolver leaves the second reading the CWD,
where no sidecar is ever found -- so `skipped` stays 0 and `overwrite=False`
silently rewrites every NFO it was supposed to leave alone.
`test_an_existing_sidecar_is_skipped` is the guard for that half, and it fails a
half-applied fix.

WHY missing_files WAS THE WRONG BUCKET FOR THREE DIFFERENT THINGS.
`_resolve_recorded` (:197) reports four states, and its own docstring says
"ambiguous and unknown are deliberately NOT folded into absent" -- guessing
first-match-wins would write a sidecar next to the wrong video. The old code
folded all three non-resolved states into `missing_files`, so the endpoint
reported files as missing when the truth was "there are two candidates" or
"you gave me nothing to resolve against". Two counters split them out:

    absent     -> missing_files   (unchanged meaning: genuinely gone)
    ambiguous  -> ambiguous       (several candidates; refusing to guess)
    unknown    -> unknown         (no download_dir; nothing can be decided)

The names are `_resolve_recorded`'s own state strings verbatim, matching the
`states` histogram precedent at :363-383, so a reader maps counter to state
with no translation step.

ADDITIVE AND NON-BREAKING, verified rather than assumed: the only test that
touches this return shape uses key MEMBERSHIP (`k in j`,
test_v3_66_522_dead_endpoints.py:65-66), no test asserts key-set equality, and
RegenNfosResult carries an index signature at frontend/src/lib/api-types.ts:1164
so the two new keys are type-legal with zero TS edits. (The register cites
frontend/src/types/api-types.ts, which does not exist.)

`download_dir` is OPTIONAL, defaulting to "" -- the precedent at :242. The
shipped SPA sends only {dry_run:true}, so requiring it would break the panel
immediately. Omitted, every relative row lands in `unknown`: the endpoint says
it could not decide instead of claiming the files are missing.
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

_KEYS = {"written", "skipped", "missing_files", "errors", "ambiguous", "unknown"}


@pytest.fixture
def lib(clean_workdir):
    """A download dir covering all four resolver states plus the skip path.

    clean_workdir chdirs to a tmp dir AND sets BD_INSTALL_DIR (CLAUDE.md
    section 5: BD_HOME does not govern the database, BD_INSTALL_DIR does), so a
    bare basename cannot accidentally resolve against the repo.

      flat.mp4          resolved directly under the dir
      sub/nested.mp4    resolved only via the basename index -- proves the fix
                        is not a flat `download_dir / fn` join
      twin.mp4          TWO copies in different subdirs -> ambiguous
      keepme.mp4        already has keepme.nfo beside it -> skipped
      gone.mp4          recorded, never on disk -> absent
      <abs>/away.mp4    an ABSOLUTE recorded path outside the dir -> resolved
    """
    import bulk_downloader.db as db
    from bulk_downloader import library_final as lf

    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="regen916_db_"), "q.db")
    db.db_init()

    dl = Path(tempfile.mkdtemp(prefix="regen916_dl_"))
    (dl / "sub").mkdir()
    (dl / "dup").mkdir()
    (dl / "flat.mp4").write_bytes(b"\0" * 10)
    (dl / "sub" / "nested.mp4").write_bytes(b"\0" * 10)
    (dl / "sub" / "twin.mp4").write_bytes(b"\0" * 10)
    (dl / "dup" / "twin.mp4").write_bytes(b"\0" * 10)
    (dl / "keepme.mp4").write_bytes(b"\0" * 10)
    (dl / "keepme.nfo").write_text("<original/>", encoding="utf-8")

    away_dir = Path(tempfile.mkdtemp(prefix="regen916_away_"))
    away = away_dir / "away.mp4"
    away.write_bytes(b"\0" * 10)

    for url, fn in [("u1", "flat.mp4"), ("u2", "nested.mp4"),
                    ("u3", "twin.mp4"), ("u4", "keepme.mp4"),
                    ("u5", "gone.mp4"), ("u6", str(away))]:
        db.db_log(site_id="s", site_name="S", url=url, status="done",
                  filename=fn, file_size=10)
    try:
        yield lf, dl, away
    finally:
        db.DB_PATH = saved


def test_a_resolvable_basename_is_not_reported_missing(lib):
    """RED: every relative row was banked as missing_files.

    flat.mp4, sub/nested.mp4 and keepme.mp4 are all on disk. Only gone.mp4 is
    genuinely absent, and twin.mp4 is ambiguous rather than missing -- so
    missing_files must be exactly 1, not 5.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=True)
    assert out["missing_files"] == 1, (
        "only gone.mp4 is absent; regen reported missing_files=%r -- a bare "
        "basename is being tested against the CWD" % (out["missing_files"],))


def test_a_nested_basename_resolves_through_the_index(lib):
    """The recorded basename has already lost its subdirectory.

    A flat `download_dir / fn` join would miss sub/nested.mp4 entirely and
    still satisfy the test above by luck, since a missed row lands in
    `unknown`/`absent` rather than being counted.

    Four rows resolve: flat, nested, keepme and the absolute one. keepme is
    SKIPPED rather than written -- the sidecar check sits above the dry_run
    branch in the loop -- so three would-writes remain.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=True)
    # flat + nested + away are written; keepme is skipped (its sidecar exists,
    # and the skip check runs BEFORE the dry_run branch).
    assert out["written"] == 3, (
        "expected flat, nested and the absolute row to be would-writes; got "
        "written=%r -- a flat join cannot see sub/nested.mp4" % (out["written"],))


def test_an_ambiguous_basename_is_not_counted_missing(lib):
    """twin.mp4 exists TWICE. Refusing to guess is the point.

    _resolve_recorded's docstring: first-match-wins would let a sidecar be
    written next to the wrong video. So the row is neither written nor
    missing -- it gets its own counter.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=True)
    assert out["ambiguous"] == 1, out
    assert out["missing_files"] == 1, (
        "an ambiguous row leaked into missing_files: %r" % (out,))


def test_without_a_download_dir_rows_are_unknown_not_missing(lib):
    """The default path, and the one the shipped SPA actually takes.

    Library.tsx sends only {dry_run:true}. With no dir, nothing can be
    resolved -- and saying so is different from claiming the files are gone.
    The absolute row still resolves, because it needs no dir.
    """
    lf, _dl, _away = lib
    out = lf.regen_nfos_from_history(dry_run=True)
    assert out["unknown"] == 5, out
    assert out["missing_files"] == 0, (
        "with no download_dir nothing is known to be absent; got %r" % (out,))
    assert out["written"] == 1, (
        "the absolute recorded path resolves without a dir; got %r" % (out,))


def test_an_absolute_recorded_path_still_resolves(lib):
    """BACK-COMPAT GUARD. Passes before AND after.

    Rows recorded before the basename regression carry a full path, and
    _resolve_recorded's first branch handles them. This is the direction the
    FIX could break -- routing everything through a download_dir-relative
    lookup would strand them.
    """
    lf, dl, away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=False)
    assert away.with_suffix(".nfo").exists(), (
        "the absolute row did not get a sidecar: %r" % (out,))


def test_an_existing_sidecar_is_skipped(lib):
    """THE SECOND DEFECT, and the one a half-applied fix leaves behind.

    keepme.nfo already sits beside keepme.mp4. With overwrite=False it must be
    left alone. If `nfo_path` is still built as Path(fn).with_suffix(".nfo")
    from the BARE basename, that path is CWD-relative, never exists, and the
    file is silently rewritten -- so this fails with skipped=0 and the original
    contents destroyed.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=False)
    assert out["skipped"] == 1, (
        "keepme.nfo exists and overwrite is False; got skipped=%r -- the skip "
        "check is still resolving against the CWD" % (out["skipped"],))
    assert (dl / "keepme.nfo").read_text(encoding="utf-8") == "<original/>", (
        "an existing sidecar was overwritten despite overwrite=False")


def test_a_real_write_lands_beside_the_resolved_file(lib):
    """write_nfo takes a str and REJECTS a Path -- silently.

    library_final.py:106 is `if not video_path or not isinstance(video_path,
    str): return None`, and _resolve_recorded returns a Path. Handing the
    resolved path over unwrapped makes write_nfo return None for every row, so
    each one banks as `errors` and not one sidecar is written -- with no
    exception raised anywhere. errors == 0 is what catches that.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=False)
    assert out["errors"] == 0, (
        "write_nfo rejected its argument -- it takes str, and the resolver "
        "returns Path: %r" % (out,))
    assert (dl / "flat.nfo").exists(), "no sidecar beside the flat file"
    assert (dl / "sub" / "nested.nfo").exists(), (
        "the nested sidecar was not written next to its video")


def test_the_returned_key_set_is_exactly_six(lib):
    """Additive, and pinned so a later drift is caught.

    The four original keys must survive -- test_v3_66_522_dead_endpoints.py
    asserts their MEMBERSHIP against the live endpoint, so dropping one is a
    500 in the panel rather than a test-only failure.
    """
    lf, dl, _away = lib
    out = lf.regen_nfos_from_history(download_dir=str(dl), dry_run=True)
    assert set(out) == _KEYS, "regen key set drifted: %r" % (sorted(out),)


def test_every_counter_survives_the_query_failure_path(lib):
    """The early return must carry all six keys, not the four it was born with.

    library_final.py's `except Exception: return out` hands back the literal
    unchanged. A counter initialised lazily on first increment would be ABSENT
    here, and an absent key reads as "no data" when the truth is "zero" --
    CLAUDE.md section 0, in a dict.
    """
    import bulk_downloader.db as db
    lf, _dl, _away = lib
    saved = db.DB_PATH
    db.DB_PATH = "/nonexistent-dir-for-regen916/q.db"
    try:
        out = lf.regen_nfos_from_history(download_dir="/tmp", dry_run=True)
    finally:
        db.DB_PATH = saved
    assert set(out) == _KEYS, (
        "the query-failure early return dropped counters: %r" % (sorted(out),))
    assert all(v == 0 for v in out.values()), out
