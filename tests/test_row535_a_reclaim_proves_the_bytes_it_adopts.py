"""Rows 535 and 575: the RECLAIM half of a staging claim's life.

CONTRACT: the staging claim publishes, mints, or reclaims an owner only after
the bytes that owner names are proven present and identified.

The mint half was closed at v3.66.1391 (row 533): a set-aside that RAISES now
unwinds the claim it could not make good on. The reclaim half was not, and the
refutation of 2026-09-01 named it twice:

  rank 3 / row 535  the crash-window variant. A SIGKILL or a deploy restart
                    between `_create_owner` and the `os.replace` inside
                    `_set_aside_unowned_bytes` raises NO exception, so the
                    row-533 unwind never runs. What is left on disk is a claim
                    recording THIS job standing over bytes that were never
                    measured. `claim()` is idempotent for one identity, so the
                    restart takes the reclaim branch -- `if
                    _read_owner_identity(owner) == identity: return staging` --
                    which measures nothing at all. The transport then reads
                    those foreign bytes as its own resume offset, appends a
                    different scene onto them, and promotes the splice as
                    `done` under the right title: the 2026-08-29 corruption,
                    reached with no error anywhere.

  rank 43 / row 575 the same seam stated generally: the reclaim branch measures
                    nothing.

THE FIX MEASURED HERE. The claim record carries a PROOF field. `_create_owner`
publishes it false; `claim()` sets it true only after the staging path it names
has been proven absent or empty. A reclaim of an UNPROVEN claim therefore knows
the bytes under it were never accounted for and measures them exactly as a
fresh mint would.

THIS FIX MUST NOT REPRODUCE THE DEFECT IT PREVENTS (CLAUDE.md A7). Three shapes
are pinned below because each of them would do exactly that:

  * a claim that is returned while its record still says unproven would let the
    transport write gigabytes under it, and the NEXT reclaim would then set
    aside the job's OWN bytes -- row 541's consequence, manufactured here;
  * a legacy v1 record (no proof field) read as UNPROVEN would set aside the
    own bytes of every download in flight across the fleet at the moment this
    version deploys -- the same consequence, at fleet scale;
  * unwinding a claim on a failed reclaim-heal would delete an ownership record
    this call did not create, minting the ownerless `.part` that refutation
    rank 1 feeds on.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import staging_claim as sc

BD_GATE_SCOPE = "module"

_JOB_A = "https://example.test/scene-a"
_JOB_B = "https://example.test/scene-b"

_FOREIGN = b"\xaa" * 4096
_OURS = b"\x5c" * (5 * 1024 * 1024)


def _ident(url: str) -> str:
    return sc.job_identity(url)


def _orphans(d):
    return sorted(p for p in d.iterdir() if ".orphaned-" in p.name)


def _crashed_mint(tmp_path, name="Interrupted.mp4", body=_FOREIGN):
    """The exact on-disk state a SIGKILL between `_create_owner` and the
    `os.replace` leaves behind, built by replaying those two steps and stopping
    between them.

    Constructing it directly is the only faithful simulation: after the row-533
    unwind, no in-process exception can leave this state -- which is precisely
    why the unwind does not close it. The crash is not an exception.
    """
    final = tmp_path / name
    staging = sc.staging_path_for(final)
    staging.write_bytes(body)
    owner = sc.owner_path_for(staging)
    minted = sc._create_owner(owner, _ident(_JOB_A))

    # PRECONDITIONS, asserted before any verdict is reached over them.
    assert minted is True, "precondition: this call is the one that minted"
    assert owner.is_file(), "precondition: the crash left a claim on disk"
    assert sc._read_owner_identity(owner) == _ident(_JOB_A), (
        "precondition: the claim records OUR identity, so the restart takes "
        "the reclaim branch rather than refusing")
    assert staging.stat().st_size == len(body), (
        "precondition: the staging path holds the unmeasured bytes")
    assert staging.read_bytes() == body
    assert _orphans(tmp_path) == [], "precondition: nothing was set aside yet"
    return final, staging, owner


# ── Row 535: the crash window ──────────────────────────────────────────────

def test_a_reclaim_after_a_crashed_mint_does_not_adopt_the_bytes(tmp_path):
    """RED against v3.66.1391: the reclaim branch handed the staging path back
    with 4096 unmeasured bytes still in it."""
    final, staging, owner = _crashed_mint(tmp_path)

    got = sc.claim(final, _ident(_JOB_A))

    assert got == staging
    remaining = staging.stat().st_size if staging.exists() else 0
    assert remaining == 0, (
        f"the reclaim adopted {remaining} bytes no claim ever measured; the "
        "transport now takes its resume offset from another scene's file and "
        "appends onto it")

    aside = _orphans(tmp_path)
    assert len(aside) == 1, (
        f"expected exactly one set-aside file, got {[p.name for p in aside]}")
    assert aside[0].read_bytes() == _FOREIGN, (
        "the unmeasured bytes were destroyed rather than set aside; nothing "
        "is deleted on this path")
    assert aside[0].name.endswith(".part"), (
        "the set-aside bytes must keep a .part suffix or "
        "crash_recovery.scan_for_orphans cannot see them")


def test_the_healed_claim_is_proven_so_the_next_reclaim_measures_nothing(tmp_path):
    """The heal happens exactly ONCE. A guard that re-measured on every reclaim
    would set aside the job's own bytes on its first genuine resume."""
    final, staging, owner = _crashed_mint(tmp_path)

    fired = {"n": 0}
    real = sc._set_aside_unowned_bytes

    def counted(path):
        fired["n"] += 1
        return real(path)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_set_aside_unowned_bytes", counted)
        sc.claim(final, _ident(_JOB_A))
        assert fired["n"] == 1, (
            "the heal did not run over the crashed mint's unmeasured bytes")

        # This job now streams its own bytes into the path it owns.
        staging.write_bytes(_OURS)
        assert staging.stat().st_size == len(_OURS), "precondition: our bytes"

        sc.claim(final, _ident(_JOB_A))
        assert fired["n"] == 1, (
            "the reclaim re-measured a claim that had already been proven, so "
            "a genuine resume set aside its own 5 MiB and restarted at byte 0")

    assert staging.read_bytes() == _OURS, "the resume lost its own bytes"
    assert len(_orphans(tmp_path)) == 1, (
        "a second set-aside file appeared, so the job's own bytes were "
        "orphaned by the very guard written to prevent that")


def test_a_fresh_mint_records_its_proof(tmp_path):
    """The seam itself: an ordinary mint leaves a PROVEN claim on disk, so the
    resume path above is reached by the normal route and not only by the heal."""
    final = tmp_path / "Clean.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    record = json.loads(sc.owner_path_for(staging).read_text(encoding="utf-8"))
    assert record["job"] == _ident(_JOB_A)
    assert record["v"] == sc.OWNER_FORMAT_VERSION
    assert record[sc.OWNER_PROOF_KEY] is True, (
        "an ordinary mint left an unproven claim, so the next reclaim heals "
        "over bytes this job is entitled to resume")


# ── Row 575: the reclaim branch measures nothing ───────────────────────────

def test_a_proven_claim_still_resumes_its_own_bytes(tmp_path):
    """NEGATIVE CONTROL for row 575's fix. The guard must distinguish, not
    refuse: an interrupted download reclaiming its own proven claim resumes
    from its own `.part`. That behaviour is the one thing this module says must
    not be traded away, so widening the new measurement into "always
    re-measure" fails here."""
    final = tmp_path / "Resumable.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(_OURS)
    assert staging.stat().st_size == len(_OURS), "precondition: 5 MiB staged"
    assert sc.owner_path_for(staging).is_file(), "precondition: claim held"

    got = sc.claim(final, _ident(_JOB_A))

    assert got == staging
    assert staging.stat().st_size == len(_OURS), (
        "the reclaim restarted this job's own download at byte 0")
    assert staging.read_bytes() == _OURS
    assert _orphans(tmp_path) == [], (
        "the job's own bytes were renamed to .orphaned-*; nothing reaps those, "
        "and they are exactly the ownerless-.part population rank 1 needs")


def test_a_foreign_claim_is_still_refused(tmp_path):
    """NEGATIVE CONTROL: the new proof field must not become a second way in.
    A claim naming a DIFFERENT job still refuses, proven or not."""
    final = tmp_path / "Contested.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(_OURS)

    with pytest.raises(sc.StagingClaimedByAnotherJob):
        sc.claim(final, _ident(_JOB_B))
    assert staging.read_bytes() == _OURS, (
        "job B touched the bytes of a claim it was refused")
    assert _orphans(tmp_path) == [], "job B set aside job A's bytes"


def test_an_unproven_foreign_claim_is_refused_before_it_is_healed(tmp_path):
    """The refusal must come FIRST. Healing an unproven claim that belongs to
    somebody else would set aside a live download's bytes mid-transfer."""
    final = tmp_path / "Contested.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_OURS)
    owner = sc.owner_path_for(staging)
    assert sc._create_owner(owner, _ident(_JOB_A)) is True, "precondition"

    with pytest.raises(sc.StagingClaimedByAnotherJob):
        sc.claim(final, _ident(_JOB_B))
    assert staging.read_bytes() == _OURS
    assert _orphans(tmp_path) == []


# ── The fix must not reproduce the defect ──────────────────────────────────

def test_a_legacy_v1_claim_is_grandfathered_as_proven(tmp_path):
    """A v1 record has no proof field because v1 minted and set aside in one
    synchronous call: reaching the reclaim branch at all meant the mint had
    completed. Reading key-absent as UNPROVEN would, on the first retry after
    this version deploys, set aside the own bytes of every download in flight
    across the fleet -- this fix manufacturing row 541 at scale."""
    final = tmp_path / "InFlightAcrossTheDeploy.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_OURS)
    owner = sc.owner_path_for(staging)
    owner.write_text(json.dumps({"v": 1, "job": _ident(_JOB_A)}, sort_keys=True),
                     encoding="utf-8")
    assert sc.OWNER_PROOF_KEY not in json.loads(owner.read_text()), (
        "precondition: this is a v1 record, written before the proof field")
    assert staging.stat().st_size == len(_OURS), "precondition: 5 MiB in flight"

    got = sc.claim(final, _ident(_JOB_A))

    assert got == staging
    assert staging.read_bytes() == _OURS, (
        "the upgrade destroyed the resume state of a download that was in "
        "flight when it deployed")
    assert _orphans(tmp_path) == []


def test_a_current_claim_missing_its_proof_field_is_unknown(tmp_path):
    """The inverse of the grandfather clause, so it cannot be a fail-open: a
    record that DECLARES this format and omits the field is malformed, and an
    unmeasurable state is UNKNOWN rather than permission (A2)."""
    final = tmp_path / "Malformed.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_OURS)
    owner = sc.owner_path_for(staging)
    owner.write_text(
        json.dumps({"v": sc.OWNER_FORMAT_VERSION, "job": _ident(_JOB_A)},
                   sort_keys=True), encoding="utf-8")

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.claim(final, _ident(_JOB_A))
    assert "proof" in str(exc.value).lower(), (
        f"the refusal must name the step that failed, not collapse into a "
        f"generic unreadable-claim message: {exc.value}")
    assert staging.read_bytes() == _OURS, "an UNKNOWN state touched the bytes"


def test_the_proof_is_made_durable_after_it_is_published(tmp_path):
    """A proof that a power loss can un-write is a proof that reintroduces the
    defect: the machine comes back with the claim reading unproven over bytes
    the transport had already streamed, and the reclaim orphans them. Assert
    the directory sync FIRES, exactly once per claim, and only after the record
    is in place."""
    final = tmp_path / "Durable.mp4"
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    seen = []

    real = sc._fsync_dir

    def watched(directory):
        seen.append((str(directory),
                     json.loads(owner.read_text(encoding="utf-8"))))
        return real(directory)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_fsync_dir", watched)
        sc.claim(final, _ident(_JOB_A))

    assert len(seen) == 1, f"expected exactly one directory sync, got {len(seen)}"
    where, record_at_sync = seen[0]
    assert where == str(tmp_path), (
        f"the sync named {where}, not the directory holding the claim")
    assert record_at_sync[sc.OWNER_PROOF_KEY] is True, (
        "the sync ran before the proof was in place, so it made the wrong "
        "version of the record durable")


def test_a_claim_survives_a_filesystem_that_cannot_sync_a_directory(tmp_path):
    """NEGATIVE CONTROL: the durability step is best-effort by design --
    Windows and some network filesystems refuse to open a directory for fsync
    -- so it must not become a new way for a download to be refused."""
    final = tmp_path / "NoDirSync.mp4"

    def refuse(_directory):
        raise OSError(1, "synthetic: this filesystem cannot sync a directory")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_fsync_dir", refuse)
        with pytest.raises(sc.StagingUnavailable):
            sc.claim(final, _ident(_JOB_A))

    # And the real helper swallows exactly that failure, so the raise above can
    # only be reached by a caller that replaced it.
    sc._fsync_dir(tmp_path / "no-such-directory")
    staging = sc.claim(final, _ident(_JOB_A))
    assert sc.owner_path_for(staging).is_file()


def test_a_proof_field_that_is_not_a_boolean_is_unknown(tmp_path):
    """A mutation battery found this one: reading the field without checking
    its type made `"proven": "no"` truthy, so the string that most plainly says
    UNPROVEN was read as proof. Whether the bytes were accounted for is not
    something a malformed record can answer either way."""
    final = tmp_path / "Truthy.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_OURS)
    owner = sc.owner_path_for(staging)
    owner.write_text(
        json.dumps({"v": sc.OWNER_FORMAT_VERSION, "job": _ident(_JOB_A),
                    sc.OWNER_PROOF_KEY: "no"}, sort_keys=True),
        encoding="utf-8")
    assert json.loads(owner.read_text())[sc.OWNER_PROOF_KEY] == "no", (
        "precondition: the field is present, non-boolean, and truthy")

    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.claim(final, _ident(_JOB_A))
    assert "proof" in str(exc.value).lower(), (
        f"the refusal must name the field that could not be read: {exc.value}")
    assert staging.read_bytes() == _OURS, "an UNKNOWN state touched the bytes"
    assert _orphans(tmp_path) == []


def test_a_mint_that_cannot_prove_itself_leaves_no_claim(tmp_path):
    """Returning a staging path under an unproven claim is the whole defect
    moved one step later: the transport writes gigabytes into it and the next
    reclaim heals by orphaning them."""
    final = tmp_path / "Unprovable.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_FOREIGN)
    owner = sc.owner_path_for(staging)

    def boom(_owner_path, _identity):
        raise sc.StagingUnavailable("synthetic: the proof could not be written")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_prove_owner", boom)
        with pytest.raises(sc.StagingUnavailable):
            sc.claim(final, _ident(_JOB_A))

    assert not owner.exists(), (
        "a claim this call published and could not prove outlived the call")
    aside = _orphans(tmp_path)
    assert len(aside) == 1 and aside[0].read_bytes() == _FOREIGN, (
        "the unwind destroyed the bytes it had already set aside")


def test_a_reclaim_that_cannot_prove_itself_keeps_the_claim(tmp_path):
    """UNWIND ASYMMETRY. The mint unwinds because it created the claim; the
    reclaim must NOT, because the claim pre-existed the call and may be
    guarding crash residue. Deleting it here mints the ownerless `.part` that
    refutation rank 1 turns into a spliced file promoted as done."""
    final, staging, owner = _crashed_mint(tmp_path)

    def boom(_owner_path, _identity):
        raise sc.StagingUnavailable("synthetic: the proof could not be written")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_prove_owner", boom)
        with pytest.raises(sc.StagingUnavailable):
            sc.claim(final, _ident(_JOB_A))

    assert owner.is_file(), (
        "the failed heal deleted a claim it did not create, so the bytes "
        "beneath it are now ownerless and the next job adopts them")
    assert sc._read_owner_identity(owner) == _ident(_JOB_A)
