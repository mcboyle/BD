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
import os
import pathlib
import subprocess
import sys
import textwrap
import threading
import time

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


# ── The FIX'S OWN RACE: a settle acts on a record it read earlier ──────────
#
# F3 of the 2026-09-01 night refutation, against v3.66.1395 -- the cut that
# added everything above. Before it, the same-identity reclaim branch was
# `if _read_owner_identity(owner) == identity: return staging`: READ-ONLY. It
# is now `_set_aside_unowned_bytes(staging); _prove_owner(owner, identity)`,
# which RENAMES whatever is at the staging path. The decision to rename was
# taken from a record read EARLIER IN THE SAME CALL, and nothing re-validated
# it before the rename.
#
# `job_identity()` is `sha256(page_url)` and therefore stable across processes,
# so two workers on one page_url share an identity -- which is exactly the
# collision the branch now acts on destructively. The module docstring names
# that threat model itself: "different threads, different SiteRunner instances,
# or different processes (a deploy restart mid-download)".
#
# The window has TWO sides and only one of them was reported:
#
#   HEAL SIDE (F3 as filed). A publishes an unproven claim; B reads it; A
#   completes its mint and streams; B wakes with its stale unproven read and
#   renames A's live `.part` to `.orphaned-*`. A keeps writing to an inode no
#   longer at that path and promotes something that is not what is there.
#
#   MINT SIDE (the mirror, found while fixing the first). A publishes an
#   unproven claim and is descheduled; B heals it, proves it, returns, and
#   streams; A wakes and runs its own unconditional `_set_aside_unowned_bytes`
#   over B's live bytes. Same corruption, other branch. A re-read placed only
#   in the reclaim branch would leave this half open.
#
# THE FIX. Both branches converge on one SETTLE that runs under an exclusive
# `flock` on the claim: acquire, re-read the record UNDER the lock, and act on
# THAT read. Every actor that can turn the record proven must hold the same
# lock across that transition, so the re-read is atomic with the rename it
# guards -- a re-read that is not would be the same defect one step smaller.
# `flock` rather than a token file because the question the branch is actually
# asking is "is a live writer inside this window, or did a crash abandon it",
# and a kernel lock released on process death is the primitive that ANSWERS
# that question; an exclusive-create token would answer it "live" forever and
# make crash residue unhealable.

_LIVE = b"\x71" * 5_000_000
_GATE_S = 2.0          # bounded: the fixed code must not need the interleaving


def _live_bytes_at(staging):
    return staging.stat().st_size if staging.exists() else 0


def _two_claims_one_identity(tmp_path, final, *, residue, gate_the_healer):
    """Drive a MINT and a RECLAIM of one staging path concurrently.

    `gate_the_healer` picks which side is made to wait, i.e. which of the two
    stale-read windows above is exercised. Both sides run through the real
    `claim()`; the only monkeypatching is synchronization that DELEGATES to the
    real helpers, and every wait is bounded so the fixed code -- in which this
    interleaving is impossible -- cannot hang instead of passing.
    """
    staging = sc.staging_path_for(final)
    if residue is not None:
        staging.write_bytes(residue)
    identity = _ident(_JOB_A)

    minted = threading.Event()          # A's UNPROVEN claim is visible
    healer_acted = threading.Event()    # B reached the branch that renames
    minter_streamed = threading.Event() # A returned and streamed its bytes
    healer_streamed = threading.Event() # B returned and streamed its bytes

    real_create = sc._create_owner
    real_set_aside = sc._set_aside_unowned_bytes
    counter_lock = threading.Lock()
    fired = {"set_aside": 0}
    who = {}
    out = {}
    handles = []

    def gated_create(owner_path, job):
        made = real_create(owner_path, job)
        if made:
            # The minter, in the window F3 names: its claim is published and
            # UNPROVEN and it holds nothing yet.
            who["minter"] = threading.get_ident()
            minted.set()
            # Hold the minter in that window until the other side has taken
            # its stale read (heal side) or finished settling (mint side).
            if gate_the_healer:
                healer_acted.wait(_GATE_S)
            else:
                healer_streamed.wait(_GATE_S)
        return made

    def gated_set_aside(path):
        with counter_lock:
            fired["set_aside"] += 1
        if gate_the_healer and threading.get_ident() != who.get("minter"):
            healer_acted.set()
            minter_streamed.wait(_GATE_S)
        return real_set_aside(path)

    def run(tag, after):
        # The transport opens the staging path ONCE and streams through that
        # descriptor, which is why a rename underneath it is silent: the writer
        # keeps a live inode that is no longer at the path it will promote.
        try:
            got = sc.claim(final, identity)
            out[tag] = got
            handle = open(got, "wb")
            handles.append(handle)
            handle.write(_LIVE)
            handle.flush()
            out[tag + "_ino"] = os.fstat(handle.fileno()).st_ino
        except BaseException as exc:     # surfaced by the caller, never eaten
            out[tag + "_exc"] = exc
        finally:
            after.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "_create_owner", gated_create)
        mp.setattr(sc, "_set_aside_unowned_bytes", gated_set_aside)
        a = threading.Thread(target=run, args=("minter", minter_streamed))
        a.start()
        assert minted.wait(_GATE_S * 4), (
            "precondition: the minter never published its unproven claim, so "
            "neither stale-read window was ever opened")
        b = threading.Thread(target=run, args=("healer", healer_streamed))
        b.start()
        a.join(_GATE_S * 8)
        b.join(_GATE_S * 8)

    for handle in handles:
        handle.close()
    assert not a.is_alive() and not b.is_alive(), (
        "a claim() call never returned; the settle deadlocked rather than "
        "serialized")
    for tag in ("minter", "healer"):
        if tag + "_exc" in out:
            raise AssertionError(f"{tag} claim() raised: {out[tag + '_exc']!r}")
    return staging, out, fired, healer_acted


def test_a_heal_does_not_orphan_a_mint_that_settled_while_it_waited(tmp_path):
    """HEAL SIDE. RED against v3.66.1395: B renamed A's live 5,000,000-byte
    `.part` to `.orphaned-*` on the strength of an unproven read taken before A
    finished minting."""
    final = tmp_path / "Contended.mp4"
    staging, out, fired, healer_acted = _two_claims_one_identity(
        tmp_path, final, residue=None, gate_the_healer=True)

    assert healer_acted.is_set(), (
        "precondition: the reclaim never reached the branch that renames, so "
        "this run did not exercise the destructive path at all")
    assert out["minter"] == staging and out["healer"] == staging, (
        "precondition: both calls were handed the same staging path")

    assert staging.exists(), (
        "the staging path a live writer was streaming into was renamed away")
    assert _live_bytes_at(staging) == len(_LIVE), (
        f"expected the live {len(_LIVE)} bytes at the staging path, found "
        f"{_live_bytes_at(staging)}")
    assert staging.stat().st_ino == out["minter_ino"], (
        "the file now at the staging path is NOT the inode the minter is "
        "streaming into; that writer will promote bytes nobody can see at "
        "the path, which is the 2026-08-29 corruption exactly")
    assert _orphans(tmp_path) == [], (
        f"a live download's own bytes were set aside as unowned: "
        f"{[p.name for p in _orphans(tmp_path)]}")


def test_a_mint_does_not_orphan_a_heal_that_settled_while_it_waited(tmp_path):
    """MINT SIDE -- the mirror. RED against v3.66.1395: A's own unconditional
    `_set_aside_unowned_bytes` renamed away the 5,000,000 bytes B was streaming
    after B had healed the very claim A published.

    The residue is seeded so the GENUINE heal is measured in the same run: it
    must still fire, exactly once, and the foreign bytes must still be set
    aside. A fix that closes this window by disabling the heal fails here."""
    final = tmp_path / "MirrorContended.mp4"
    staging, out, fired, _ = _two_claims_one_identity(
        tmp_path, final, residue=_FOREIGN, gate_the_healer=False)

    assert out["minter"] == staging and out["healer"] == staging, (
        "precondition: both calls were handed the same staging path")

    aside = _orphans(tmp_path)
    assert len(aside) == 1, (
        f"expected exactly one set-aside -- the genuine heal of the seeded "
        f"foreign bytes -- got {[p.name for p in aside]}; a second one is a "
        f"live writer's bytes orphaned by the mint's stale set-aside")
    assert aside[0].read_bytes() == _FOREIGN, (
        "the one set-aside file is not the foreign residue, so the heal was "
        "disabled and a live writer's bytes were orphaned instead")
    assert fired["set_aside"] == 1, (
        f"_set_aside_unowned_bytes fired {fired['set_aside']} times over one "
        f"claim; the second call is the stale one, and it renames whatever a "
        f"live writer has at the path")
    assert _live_bytes_at(staging) == len(_LIVE), (
        f"expected the live {len(_LIVE)} bytes at the staging path, found "
        f"{_live_bytes_at(staging)}")
    assert staging.stat().st_ino == out["healer_ino"], (
        "the file now at the staging path is NOT the inode the healer is "
        "streaming into; the mint's stale set-aside renamed it away")


def test_a_live_holder_refuses_the_heal_and_a_dead_one_releases_it(tmp_path):
    """WHY `flock` AND NOT A TOKEN FILE, pinned so a later refactor cannot
    swap the primitive silently.

    One on-disk state, two verdicts, discriminated only by whether the process
    that published the claim is still alive:

      ALIVE  -> the bytes may belong to a live mint; the heal must refuse
                (UNKNOWN is not permission, A2) rather than rename them.
      SIGKILL-> the kernel drops the lock, so the same state is CRASH RESIDUE
                and the heal must complete. An exclusive-create token would
                still be on disk here and would make crash residue -- the only
                state this branch exists for -- permanently unhealable.
    """
    final = tmp_path / "HeldByAnotherProcess.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(_FOREIGN)
    owner = sc.owner_path_for(staging)
    repo = str(pathlib.Path(sc.__file__).resolve().parents[1])

    child_src = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        from bulk_downloader import staging_claim as sc
        owner = Path(sys.argv[2])
        assert sc._create_owner(owner, sys.argv[3]) is True
        fd = sc._acquire_claim_lock(owner)
        assert fd is not None
        print("LOCKED", flush=True)
        time.sleep(600)
        """)
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    child = subprocess.Popen(
        [sys.executable, "-c", child_src, repo, str(owner), _ident(_JOB_A)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = child.stdout.readline().strip()
        assert line == "LOCKED", (
            f"precondition: the child never took the claim lock ({line!r}); "
            f"stderr: {child.stderr.read()!r}")
        assert owner.is_file(), "precondition: the child published its claim"
        assert sc._read_owner_record(owner) == (_ident(_JOB_A), False), (
            "precondition: the claim on disk reads OURS and UNPROVEN, which is "
            "the exact state the heal branch acts on")
        assert child.poll() is None, "precondition: the holder is alive"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sc, "_CLAIM_LOCK_DEADLINE_S", 0.5)
            with pytest.raises(sc.StagingUnavailable) as exc:
                sc.claim(final, _ident(_JOB_A))
        assert "lock" in str(exc.value).lower(), (
            f"the refusal must name the step that failed rather than collapse "
            f"into a generic unreadable-claim message: {exc.value}")
        assert staging.read_bytes() == _FOREIGN, (
            "the heal renamed bytes while another process held the claim")
        assert _orphans(tmp_path) == []
    finally:
        child.kill()
        child.wait(timeout=30)
        child.stdout.close()
        child.stderr.close()

    # Same bytes, same claim, same unproven record -- the holder is now dead.
    assert sc._read_owner_record(owner) == (_ident(_JOB_A), False), (
        "precondition: killing the holder did not change the record, so the "
        "only difference between the two verdicts is liveness")
    started = time.monotonic()
    got = sc.claim(final, _ident(_JOB_A))
    elapsed = time.monotonic() - started

    assert got == staging
    assert elapsed < sc._CLAIM_LOCK_DEADLINE_S, (
        f"the heal took {elapsed:.1f}s; a dead holder's lock was not released, "
        f"so crash residue is only healable by waiting out a deadline")
    assert _live_bytes_at(staging) == 0, "the heal did not set the residue aside"
    aside = _orphans(tmp_path)
    assert len(aside) == 1 and aside[0].read_bytes() == _FOREIGN, (
        f"the crash residue was not set aside after the holder died: "
        f"{[p.name for p in aside]}")
    assert sc._read_owner_record(owner) == (_ident(_JOB_A), True), (
        "the completed heal left the claim unproven, so the next reclaim "
        "measures this job's own bytes again")


def test_a_heal_refuses_where_exclusion_cannot_be_established(tmp_path):
    """The rename NEVER runs unlocked. On an interpreter with no `fcntl`,
    whether a live writer holds the claim is UNMEASURABLE, and A2 makes that
    UNKNOWN rather than permission.

    Fail-closed here and not best-effort like `_fsync_dir` because the two
    failure DIRECTIONS are opposite: a missing directory sync leaves a
    preserved, operator-visible orphan, while an unlocked rename is the
    corruption this section closes."""
    final, staging, owner = _crashed_mint(tmp_path)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "fcntl", None)
        with pytest.raises(sc.StagingUnavailable) as exc:
            sc.claim(final, _ident(_JOB_A))

    assert "lock" in str(exc.value).lower(), (
        f"the refusal must name the step that failed: {exc.value}")
    assert staging.read_bytes() == _FOREIGN, (
        "an UNKNOWN exclusion state still renamed the bytes")
    assert _orphans(tmp_path) == []
    assert owner.is_file(), (
        "the refused heal deleted a claim it did not create")


def test_a_mint_and_a_resume_still_work_where_exclusion_is_unavailable(tmp_path):
    """NEGATIVE CONTROL for the refusal above: it must bound the DESTRUCTIVE
    branch only. Refusing every claim on such a host would take the whole
    downloader down to close a race that cannot occur there -- no heal can run
    without the lock, so no stale-read pair can form."""
    final = tmp_path / "NoFlock.mp4"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "fcntl", None)
        staging = sc.claim(final, _ident(_JOB_A))
        assert sc._read_owner_record(sc.owner_path_for(staging)) == (
            _ident(_JOB_A), True), "the mint was refused for want of a lock"
        staging.write_bytes(_OURS)

        got = sc.claim(final, _ident(_JOB_A))

    assert got == staging
    assert staging.read_bytes() == _OURS, (
        "a resume was refused, or re-measured, for want of a lock")
    assert _orphans(tmp_path) == []


def test_a_lock_taken_on_a_replaced_claim_is_taken_again(tmp_path):
    """The lock has to guard the RECORD, not merely some inode that once lived
    at the claim's path.

    `_prove_owner` publishes with `os.replace`, so the claim path acquires a new
    inode every time somebody proves it -- and a worker that opened the old one
    a microsecond earlier ends up holding an exclusive lock on a detached file
    that contends with nothing. `global_config.py` keeps a permanent sibling
    lock file for exactly this reason; this module answers it by comparing the
    locked descriptor against the path and taking the lock again.

    Without that comparison the settle still READS the current record -- it
    reads by path -- so every outcome-shaped assertion in this file passes while
    the exclusion is silently void. A mutation battery found that: turning the
    comparison into `if True` escaped 23 green tests. This is the assertion that
    catches it, and it has to be about the descriptor rather than the outcome.
    """
    final, staging, owner = _crashed_mint(tmp_path)
    real_flock = sc.fcntl.flock
    swapped = {"n": 0}

    class ReplacesTheClaimOnFirstLock:
        """Another worker proves the claim in the window between our `open()`
        and our `flock()`, which is where the detached descriptor comes from."""
        LOCK_EX = sc.fcntl.LOCK_EX
        LOCK_NB = sc.fcntl.LOCK_NB

        @staticmethod
        def flock(fd, operation):
            if swapped["n"] == 0:
                swapped["n"] += 1
                tmp = owner.parent / "another-worker.proof"
                tmp.write_text(json.dumps(
                    {"v": sc.OWNER_FORMAT_VERSION, "job": _ident(_JOB_A),
                     sc.OWNER_PROOF_KEY: True}, sort_keys=True),
                    encoding="utf-8")
                os.replace(tmp, owner)
            return real_flock(fd, operation)

    before = owner.stat().st_ino
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "fcntl", ReplacesTheClaimOnFirstLock)
        fd = sc._acquire_claim_lock(owner)
    try:
        assert swapped["n"] == 1, (
            "precondition: the claim was never replaced under the lock, so "
            "this run did not exercise the detached-inode path")
        assert owner.stat().st_ino != before, (
            "precondition: os.replace did not give the claim a new inode")
        assert os.fstat(fd).st_ino == owner.stat().st_ino, (
            "the lock is held on an inode that is no longer at the claim path, "
            "so it contends with nobody and the record the settle then reads "
            "is guarded by nothing")
    finally:
        os.close(fd)

    # And the settle over that replaced record takes the resume branch: the
    # claim now reads proven, so the residue below it is this job's to keep.
    got = sc.claim(final, _ident(_JOB_A))
    assert got == staging
    assert staging.read_bytes() == _FOREIGN
    assert _orphans(tmp_path) == []
