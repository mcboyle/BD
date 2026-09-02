"""Row 528: a zero-byte staging claim must not wedge a candidate name FOREVER.

THE DEFECT, MEASURED on origin/main c4ef790e (v3.66.1431) before this file's
fix. ``_create_owner``'s no-hardlink fallback publishes a claim with
``O_CREAT|O_EXCL`` and only then writes and fsyncs it, so a SIGKILL, a host
power loss, or a disconnect on exactly the CIFS/FAT filesystems that fallback
exists to serve leaves a zero-byte ``<final>.part.owner`` and no ``.part``.
The CONSUMER is filesystem-independent once such a file exists BY ANY MEANS --
an interrupted restore, an rsync, an operator, a rolling deploy:

  * ``_read_owner_record`` reads the empty claim as a publisher mid-flight,
    polls to ``_INFLIGHT_CLAIM_DEADLINE_S`` and raises ``StagingUnavailable``;
  * ``reserve`` catches only ``StagingClaimedByAnotherJob``, so that UNKNOWN
    propagates out of the candidate loop and NO ``_1`` name is ever tried.

Measured on this host against the defective module: three ``reserve`` calls --
twice for one identity, once for an unrelated one -- each refused after ~2.0s
having created exactly 0 suffixed owner files, with the directory still holding
exactly the one zero-byte claim. ``crash_recovery.scan_for_orphans`` globs
``*.part`` and there is no ``.part``, so it enumerated it 0 times, and
``delete_orphan`` early-returned ``{"ok": true, "note": "already absent"}``
before reaching the one API that deletes an owner. One transient failure
therefore refuses every job whose template renders that name, permanently, with
no self-heal and no recovery surface.

THE CONTRACT THIS PINS. A claim publishes only state it has already made true,
and a failed or partial claim leaves nothing that blocks the next attempt. An
empty claim that outlives the in-flight wait is not a claim at all -- it names
nobody -- and where the filesystem PROVES no live publisher can be standing in
that state, the next attempt adopts the pathname by publishing a complete
UNPROVEN record over it and then measures the bytes exactly as a fresh mint
would.

WHAT THE FIX IS NOT ALLOWED TO BE, and each of these has its own test below:

  * not a timeout. A rival that fills its claim inside the deadline is still
    waited for and honoured, and its name is not stolen;
  * not a divert. ``reserve`` still refuses rather than walk past an
    unmeasurable candidate -- pinned unchanged in
    ``tests/test_part_staging_collision.py::
    test_a_reservation_that_cannot_be_measured_never_silently_diverts``;
  * not an adoption of bytes. Bytes found under a recovered claim were
    accounted for by nobody and are SET ASIDE, never taken as a resume offset;
  * not a general softening of UNKNOWN. A non-empty unreadable or unparseable
    claim still refuses, and so does an empty one on a filesystem where a live
    publisher could be inside the create-then-write window;
  * not a recovery that can itself wedge the name. An interrupted recovery
    leaves the claim exactly as it found it and the next attempt proceeds.

THE DELIBERATE RESIDUAL. The recovery is gated on PROOF that this filesystem
publishes claims atomically, so on a no-hardlink filesystem -- the producer's
own home -- an empty claim still refuses. Enumerating and reaping a claim whose
``.part`` is absent is an operator surface in ``crash_recovery``, which this cut
does not build; ``test_the_recovery_needs_no_operator_surface`` pins that the
scan cannot see this state today.
"""
from __future__ import annotations

import errno
import json
import os
import threading
import time

import pytest

from bulk_downloader import crash_recovery as cr
from bulk_downloader import staging_claim as sc


BD_GATE_SCOPE = "module"

_JOB_A = "https://example.test/scene/a"
_JOB_B = "https://example.test/scene/b"
_FOREIGN_BYTE = 0xA5
_FOREIGN_SIZE = 4096
_RIVAL_DELAY_S = 0.5


def _empty_claim(tmp_path, name="Scene.mp4"):
    """Exactly the on-disk state a killed fallback publish leaves behind.

    Preconditions are asserted here, before any verdict: the defect is a
    permanent refusal reached over a file nobody measured, so a fixture that
    did not actually build a zero-byte claim would make every assertion below
    meaningless.
    """
    final = tmp_path / name
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    fd = os.open(str(owner), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.close(fd)
    assert owner.is_file(), f"the fixture did not create {owner}"
    assert owner.stat().st_size == 0, (
        f"the fixture claim holds {owner.stat().st_size} bytes; this test is "
        "about an EMPTY one")
    assert not staging.exists(), (
        "a .part must be absent, or crash_recovery could enumerate this state")
    assert not final.exists(), (
        "the final file must be absent, or reserve skips the candidate for an "
        "unrelated reason")
    assert sorted(p.name for p in tmp_path.iterdir()) == [owner.name], (
        f"the fixture left more than the claim: {list(tmp_path.iterdir())}")
    return final, staging, owner


def _owner_names(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir()
                  if p.name.endswith(sc.OWNER_SUFFIX))


def test_a_zero_byte_claim_does_not_wedge_its_own_name(tmp_path):
    """RED on the defective parent: ``StagingUnavailable`` "is still empty after"."""
    final, staging, owner = _empty_claim(tmp_path)
    identity = sc.job_identity(_JOB_A)

    started = time.monotonic()
    candidate, got = sc.reserve(final, identity)
    elapsed = time.monotonic() - started

    assert candidate == final, (
        f"the job lost its filename to {candidate.name}; recovering the claim "
        "must not cost the operator the name their template rendered")
    assert got == staging
    assert elapsed >= sc._INFLIGHT_CLAIM_DEADLINE_S, (
        f"the claim was taken after {elapsed:.3f}s, inside the "
        f"{sc._INFLIGHT_CLAIM_DEADLINE_S}s in-flight wait; a publisher still "
        "filling its claim would have been robbed")
    assert _owner_names(tmp_path) == [owner.name], (
        f"expected exactly the one claim, found {_owner_names(tmp_path)}")
    assert sc._read_owner_record(owner) == (identity, True), (
        "the recovered claim must name this job and record that the bytes "
        f"under it were accounted for, not {sc._read_owner_record(owner)}")
    assert not list(tmp_path.glob("*_1*")), (
        f"a suffixed candidate was claimed: {list(tmp_path.glob('*_1*'))}")


def test_the_name_is_recovered_once_and_a_rival_job_still_diverts(tmp_path):
    """The three RED attempts, in GREEN. Two for one identity, one for another.

    RED measured 3 refusals and 0 candidates claimed. GREEN must be: recover,
    reclaim idempotently and WITHOUT paying the in-flight wait again, and hand
    an unrelated job the ``_1`` name rather than the one now legitimately held.
    """
    final, staging, owner = _empty_claim(tmp_path)
    a = sc.job_identity(_JOB_A)
    b = sc.job_identity(_JOB_B)
    assert a != b, "precondition: the two jobs must have distinct identities"

    first_candidate, first_staging = sc.reserve(final, a)
    assert (first_candidate, first_staging) == (final, staging)

    started = time.monotonic()
    again_candidate, again_staging = sc.reserve(final, a)
    again_elapsed = time.monotonic() - started
    assert (again_candidate, again_staging) == (final, staging), (
        "the same job did not reclaim the claim it just recovered")
    assert again_elapsed < sc._INFLIGHT_CLAIM_DEADLINE_S, (
        f"the reclaim paid {again_elapsed:.3f}s; the recovery did not persist "
        "and the empty claim is being healed over and over")

    other_candidate, other_staging = sc.reserve(final, b)
    assert other_candidate == tmp_path / "Scene_1.mp4", (
        f"an unrelated job was handed {other_candidate.name}; a claim that is "
        "now legitimately held must still divert")
    assert other_staging == sc.staging_path_for(other_candidate)
    assert sc._read_owner_record(owner)[0] == a, (
        "the base claim no longer names the job that recovered it")
    assert _owner_names(tmp_path) == sorted(
        [owner.name, sc.owner_path_for(other_staging).name]), (
        f"expected exactly 2 claims, found {_owner_names(tmp_path)}")


def test_a_recovered_claim_sets_aside_bytes_it_did_not_write(tmp_path):
    """The rank-1 shape, applied to this fix's own recovery path.

    An empty claim accounted for nothing, so bytes beneath it have no
    established provenance. Adopting them would hand the transport another
    scene's resume offset -- the 2026-08-29 corruption reached through the fix
    written to prevent a different one.
    """
    final, staging, owner = _empty_claim(tmp_path)
    staging.write_bytes(bytes([_FOREIGN_BYTE]) * _FOREIGN_SIZE)
    assert staging.stat().st_size == _FOREIGN_SIZE, "precondition"

    candidate, got = sc.reserve(final, sc.job_identity(_JOB_A))

    assert candidate == final and got == staging
    assert not got.exists() or got.read_bytes() == b"", (
        f"the recovery adopted {got.stat().st_size} foreign bytes as this "
        "job's resume offset")
    aside = [p for p in tmp_path.iterdir()
             if p.name.endswith(".part") and p != staging]
    assert len(aside) == 1, (
        f"expected exactly 1 set-aside .part, found "
        f"{[p.name for p in tmp_path.iterdir()]}")
    assert aside[0].read_bytes() == bytes([_FOREIGN_BYTE]) * _FOREIGN_SIZE, (
        "the unowned bytes were altered or destroyed instead of preserved")
    assert sc._read_owner_record(owner) == (sc.job_identity(_JOB_A), True)


def test_a_rival_filling_its_claim_inside_the_wait_is_honoured(tmp_path):
    """NEGATIVE CONTROL: the fix is a proof, not a timeout.

    The in-flight wait exists so that losing a race does not present as an
    unreadable claim. A recovery that fired on elapsed time alone would steal
    the base name from a publisher that was about to finish.
    """
    final, staging, owner = _empty_claim(tmp_path)
    rival = sc.job_identity(_JOB_B)
    published = threading.Event()
    failure = []

    def _publish_late():
        try:
            time.sleep(_RIVAL_DELAY_S)
            payload = json.dumps(
                {"v": sc.OWNER_FORMAT_VERSION, "job": rival,
                 sc.OWNER_PROOF_KEY: True}, sort_keys=True).encode("utf-8")
            tmp = owner.parent / (owner.name + ".rival-tmp")
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            # Published atomically, exactly as the module publishes its own
            # records: a torn read would refuse for the wrong reason and the
            # control would prove nothing about the recovery.
            os.replace(str(tmp), str(owner))
            published.set()
        except BaseException as exc:                        # noqa: BLE001
            failure.append(exc)

    thread = threading.Thread(target=_publish_late)
    thread.start()
    try:
        started = time.monotonic()
        candidate, got = sc.reserve(final, sc.job_identity(_JOB_A))
        elapsed = time.monotonic() - started
    finally:
        thread.join(timeout=30)
    assert not failure, f"the rival fixture failed: {failure}"
    assert published.is_set(), "precondition: the rival never published"

    assert elapsed >= _RIVAL_DELAY_S, (
        f"reserve returned after {elapsed:.3f}s, before the rival published; "
        "it cannot have read the rival's record")
    assert elapsed < sc._INFLIGHT_CLAIM_DEADLINE_S, (
        f"reserve waited {elapsed:.3f}s, past the in-flight deadline; the "
        "rival's record was not honoured when it arrived")
    assert candidate == tmp_path / "Scene_1.mp4", (
        f"a live publisher's name was taken: reserve returned {candidate.name}")
    assert sc._read_owner_record(owner)[0] == rival, (
        "the base claim no longer records the rival that published it")


def test_a_well_formed_claim_held_by_another_job_still_diverts(tmp_path):
    """NEGATIVE CONTROL: a genuine collision is still refused the base name."""
    final = tmp_path / "Scene.mp4"
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    rival = sc.job_identity(_JOB_B)
    owner.write_text(json.dumps(
        {"v": sc.OWNER_FORMAT_VERSION, "job": rival, sc.OWNER_PROOF_KEY: True},
        sort_keys=True), encoding="utf-8")
    assert owner.stat().st_size > 0, "precondition: this claim is NOT empty"

    started = time.monotonic()
    candidate, got = sc.reserve(final, sc.job_identity(_JOB_A))
    elapsed = time.monotonic() - started

    assert candidate == tmp_path / "Scene_1.mp4", (
        f"a well-formed foreign claim did not divert: got {candidate.name}")
    assert got == sc.staging_path_for(candidate)
    assert elapsed < sc._INFLIGHT_CLAIM_DEADLINE_S, (
        f"a readable claim cost {elapsed:.3f}s; the in-flight wait was paid "
        "for a record that was never empty")
    assert sc._read_owner_record(owner) == (rival, True), (
        "the foreign claim was rewritten")


def test_an_empty_claim_is_not_adopted_where_publication_is_not_atomic(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL, and the one that makes this a proof.

    On a filesystem without hardlinks a live publisher CAN be standing inside
    the create-then-write window, so an empty claim there is UNKNOWN and UNKNOWN
    is not permission (A2). The refusal must survive unchanged, and the claim
    must be left byte-for-byte and inode-for-inode as it was found.
    """
    final, staging, owner = _empty_claim(tmp_path)
    before = owner.stat()

    def _no_hardlinks(_src, _dst, **_kw):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(sc.os, "link", _no_hardlinks)

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(final, sc.job_identity(_JOB_A))
    assert "is still empty after" in str(exc.value), (
        f"the refusal changed shape: {exc.value}")

    after = owner.stat()
    assert (after.st_dev, after.st_ino, after.st_size) == (
        before.st_dev, before.st_ino, 0), (
        "the claim was replaced or written on a filesystem where a live "
        "publisher could have been inside the window")
    assert sorted(p.name for p in tmp_path.iterdir()) == [owner.name], (
        f"the refused recovery left residue: {list(tmp_path.iterdir())}")


def test_a_malformed_non_empty_claim_is_never_adopted(tmp_path):
    """NEGATIVE CONTROL: only EMPTY is recoverable. Garbage stays UNKNOWN."""
    final = tmp_path / "Scene.mp4"
    owner = sc.owner_path_for(sc.staging_path_for(final))
    owner.write_bytes(b"{ this is not a claim record")

    started = time.monotonic()
    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(final, sc.job_identity(_JOB_A))
    elapsed = time.monotonic() - started

    assert "does not parse" in str(exc.value), (
        f"an unparseable claim refused for the wrong reason: {exc.value}")
    assert elapsed < sc._INFLIGHT_CLAIM_DEADLINE_S, (
        f"an unparseable claim paid the {elapsed:.3f}s in-flight wait; only an "
        "EMPTY claim is in-flight")
    assert owner.read_bytes() == b"{ this is not a claim record", (
        "the unparseable claim was overwritten")
    assert sorted(p.name for p in tmp_path.iterdir()) == [owner.name], (
        f"the refusal left residue: {list(tmp_path.iterdir())}")


def test_an_interrupted_recovery_leaves_the_next_attempt_able_to_proceed(
        tmp_path, monkeypatch):
    """A7 SELF-AUDIT: a recovery that fails partway is the defect one level down.

    The recovery publishes its replacement record through a private temporary
    name and one ``os.replace``. Fail that replace and the claim must be
    exactly what it was -- still empty, no residue, no half-published record --
    and the next attempt must recover it rather than inherit a second wedge.
    """
    final, staging, owner = _empty_claim(tmp_path)
    identity = sc.job_identity(_JOB_A)
    real_replace = os.replace
    fired = {"n": 0}

    def _fail_the_first_adopt(src, dst, **kw):
        if (str(dst) == str(owner)
                and str(src).endswith(sc._ADOPT_TMP_SUFFIX)):
            fired["n"] += 1
            if fired["n"] == 1:
                raise OSError(errno.EIO, "Input/output error")
        return real_replace(src, dst, **kw)

    monkeypatch.setattr(sc.os, "replace", _fail_the_first_adopt)

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(final, identity)
    assert fired["n"] == 1, (
        f"the recovery published {fired['n']} times, not once; the fixture did "
        "not interrupt the step it names")
    assert "Input/output error" in str(exc.value), (
        f"the interrupted recovery did not carry the failing step's own words: "
        f"{exc.value}")
    assert owner.stat().st_size == 0, (
        "the interrupted recovery left a partial record behind")
    assert sorted(p.name for p in tmp_path.iterdir()) == [owner.name], (
        f"the interrupted recovery left residue: {list(tmp_path.iterdir())}")

    candidate, got = sc.reserve(final, identity)
    assert fired["n"] == 2, (
        "the second attempt did not reach the recovery at all")
    assert candidate == final and got == staging
    assert sc._read_owner_record(owner) == (identity, True)


def test_the_recovery_needs_no_operator_surface(tmp_path, monkeypatch):
    """The orphan scan cannot see this state, so the heal is the only route.

    ``scan_for_orphans`` globs ``*.part`` and a wedged claim has none, which is
    what made the RED block unbounded. This pins that the fix does not quietly
    depend on an operator surface that still returns zero rows.
    """
    monkeypatch.setattr(cr, "_ignored_paths", lambda: set())
    final, staging, owner = _empty_claim(tmp_path)
    s_cfg = {"site": {"name": "Test Site", "download_dir": str(tmp_path)}}

    rows = cr.scan_for_orphans(s_cfg=s_cfg, runners={}, age_threshold_s=0)
    assert rows == [], (
        "precondition: the orphan scan must not enumerate a claim that has no "
        f".part, or this test is measuring something else -- got {rows}")

    candidate, got = sc.reserve(final, sc.job_identity(_JOB_A))
    assert candidate == final and got == staging
    assert sc._read_owner_record(owner)[1] is True


def test_racing_recoverers_hand_the_recovered_name_to_exactly_one(tmp_path):
    """A7 SELF-AUDIT: the recovery must not reintroduce the stale read.

    The recovery replaces the claim's inode, so any lock held across it is
    detached the instant it lands. Were the blank read and the republish split
    across a lock release, two workers could each read blank, each wait the
    deadline out, and each republish -- the second landing on top of the
    first's already PROVEN record and then setting its live bytes aside, which
    is the corruption the whole module exists to stop. N workers over one blank
    claim must produce N distinct names, one of them the base name, and no
    UNKNOWN anywhere.
    """
    final, staging, owner = _empty_claim(tmp_path)
    workers = 8
    start = threading.Barrier(workers)
    results = [None] * workers

    def _reserve(i):
        start.wait(timeout=60)
        try:
            candidate, _got = sc.reserve(
                final, sc.job_identity(f"https://example.test/scene/{i}"))
            results[i] = candidate.name
        except BaseException as exc:                        # noqa: BLE001
            results[i] = f"UNKNOWN: {type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=_reserve, args=(i,))
               for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a worker never finished"

    unknown = [r for r in results if r is None or r.startswith("UNKNOWN")]
    assert not unknown, f"the recovery refused {len(unknown)} workers: {unknown}"
    assert results.count(final.name) == 1, (
        f"{results.count(final.name)} workers were handed the base name: "
        f"{results}")
    assert len(set(results)) == workers, (
        f"two workers were handed the same staging path: {results}")

    for name in results:
        claim_file = sc.owner_path_for(sc.staging_path_for(tmp_path / name))
        assert claim_file.is_file() and claim_file.stat().st_size > 0, (
            f"{name} was returned without a complete claim behind it")
        assert sc._read_owner_record(claim_file)[1] is True, (
            f"{name} was returned under a claim that proves nothing")
    residue = [p.name for p in tmp_path.iterdir()
               if p.name.endswith((sc._ADOPT_TMP_SUFFIX, ".probe", ".tmp"))]
    assert not residue, f"the race left publish residue behind: {residue}"


def test_a_blank_claim_is_not_recovered_without_a_lock(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: exclusion is a PRECONDITION of the recovery.

    Without ``fcntl`` two workers can both read the same blank claim and both
    republish it, and the second would land on the first's already proven
    record and then set its live bytes aside. UNKNOWN is not permission (A2).
    """
    final, staging, owner = _empty_claim(tmp_path)
    before = owner.stat()
    monkeypatch.setattr(sc, "fcntl", None)

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(final, sc.job_identity(_JOB_A))

    assert "is still empty after" in str(exc.value)
    assert "no fcntl" in str(exc.value), (
        f"the refusal did not name the step that failed: {exc.value}")
    assert "does not publish claims atomically" not in str(exc.value), (
        "three conditions decline to recover a blank claim and they lead to "
        "three different operator actions; this one reported another's reason")
    after = owner.stat()
    assert (after.st_dev, after.st_ino, after.st_size) == (
        before.st_dev, before.st_ino, 0), (
        "the claim was recovered without exclusion")
    assert sorted(p.name for p in tmp_path.iterdir()) == [owner.name], (
        f"the refusal left residue: {list(tmp_path.iterdir())}")


def test_a_claim_this_call_minted_is_never_recovered_when_blank(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL: only ABANDONED residue is recoverable.

    ``_create_owner`` publishes a complete record, so a blank one under a claim
    THIS call just minted is a file something else truncated. That is
    corruption, not a killed publish, and it stays UNKNOWN.
    """
    final = tmp_path / "Scene.mp4"
    owner = sc.owner_path_for(sc.staging_path_for(final))
    real_create_owner = sc._create_owner
    fired = {"n": 0}

    def _truncate_what_it_minted(owner_path, identity):
        minted = real_create_owner(owner_path, identity)
        assert minted is True, "precondition: this call must be the minter"
        assert owner_path.stat().st_size > 0, (
            "precondition: the mint must have published a complete record")
        os.truncate(str(owner_path), 0)
        fired["n"] += 1
        return minted

    monkeypatch.setattr(sc, "_create_owner", _truncate_what_it_minted)

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(final, sc.job_identity(_JOB_A))

    assert fired["n"] == 1, (
        f"the fixture minted {fired['n']} claims; it must truncate exactly the "
        "one this call published")
    assert "is still empty after" in str(exc.value)
    assert "published that claim complete" in str(exc.value), (
        f"the refusal did not name why it declined to recover: {exc.value}")
    assert "no fcntl" not in str(exc.value), (
        "the refusal reported another condition's reason")
    assert owner.stat().st_size == 0, (
        "a truncated record under this call's own mint was rewritten")
