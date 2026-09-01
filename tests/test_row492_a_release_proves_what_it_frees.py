"""Rows 492, 523, 489, 506 and 507: the release half of a staging claim's life.

staging_claim is a module built on identity -- claim() refuses a foreign claim,
reserve() diverts around one -- and its single state-MUTATING exit had none.
`release(staging_path)` took no identity, read no claim, and unlinked the owner
file unconditionally. Its docstring called a leaked claim inert, which is true
only for the caller that owns it.

Five measured consequences, one contract:

  492  release() frees a claim it cannot prove it owns.
  523  _do_download reserves once and releases at only some of its exits, so a
       claim outlives the job that took it with nothing able to see it.
  489  the browser fallback releases a claim its .part OUTLIVES -- the bytes
       stay, unowned, and the next job rendering that name adopts them.
  506  a leaked .owner is invisible to every sweep in the product:
       cleanup_helpers._PARTIAL_EXTS is a five-extension denominator that does
       not include it, so GET /api/cleanup/summary can never report one.
  507  force_cleanup counts a cleanup it did not perform.

Each test asserts its preconditions before its verdict, because every one of
these is a verdict reached over a state nobody measured.
"""
from __future__ import annotations

import pathlib

import pytest

from bulk_downloader import staging_claim as sc

BD_GATE_SCOPE = "module"

_JOB_A = "https://example.test/scene-a"
_JOB_B = "https://example.test/scene-b"


def _ident(url: str) -> str:
    return sc.job_identity(url)


# ── 492: a release must prove what it frees ────────────────────────────────

def test_release_refuses_a_claim_another_job_owns(tmp_path):
    final = tmp_path / "Contested.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert owner.is_file(), "precondition: job A holds the claim"

    freed = sc.release(staging, _ident(_JOB_B))

    assert freed is False, "job B was told it freed a claim it does not own"
    assert owner.is_file(), (
        "job B unlinked job A's claim; the next reserve() would hand A's "
        "staging path to a third job while A is still writing into it")
    assert sc._read_owner_identity(owner) == _ident(_JOB_A)


def test_release_frees_its_own_claim(tmp_path):
    """POSITIVE CONTROL: the ordinary path must still work, or every completed
    download leaks a claim and the fix is worse than the defect."""
    final = tmp_path / "Mine.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert owner.is_file()

    assert sc.release(staging, _ident(_JOB_A)) is True
    assert not owner.exists()


def test_release_is_idempotent_when_there_is_no_claim(tmp_path):
    final = tmp_path / "Absent.mp4"
    staging = sc.staging_path_for(final)
    assert not sc.owner_path_for(staging).exists()
    assert sc.release(staging, _ident(_JOB_A)) is True


def test_a_forced_release_is_explicit_and_still_reports_what_it_did(tmp_path):
    """The operator-driven sweep genuinely may free a foreign claim -- that is
    what delete_orphan is for -- but it must SAY so rather than inherit the
    old unconditional behaviour by omission."""
    final = tmp_path / "Operator.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert sc.release(staging, _ident(_JOB_B), force=True) is True
    assert not owner.exists()


def test_release_without_an_identity_is_refused(tmp_path):
    """A caller that cannot name itself cannot prove ownership. UNKNOWN is a
    failing state, never permission (A2)."""
    final = tmp_path / "Anonymous.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    with pytest.raises((TypeError, ValueError)):
        sc.release(staging)
    assert owner.is_file(), "the anonymous call freed the claim anyway"


# ── 489: a claim may not be released while its .part still exists ──────────

def test_release_refuses_while_its_own_part_still_holds_bytes(tmp_path):
    """release's own docstring states the precondition -- drop a claim once its
    .part is GONE. It never checked. The browser fallback releases after
    _http_download raised, a path that removes neither the bytes nor the claim,
    so the .part outlives its claim and the next job adopts it."""
    final = tmp_path / "Outlives.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(b"\xaa" * 1024)
    owner = sc.owner_path_for(staging)

    freed = sc.release(staging, _ident(_JOB_A))

    assert freed is False, (
        "the claim was dropped while its .part still holds 1024 bytes; those "
        "bytes are now unowned and the next job rendering this name adopts them")
    assert owner.is_file()
    assert staging.stat().st_size == 1024, "the bytes were altered"


def test_release_succeeds_once_the_part_is_gone(tmp_path):
    """POSITIVE CONTROL for the rule above."""
    final = tmp_path / "Promoted.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(b"\xbb" * 16)
    staging.unlink()
    assert sc.release(staging, _ident(_JOB_A)) is True
    assert not sc.owner_path_for(staging).exists()


# ── 506: a leaked claim must be visible to the sweeps ─────────────────────

def test_a_leaked_claim_is_visible_to_the_partial_sweep():
    from bulk_downloader import cleanup_helpers

    exts = cleanup_helpers._PARTIAL_EXTS
    assert exts, "the partial-file denominator is empty"
    assert sc.OWNER_SUFFIX in exts, (
        f"the sweep denominator is {sorted(exts)}, which cannot see a leaked "
        f"{sc.OWNER_SUFFIX}; GET /api/cleanup/summary can therefore never "
        "report one, and nothing else in the product looks for it either")


def test_the_partial_sweep_still_sees_ordinary_partials():
    """NEGATIVE CONTROL: widening the denominator must not narrow it."""
    from bulk_downloader import cleanup_helpers

    for ext in (".part", ".ytdl", ".download", ".crdownload", ".tmp"):
        assert ext in cleanup_helpers._PARTIAL_EXTS, ext


# ── Row 533: a mint that cannot make good on itself must not survive ───────
#
# claim() publishes the .owner FIRST and moves the foreign bytes SECOND, so any
# failure between the two left a claim standing over bytes it had not cleared.
# claim() is idempotent for one identity, so the very next attempt by the SAME
# job took the reclaim branch -- which never re-measures -- resumed over another
# scene's bytes, and promoted the splice as done under the right title. That is
# the 2026-08-29 corruption, manufactured by the fix written to prevent it.
#
# The reachable trigger needs no error at all: a SIGKILL or a deploy restart in
# the window between _create_owner and os.replace leaves exactly this state.

def test_a_failed_set_aside_leaves_no_claim_behind(tmp_path, monkeypatch):
    """RED against the fix as first shipped."""
    final = tmp_path / "Interrupted.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(b"\xaa" * 4096)
    owner = sc.owner_path_for(staging)
    assert not owner.exists(), "precondition: the bytes are ownerless"

    def boom(_path):
        raise sc.StagingUnavailable("synthetic: the rename could not be made")

    monkeypatch.setattr(sc, "_set_aside_unowned_bytes", boom)
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, _ident(_JOB_A))

    assert not owner.exists(), (
        "the claim survived a set-aside it could not perform, so the next "
        "attempt by this same job takes the reclaim branch -- which never "
        "re-measures -- and resumes over 4096 foreign bytes")
    assert staging.read_bytes() == b"\xaa" * 4096, (
        "the foreign bytes were altered by a call that failed")


def test_the_retry_after_a_failed_set_aside_still_refuses_to_adopt(tmp_path, monkeypatch):
    """The consequence, end to end: because the mint unwound, the RETRY is a
    fresh mint and measures the bytes again instead of inheriting a claim."""
    final = tmp_path / "Interrupted.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(b"\xaa" * 4096)

    calls = {"n": 0}
    real = sc._set_aside_unowned_bytes

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sc.StagingUnavailable("synthetic: transient rename failure")
        return real(path)

    monkeypatch.setattr(sc, "_set_aside_unowned_bytes", flaky)
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, _ident(_JOB_A))

    got = sc.claim(final, _ident(_JOB_A))
    assert calls["n"] == 2, (
        "the retry took the reclaim branch instead of minting again, so the "
        "foreign bytes were never measured a second time")
    assert not got.exists() or got.read_bytes() == b"", (
        "the retry adopted the foreign bytes")


def test_an_ordinary_mint_is_unaffected(tmp_path):
    """POSITIVE CONTROL: the unwind must not fire on the happy path."""
    final = tmp_path / "Clean.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    assert sc.owner_path_for(staging).is_file(), (
        "the unwind removed a claim that was minted successfully")
