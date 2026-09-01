"""Row 541 (refutation rank 9): a job's own partial bytes must never be set
aside because the claim over them was dropped while they were still there.

THE STATED MECHANISM IS CLOSED, AND THIS FILE PINS IT SHUT. The refuter's route
in was `release()` -- called at the browser-fallback exits of
`runner_transport._do_download` after `_http_download` had already staged
gigabytes -- dropping the claim while the `.part` survived. The next attempt by
the same job then found bytes with no owner, could not tell its own work from a
stranger's, renamed 5 GB to `.orphaned-*` and restarted at byte 0. Nothing
reaps those orphans, so they accumulate, and they are exactly the ownerless-
`.part` population that refutation rank 1 splices into a file promoted as done.

`release()` grew the part-is-gone proof at v3.66.1391 (rows 489/492, commit
651f2999), which is AFTER the 5291de20 base the refutation measured. Replaying
5291de20's `staging_claim.py` under these tests reproduces the orphaning; the
current module does not. The row is therefore pinned rather than fixed, and the
size below is a real multi-gigabyte `st_size` rather than a stand-in, because
`release()` and `_set_aside_unowned_bytes` both branch on that exact number.

STILL OPEN AND DELIBERATELY NOT ADDRESSED HERE (reported, not folded into this
cut, A3): every caller in `runner_transport` discards the False that `release()`
returns, so a retained claim is silent; and `_http_download` unlinks
`owner_path` directly at two places rather than going through `release()`, which
on the opt-in ramdisk arm can drop the claim while an on-disk `.part` survives
-- the same shape, reached around this module rather than through it.
"""
from __future__ import annotations

import pytest

from bulk_downloader import staging_claim as sc

BD_GATE_SCOPE = "module"

_JOB_A = "https://example.test/scene-a"

# A real 5 GB st_size, made sparse so the assertion costs no disk. Every branch
# under test reads st_size; none reads the bytes.
_FIVE_GB = 5 * 1024 * 1024 * 1024


def _ident(url: str) -> str:
    return sc.job_identity(url)


def _stage_five_gigabytes(staging):
    with open(staging, "wb") as fh:
        fh.truncate(_FIVE_GB)
        fh.seek(_FIVE_GB - 8)
        fh.write(b"TAILMARK")
    return staging.stat().st_size


def test_release_retains_the_claim_over_a_surviving_multi_gigabyte_part(tmp_path):
    final = tmp_path / "FiveGigScene.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    size = _stage_five_gigabytes(staging)

    # PRECONDITIONS before the verdict.
    assert size == _FIVE_GB, (
        f"precondition: the .part must really be {_FIVE_GB} bytes, got {size}")
    assert owner.is_file(), "precondition: this job holds the claim"
    assert sc._read_owner_identity(owner) == _ident(_JOB_A)

    freed = sc.release(staging, _ident(_JOB_A))

    assert freed is False, (
        "release() reported it had dropped a claim whose .part is still on "
        "disk, which is its own documented precondition")
    assert owner.is_file(), (
        "the claim was dropped over 5 GB of surviving bytes; they are now "
        "ownerless and the next attempt cannot tell them from a stranger's")
    assert staging.stat().st_size == _FIVE_GB, "release() touched the bytes"


def test_the_retry_resumes_the_five_gigabytes_instead_of_orphaning_them(tmp_path):
    """The consequence end to end: because the claim survived, the retry takes
    the reclaim branch and the bytes are still there to resume from."""
    final = tmp_path / "FiveGigScene.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    assert _stage_five_gigabytes(staging) == _FIVE_GB, "precondition"
    assert sc.release(staging, _ident(_JOB_A)) is False, "precondition"

    got = sc.claim(final, _ident(_JOB_A))

    assert got == staging
    assert staging.stat().st_size == _FIVE_GB, (
        "the retry restarted a 5 GB download at byte 0")
    with open(staging, "rb") as fh:
        fh.seek(_FIVE_GB - 8)
        tail = fh.read(8)
    assert tail == b"TAILMARK", (
        "the retry is resuming from a different file's bytes")
    orphans = [p.name for p in tmp_path.iterdir() if ".orphaned-" in p.name]
    assert orphans == [], (
        f"the job's own bytes were set aside as {orphans}; nothing ever reaps "
        "those, and they manufacture the ownerless-.part population that the "
        "rank-1 corruption feeds on")


def test_release_still_frees_a_claim_whose_part_is_gone(tmp_path):
    """NEGATIVE CONTROL: the part-is-gone proof must not be a refusal to ever
    release. A completed download promotes its `.part` and its claim must go
    with it, or every finished file leaves a claim that pushes the next
    download of that name onto `_1` forever."""
    final = tmp_path / "Completed.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    assert _stage_five_gigabytes(staging) == _FIVE_GB, "precondition: staged"
    staging.replace(final)               # the promotion release() waits for
    assert not staging.exists(), "precondition: the .part is gone"
    assert final.stat().st_size == _FIVE_GB, "precondition: it was promoted"

    freed = sc.release(staging, _ident(_JOB_A))

    assert freed is True
    assert not sc.owner_path_for(staging).exists(), (
        "the claim outlived the .part it guards")


def test_release_still_frees_a_claim_over_an_empty_part(tmp_path):
    """NEGATIVE CONTROL, the other edge: zero bytes are no resume offset and no
    hazard -- `_set_aside_unowned_bytes` leaves them alone for that reason --
    so an empty `.part` must not wedge the claim open either."""
    final = tmp_path / "NothingStaged.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(b"")
    assert staging.stat().st_size == 0, "precondition: empty .part"

    assert sc.release(staging, _ident(_JOB_A)) is True
    assert not sc.owner_path_for(staging).exists()


def test_release_without_an_identity_is_refused(tmp_path):
    """NEGATIVE CONTROL: a caller that cannot name itself cannot prove
    ownership, so the guard cannot be walked around by omission."""
    final = tmp_path / "Anonymous.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    with pytest.raises(ValueError):
        sc.release(staging)
    assert sc.owner_path_for(staging).is_file()
