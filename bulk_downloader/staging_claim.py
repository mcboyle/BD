"""Exclusive RESERVATION of a download's destination and its ``.part`` staging path.

THE DEFECT THIS EXISTS FOR (part-staging-collision, MEASURED at v3.66.1362).

``runner_transport._http_download`` derived its staging path from the final path
alone -- ``final_path.with_suffix(final_path.suffix + ".part")`` -- and the only
collision handling anywhere on the path was ``detect.safe_dest``, which probes
for an EXISTING FINAL FILE and returns a free name. That is check-then-act with
no reservation, and a site runs ``max_concurrent`` worker threads against one
shared download directory, so two jobs whose templated names render the same
string is routine rather than exotic:

    worker A   final=X.mp4 (does not exist) -> safe_dest returns X.mp4
               opens X.mp4.part "wb", streams scene A
    worker B   final=X.mp4 (STILL does not exist -- A has not promoted)
               -> safe_dest returns X.mp4 too
               sees stat(X.mp4.part).st_size > 0, calls that a resume,
               sends Range: bytes=N- for scene B's URL, gets 206,
               opens the SAME X.mp4.part in "ab" and APPENDS scene B's bytes
               onto scene A's, then promotes the concatenation to `done`.

The ``.part.meta`` validator sidecar is not a defence: it is absent whenever the
origin sends no ETag/Last-Modified, and it is deleted outright on a 200 restart.
When it IS present the If-Range mismatch turns the append into a ``wb`` TRUNCATE
of a file another worker is still writing through an open descriptor, which is
the same corruption wearing a different hat.

THE CONTRACT HERE. A staging path is RESERVED, never probed. Ownership is taken
by publishing ``<staging>.owner`` with a single atomic filesystem operation
(``link`` of an already-complete file, ``O_CREAT|O_EXCL`` where the filesystem
has no hardlinks), so of two racing workers exactly one wins and the loser
learns it lost. There is no window between "is it free" and "take it" because
there is no separate question. The owner file records the claiming job's
identity (the sha256 of its ``page_url``, which is what the runner already
treats as job identity), so:

  * a DIFFERENT job can never be handed a staging path that is already claimed;
  * the SAME job -- an interrupted download restarted later -- reclaims its own
    claim and resumes from its own ``.part``, which is the behaviour that must
    not be traded away to fix the collision.

A CLAIM IS PUBLISHED IN TWO STEPS BECAUSE IT ANSWERS TWO QUESTIONS. Naming an
owner is not the same as accounting for the bytes at the path that owner names,
and the gap between them is where this module has failed repeatedly. So the
claim is published UNPROVEN, the pre-existing bytes are measured and set aside,
and only then is the claim rewritten as proven. A reclaim reads that field:
proven means every byte at the staging path was written under this claim and
the interrupted download resumes from it untouched; unproven means the mint
that published it never finished, so the bytes beneath it are of no established
provenance and are measured exactly as a fresh mint would measure them.

That is what closes the crash window. A SIGKILL or a deploy restart between
publishing the claim and moving the bytes raises no exception, so no unwind can
see it; what it leaves on disk is a claim naming THIS job over bytes nobody
measured, and an idempotent reclaim used to hand that straight back to the
transport as a resume offset.

The mechanism is deliberately filesystem-level. Colliding writers can be
different threads, different ``SiteRunner`` instances, or different processes
(a deploy restart mid-download), so an in-process ``threading.Lock`` cannot be
the seam.

UNKNOWN IS NOT PERMISSION (CLAUDE.md A7). If ownership cannot be MEASURED -- the
owner file cannot be created for a reason other than "it already exists", or it
exists and cannot be read or does not parse -- this module raises
``StagingUnavailable`` and the caller refuses the download. It never falls
through to an unreserved staging path, because an unreserved staging path is
precisely the defect.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows, and anywhere else without POSIX advisory locks
    fcntl = None  # type: ignore[assignment]

# The staging suffix is the one the transport has always used. It is defined
# HERE and consumed by the transport so the reservation and the writer cannot
# drift onto two different filenames -- a reservation that guards a path the
# writer does not use is a gate that cannot see its subject.
STAGING_SUFFIX = ".part"
OWNER_SUFFIX = ".owner"

# Version 2 adds OWNER_PROOF_KEY. A v1 record has no proof field because v1
# minted and set the bytes aside in ONE synchronous call, so reaching the
# reclaim branch at all meant the mint had completed -- see
# ``_read_owner_record`` for why that makes key-absent-on-v1 equal to proven,
# and why the same absence on a v2 record is UNKNOWN instead. Older code
# reading a v2 record still finds ``v`` and ``job`` where it expects them, so
# the format is readable in both directions across a rolling deploy.
OWNER_FORMAT_VERSION = 2

# The claim's second proof. ``job`` says WHO owns the staging path; this says
# whether the bytes AT that path have been accounted for by the mint that
# published the claim. The two are separate questions and a claim that answers
# only the first is exactly the state a crashed mint leaves behind.
OWNER_PROOF_KEY = "proven"

# Mirrors detect.safe_dest's range: X, X_1 .. X_999, then a random suffix.
MAX_NUMBERED_CANDIDATES = 1000

_IDENTITY_HEX_LEN = 64  # sha256

# Only reachable on the no-hardlink fallback below, where a claim is created
# empty and filled a microsecond later. A reader that catches that instant sees
# an EMPTY file, which is in-flight rather than malformed; it waits this long
# for the writer to finish before calling ownership UNKNOWN. Every other
# unparseable shape is UNKNOWN immediately. On the primary path the claim is
# published complete by a single link(), so this window does not exist.
_INFLIGHT_CLAIM_DEADLINE_S = 2.0
_INFLIGHT_POLL_S = 0.005

# THE SETTLE LOCK. `claim()` decides whether to RENAME whatever is at the
# staging path, and it takes that decision from a claim record. Without
# exclusion the record can be made good by another worker between the read and
# the rename, and the rename then lands on a live writer's `.part` -- measured
# on both arms at v3.66.1395 and pinned in
# `tests/test_row535_a_reclaim_proves_the_bytes_it_adopts.py`.
#
# Generous, because expiry REFUSES a download. One hold spans a stat, a rename,
# a write, an fsync of the record and an fsync of the directory entry, and an
# fsync on a loaded spindle is measured in seconds rather than milliseconds; the
# deadline has to sit well above a slow hold or a busy host starts refusing
# claims that were never contended in any meaningful sense. An uncontended
# acquire -- which is every ordinary claim -- costs one open and one flock.
_CLAIM_LOCK_DEADLINE_S = 30.0
_CLAIM_LOCK_POLL_S = 0.005

# The claim file is REPLACED by `_prove_owner`, so a lock taken on it can be a
# lock on a detached inode. That is caught by comparing the locked descriptor
# with the path after the lock is held, and retried -- bounded, because an
# unbounded retry over a path somebody is rewriting in a loop is a livelock
# wearing a safety hat.
_CLAIM_LOCK_INODE_ATTEMPTS = 5


class StagingUnavailable(RuntimeError):
    """Ownership of a staging path could not be MEASURED.

    This is the UNKNOWN state, and UNKNOWN fails. Raised when the owner file
    cannot be created for any reason other than already existing, or when an
    existing owner file cannot be read or does not parse. The caller must
    refuse the transfer; it must NOT proceed against an unreserved path.
    """


class StagingClaimedByAnotherJob(RuntimeError):
    """Ownership WAS measured and it belongs to a different job.

    This is a determinate answer, not an UNKNOWN: the claim was read and it
    names an identity that is not ours. ``reserve`` handles it by moving to the
    next candidate name; a caller that has no alternative name refuses.
    """


class _ExpiredEmptyClaim(StagingUnavailable):
    """A no-hardlink fallback claim that outlived its writer.

    This stays private because it is an implementation distinction only:
    callers still see an unmeasurable claim as ``StagingUnavailable`` unless
    ``_settle_claim`` holds the claim's verified flock and can heal it.
    """


def job_identity(page_url) -> str:
    """The stable identity of a download job.

    ``page_url`` is what the runner itself already uses as the job key
    (``self.jobs[page_url]``, ``queue_upsert(site_id, page_url, ...)``), and it
    is stable across a restart -- which the media URL is not, because signed
    and expiring CDN URLs rotate. Keying on the media URL would make a genuine
    resume fail its own identity check and orphan its ``.part``.
    """
    if not isinstance(page_url, str) or not page_url:
        raise StagingUnavailable(
            "cannot derive a job identity from "
            f"{page_url!r}: no page_url, so ownership of the staging path "
            "cannot be established or checked")
    return hashlib.sha256(page_url.encode("utf-8")).hexdigest()


def staging_path_for(final_path) -> Path:
    """The ``.part`` path for ``final_path``. THE single definition."""
    final_path = Path(final_path)
    return final_path.with_suffix(final_path.suffix + STAGING_SUFFIX)


def owner_path_for(staging_path) -> Path:
    """The claim file guarding ``staging_path``.

    Built by string concatenation rather than ``with_suffix`` so a staging name
    containing dots cannot lose a component.
    """
    return Path(str(staging_path) + OWNER_SUFFIX)


def _read_owner_record(owner_path: Path) -> tuple[str, bool]:
    """``(identity, proven)`` for an existing claim, or UNKNOWN.

    An EMPTY claim means "another worker is publishing this right now" rather
    than "this is corrupt", and it is only reachable on the no-hardlink
    fallback in ``_create_owner``. It is waited out, briefly and against a
    deadline, so that losing a race does not present as an unreadable claim --
    which would be the self-inflicted version of the very defect this module
    exists to fix. Every other unparseable shape is UNKNOWN at once.
    """
    deadline = time.monotonic() + _INFLIGHT_CLAIM_DEADLINE_S
    while True:
        try:
            raw = owner_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StagingUnavailable(
                f"staging claim {owner_path} exists and cannot be read "
                f"({type(exc).__name__}: {exc}); ownership is UNKNOWN, so "
                "this download refuses rather than write into a path it "
                "cannot prove it owns. Remove that file if it is stale."
            ) from exc
        except UnicodeDecodeError as exc:
            raise StagingUnavailable(
                f"staging claim {owner_path} is not valid UTF-8; ownership "
                "is UNKNOWN. Remove that file if it is stale.") from exc
        if raw != "":
            break
        if time.monotonic() >= deadline:
            raise _ExpiredEmptyClaim(
                f"staging claim {owner_path} is still empty after "
                f"{_INFLIGHT_CLAIM_DEADLINE_S}s; ownership is UNKNOWN. "
                "Remove that file if it is stale.")
        time.sleep(_INFLIGHT_POLL_S)
    try:
        record = json.loads(raw)
        identity = record["job"]
    except Exception as exc:
        raise StagingUnavailable(
            f"staging claim {owner_path} does not parse as a claim record "
            f"({type(exc).__name__}); ownership is UNKNOWN. Remove that file "
            "if it is stale.") from exc
    if not isinstance(identity, str) or len(identity) != _IDENTITY_HEX_LEN:
        raise StagingUnavailable(
            f"staging claim {owner_path} carries a malformed job identity; "
            "ownership is UNKNOWN. Remove that file if it is stale.")
    return identity, _read_proof(owner_path, record)


def _read_proof(owner_path: Path, record) -> bool:
    """Whether ``record`` says the bytes under its claim were accounted for.

    THE MIGRATION IS THE DANGEROUS CASE, and it is dangerous in the direction
    this whole module exists to prevent. Every download in flight at the moment
    this version deploys holds a v1 claim, which has no proof field. Reading
    that absence as "unproven" would make the first retry of each of them set
    aside its OWN multi-gigabyte ``.part`` and restart at byte 0 -- row 541's
    consequence, manufactured fleet-wide by the fix written to close row 535.
    A v1 mint set the bytes aside inside the same synchronous call that
    published the claim, so a v1 record that exists at all is a record whose
    mint completed: key-absent on a pre-proof version IS proven.

    The same absence on a record that DECLARES this version is a different
    fact. Nothing writes that shape, so it is malformed, and a malformed claim
    is UNKNOWN rather than permission (A2). The version field is the
    discriminator that keeps those two absences apart; without it the
    grandfather clause would be an unbounded fail-open.
    """
    if OWNER_PROOF_KEY in record:
        proof = record[OWNER_PROOF_KEY]
        if not isinstance(proof, bool):
            raise StagingUnavailable(
                f"staging claim {owner_path} carries a malformed {OWNER_PROOF_KEY!r} "
                f"proof field ({type(proof).__name__}); whether the bytes under "
                "this claim were ever accounted for is UNKNOWN. Remove that "
                "file if it is stale.")
        return proof
    version = record.get("v")
    if isinstance(version, int) and version < OWNER_FORMAT_VERSION:
        return True
    raise StagingUnavailable(
        f"staging claim {owner_path} declares format v{version!r} but carries "
        f"no {OWNER_PROOF_KEY!r} proof field, so whether the bytes under it "
        "were ever accounted for is UNKNOWN. Remove that file if it is stale.")


def _read_owner_identity(owner_path: Path) -> str:
    """The identity recorded in an existing claim, or UNKNOWN.

    The identity half of ``_read_owner_record``, kept as its own name because
    ``release`` asks only that question.
    """
    return _read_owner_record(owner_path)[0]


def _write_complete(path: Path, payload: bytes) -> None:
    """Create ``path`` exclusively and leave it complete on disk, or raise."""
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _create_owner(owner_path: Path, identity: str) -> bool:
    """Atomically publish the claim. True if WE created it, False if it existed.

    The claim is written to a private temporary name FIRST and published with
    ``os.link``, which is atomic and refuses an existing target. That ordering
    is the point: the instant the claim becomes visible to another worker it is
    already complete, so a loser can never read a half-written claim and be
    forced to call ownership UNKNOWN. (This gate's own 16-thread control
    measured exactly that failure against a create-then-write claim.)

    ``os.link`` is unavailable on a few filesystems a self-hosted download
    directory might sit on (plain CIFS, FAT). There the code falls back to
    ``O_CREAT|O_EXCL`` plus a write, which is still exclusive -- only the
    publish is no longer instantaneous, which is what the empty-claim wait in
    ``_read_owner_record`` covers.

    The published claim is UNPROVEN: it names an owner, and it says so plainly,
    but the bytes at the staging path it guards have not been measured yet --
    ``claim`` does that next and only then calls ``_prove_owner``. Publishing
    the proof here instead would be a claim asserting something no code had
    checked, which is the shape of every defect in this file's history.
    """
    payload = json.dumps(
        {"v": OWNER_FORMAT_VERSION, "job": identity, OWNER_PROOF_KEY: False},
        sort_keys=True).encode("utf-8")
    tmp = owner_path.parent / (
        f"{owner_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _write_complete(tmp, payload)
    except OSError as exc:
        raise StagingUnavailable(
            f"cannot stage the staging claim {owner_path} "
            f"({type(exc).__name__}: {exc}); ownership cannot be "
            "established, so this download refuses rather than stage into "
            "an unreserved path") from exc
    try:
        try:
            os.link(str(tmp), str(owner_path))
            return True
        except FileExistsError:
            return False
        except OSError:
            # No hardlink support here. Fall back to an exclusive create.
            pass
        try:
            _write_complete(owner_path, payload)
            return True
        except FileExistsError:
            return False
        except OSError as exc:
            try:
                os.unlink(str(owner_path))
            except OSError:
                pass
            raise StagingUnavailable(
                f"cannot record the staging claim {owner_path} "
                f"({type(exc).__name__}: {exc}); ownership cannot be "
                "established") from exc
    finally:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass


def _fsync_dir(directory: Path) -> None:
    """Make a rename in ``directory`` durable. Best-effort, and here is why.

    ``_write_complete`` fsyncs the claim's CONTENTS; the rename that publishes
    it is a directory operation and survives a power loss only once the
    directory entry is committed. Without this, a machine that lost power
    seconds after a proof was written would come back with the claim reading
    UNPROVEN over bytes the transport had since streamed into it -- and the
    reclaim would set the job's own multi-gigabyte partial aside as
    unaccounted-for. That is row 541's consequence reached through the fix for
    row 535, which is exactly the shape A7 says to go looking for.

    Best-effort rather than fatal because opening a directory for fsync is not
    portable -- Windows refuses it outright, and some network filesystems do
    too -- and a download must not be refused on a host whose filesystem simply
    does not offer the call. The residual window is a power loss inside the
    same instant, and it fails toward a preserved, operator-visible
    ``.orphaned-*.part`` rather than toward a splice.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _prove_owner(owner_path: Path, identity: str) -> None:
    """Record that the bytes this claim names have been accounted for.

    Called only once ``_set_aside_unowned_bytes`` has returned, i.e. once the
    staging path is provably absent or empty. Everything written there
    afterwards was written under this claim, so a later reclaim by the same job
    may resume from it without measuring it again -- which is the resume
    behaviour this module refuses to trade away.

    Published with ``os.replace`` onto the existing claim, which is atomic: a
    racing reader sees either the complete unproven record or the complete
    proven one, never a half-written file. That matters for the same reason the
    ``link``-publish in ``_create_owner`` does -- a reader forced to call a
    torn claim UNKNOWN is this module inflicting its own defect on itself.

    A failure here is UNKNOWN and is raised. The caller must not return a
    staging path under a claim that still says unproven: the transport would
    stream gigabytes into it and the NEXT reclaim, reading unproven, would set
    those bytes aside as unaccounted-for -- destroying the job's own work.
    """
    payload = json.dumps(
        {"v": OWNER_FORMAT_VERSION, "job": identity, OWNER_PROOF_KEY: True},
        sort_keys=True).encode("utf-8")
    tmp = owner_path.parent / (
        f"{owner_path.name}.{os.getpid()}.{uuid.uuid4().hex}.proof")
    try:
        _write_complete(tmp, payload)
        os.replace(str(tmp), str(owner_path))
        _fsync_dir(owner_path.parent)
    except OSError as exc:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise StagingUnavailable(
            f"the staging claim {owner_path} cannot record that its bytes "
            f"were accounted for ({type(exc).__name__}: {exc}); whether this "
            "path is safe to stage into is UNKNOWN, so this download refuses "
            "rather than write under a claim that proves nothing") from exc


def _set_aside_unowned_bytes(staging: Path) -> None:
    """Move pre-existing staging bytes out of a freshly claimed path.

    Nothing is deleted. The bytes keep a ``.part`` suffix so
    ``crash_recovery.scan_for_orphans`` -- which globs ``*.part`` and only
    LISTS -- can still see them, and ``delete_orphan`` can still reap them on
    operator command. Destroying them here would trade one silent data loss for
    another.

    An EMPTY file is left alone: zero bytes are no resume offset and no hazard,
    and setting them aside would leave an empty orphan reported forever.

    A failure to move is not survivable. If the bytes cannot be got out of the
    way, the claim cannot honestly say it owns the path, so the download refuses
    rather than resume over provenance it cannot establish (A2: an unmeasurable
    state is UNKNOWN, never permission).
    """
    try:
        size = staging.stat().st_size
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StagingUnavailable(
            f"staging path {staging} exists and cannot be measured "
            f"({type(exc).__name__}: {exc}); its bytes have no provable owner, "
            "so this download refuses rather than resume over them") from exc
    if size == 0:
        return
    aside = staging.parent / (
        f"{staging.name}.orphaned-{int(time.time())}.{uuid.uuid4().hex[:6]}.part")
    try:
        os.replace(str(staging), str(aside))
    except OSError as exc:
        raise StagingUnavailable(
            f"staging path {staging} holds {size} byte(s) that no claim owns "
            f"and they cannot be moved aside ({type(exc).__name__}: {exc}); "
            "this download refuses rather than append onto bytes it cannot "
            "prove are its own") from exc


def _acquire_claim_lock(owner_path: Path):
    """Exclusive access to the claim now at ``owner_path``. CLOSE THE FD TO RELEASE.

    Returns an open file descriptor, or ``None`` when this interpreter has no
    ``fcntl`` at all -- ``_settle_claim`` decides what that absence licenses,
    and the answer is different for the two arms.

    WHY A KERNEL LOCK AND NOT A TOKEN FILE. The question the reclaim branch is
    really asking is "is a live writer inside the publish-to-prove window, or
    did a crash abandon a claim there". An ``O_CREAT|O_EXCL`` token answers that
    question "live" forever, because a SIGKILLed process leaves its token on
    disk -- and crash residue is the ONLY state the reclaim heal exists for, so
    a token would make the branch permanently unreachable. ``flock`` is dropped
    by the kernel when the holder dies, which is exactly the discrimination
    wanted. It is also per open file description rather than per process, so two
    threads of one interpreter contend with each other as well.

    WHY THE CLAIM FILE ITSELF AND NOT A SIBLING ``.lock``.
    ``app_config_transaction`` elsewhere in this package keeps a permanent lock
    file precisely because locking a replaceable inode would not coordinate a
    process that opened it after the rename, and ``_prove_owner`` does replace
    this one. (Named by function rather than by module on purpose: the
    dependency graph classifies a config-store READER by a word-boundary regex
    over raw file text, so writing that module's name in a comment here would
    have invented an edge this module does not have.) The answer here is the
    descriptor-versus-path comparison below rather than a second file: a
    permanent sidecar would litter the operator's download directory with a
    lock per staged file, and unlinking it would be its own race. The comparison
    is what makes the lock provably a lock ON THE RECORD the caller then reads
    -- the whole point of the exercise, since a lock over a detached inode is a
    re-read that guards nothing.

    Every failure to establish exclusion raises ``StagingUnavailable`` naming
    the step that failed (A7: a diagnostic that collapses distinct failures
    costs the investigation). It is never returned as "no lock", because a
    caller cannot tell a lock it does not hold from one it does.
    """
    if fcntl is None:
        return None
    deadline = time.monotonic() + _CLAIM_LOCK_DEADLINE_S
    for _ in range(_CLAIM_LOCK_INODE_ATTEMPTS):
        try:
            fd = os.open(str(owner_path), os.O_RDONLY)
        except FileNotFoundError as exc:
            raise StagingUnavailable(
                f"the staging claim {owner_path} was gone before a lock could "
                "be taken on it, so nothing can be shown to guard the record "
                "this call is about to read; ownership is UNKNOWN") from exc
        except OSError as exc:
            raise StagingUnavailable(
                f"the staging claim {owner_path} cannot be opened to lock it "
                f"({type(exc).__name__}: {exc}); whether another worker is "
                "inside this claim's publish-to-prove window is UNKNOWN") from exc
        keep = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StagingUnavailable(
                            f"the staging claim {owner_path} is still locked by "
                            f"another worker after {_CLAIM_LOCK_DEADLINE_S}s; "
                            "whether the bytes at its staging path belong to a "
                            "live download is UNKNOWN, so this call refuses "
                            "rather than settle over them")
                    time.sleep(_CLAIM_LOCK_POLL_S)
                except OSError as exc:
                    raise StagingUnavailable(
                        f"the staging claim {owner_path} cannot be locked "
                        f"({type(exc).__name__}: {exc}); whether another worker "
                        "is inside this claim's publish-to-prove window is "
                        "UNKNOWN") from exc
            locked = os.fstat(fd)
            try:
                current = os.stat(str(owner_path))
            except FileNotFoundError as exc:
                raise StagingUnavailable(
                    f"the staging claim {owner_path} was removed while a lock "
                    "was being taken on it; ownership is UNKNOWN") from exc
            except OSError as exc:
                raise StagingUnavailable(
                    f"the staging claim {owner_path} cannot be identified after "
                    f"locking it ({type(exc).__name__}: {exc}); whether the lock "
                    "guards the record this call reads is UNKNOWN") from exc
            if (locked.st_dev, locked.st_ino) == (current.st_dev, current.st_ino):
                keep = True
                return fd
            # The record was replaced (a prove) or unlinked and re-minted while
            # we were taking the lock. The descriptor we hold is detached, so
            # the lock guards nothing the next read will see. Take it again on
            # the inode that is actually there.
        finally:
            if not keep:
                os.close(fd)
    raise StagingUnavailable(
        f"the staging claim {owner_path} was replaced "
        f"{_CLAIM_LOCK_INODE_ATTEMPTS} times while a lock was being taken on "
        "it, so no lock can be shown to guard the record this call reads; "
        "ownership is UNKNOWN. Remove that file if it is stale.")


def _settle_claim(staging: Path, owner: Path, identity: str, *,
                  minted: bool) -> Path:
    """Finish the claim at ``owner`` and return the staging path it guards.

    ONE routine for both arms of ``claim()``, and the read that decides what to
    do happens HERE, inside the lock, rather than in the caller.

    THE DEFECT THIS EXISTS FOR (F3 of the 2026-09-01 refutation, and its
    mirror). v3.66.1395 turned the same-identity reclaim branch from a bare
    ``return staging`` into ``_set_aside_unowned_bytes; _prove_owner``, i.e. it
    put a RENAME behind a decision taken from a record read earlier in the same
    call. ``job_identity`` is ``sha256(page_url)`` and stable across processes,
    so two workers on one page_url share an identity and both reach that branch.
    Both arms then act on a stale read:

        HEAL SIDE   A publishes an UNPROVEN claim; B reads it; A completes its
                    mint and the transport streams into the staging path; B
                    wakes holding its stale unproven read and renames A's live
                    `.part` to `.orphaned-*`. A goes on writing to an inode
                    that is no longer at that path and promotes something that
                    is not what is there -- the 2026-08-29 corruption, reached
                    through the fix written to prevent it.

        MINT SIDE   A publishes an UNPROVEN claim and is descheduled; B heals
                    it, proves it, returns, and streams; A wakes and runs its
                    own unconditional set-aside over B's live bytes. Same
                    corruption, other branch. A re-read placed only in the
                    reclaim arm would leave this half open, which is why there
                    is one settle and not two guards.

    WHAT MAKES THE RE-READ SOUND. It is not that it happens late; it is that
    every actor able to turn this record proven must hold the same lock across
    that transition, and the rename runs only while holding the lock and having
    read UNPROVEN under it. A re-read that is not atomic with the action it
    guards is the same defect one step smaller (A7).

    The lock is released before the staging path is returned, and that is safe:
    once the record reads proven, every later reader takes the resume branch,
    which touches nothing.
    """
    lock_fd = _acquire_claim_lock(owner)
    retry_expired_empty = False
    try:
        try:
            holder, proven = _read_owner_record(owner)
        except _ExpiredEmptyClaim as exc:
            if lock_fd is None:
                raise StagingUnavailable(
                    f"the empty staging claim {owner} outlived its publisher, "
                    "but this interpreter cannot lock it (no fcntl), so whether "
                    "another writer is about to finish it is UNKNOWN") from exc
            try:
                owner.unlink()
            except OSError as unlink_exc:
                raise StagingUnavailable(
                    f"the empty staging claim {owner} outlived its publisher "
                    f"but cannot be removed while its verified lock is held "
                    f"({type(unlink_exc).__name__}: {unlink_exc}); ownership "
                    "remains UNKNOWN") from unlink_exc
            retry_expired_empty = True
        if retry_expired_empty:
            # The locked inode was the empty record and it is now gone. Drop
            # that lock before a fresh atomic mint; keeping it would protect a
            # detached inode, the exact shape `_acquire_claim_lock` rejects.
            pass
        else:
            if holder != identity:
                raise StagingClaimedByAnotherJob(
                    f"staging path {staging} is claimed by a different download; "
                    "appending to it would splice two files together")
            if proven:
                # The claim was completed, so every byte at the staging path was
                # written under it. This is the interrupted download resuming its
                # own `.part`, and it must not be re-measured: doing so would
                # rename this job's own multi-gigabyte partial to `.orphaned-*` and
                # restart it at byte 0 (row 541), which nothing ever reaps.
                #
                # For a MINT this is the mirror case above: another worker settled
                # the claim we published while we were descheduled, and its
                # transport may already be streaming. Returning here without
                # touching anything is the whole fix for that arm.
                return staging
            if lock_fd is None and not minted:
                # No `fcntl` in this interpreter, so exclusion cannot be
                # established -- and UNKNOWN is not permission to rename bytes that
                # may belong to a live writer (A2). Fail CLOSED here rather than
                # best-effort as `_fsync_dir` does, because the failure DIRECTIONS
                # are opposite: a missing directory sync leaves a preserved,
                # operator-visible orphan, while an unlocked rename is the
                # corruption this function exists to stop.
                #
                # Bounded to the destructive branch on purpose. A mint below still
                # proceeds on such a host, and no stale-read PAIR can form there:
                # the heal is the other half of every pair and it refuses here, and
                # only one caller per claim can ever be the minter.
                raise StagingUnavailable(
                    f"the staging claim {owner} reads UNPROVEN and this "
                    "interpreter cannot take a lock on it (no fcntl), so whether a "
                    "live writer holds it or a crash abandoned it is UNKNOWN; this "
                    "download refuses rather than set aside bytes that may be a "
                    "running transfer's")
            try:
                _set_aside_unowned_bytes(staging)
                _prove_owner(owner, identity)
            except BaseException:
                if minted:
                    try:
                        owner.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            return staging
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    if retry_expired_empty:
        return claim_staging_path(staging, identity)


def claim(final_path, identity: str) -> Path:
    """Take (or reclaim) the staging path for exactly ``final_path``.

    Returns the staging path on success. Raises ``StagingClaimedByAnotherJob``
    when the claim is measured and belongs to somebody else, and
    ``StagingUnavailable`` when ownership cannot be measured at all.

    Idempotent for one identity: a job that already holds the claim gets it
    back, which is what makes an interrupted download resume its own ``.part``
    instead of being forced to restart.
    """
    return claim_staging_path(staging_path_for(final_path), identity)


def claim_staging_path(staging_path, identity: str) -> Path:
    """Take a claim for this exact path, including a RAM-disk staging path."""
    staging = Path(staging_path)
    owner = owner_path_for(staging)
    # Row 481. A FRESH mint owns none of the bytes that were already there.
    # This module's only two exists() probes test the FINAL candidate, so until
    # v3.66.1391 the .part's presence, size and provenance were never measured
    # -- and reserve() skips a candidate only when the final file exists, which
    # an abandoned .part by definition does not. The next unrelated job
    # rendering that name therefore reclaimed foreign bytes, took resume_from
    # from their size, sent no If-Range (the .part.meta sidecar is absent
    # whenever the origin gave no validator), got a 206, appended, and promoted
    # the concatenation under its own title.
    #
    # Whether this call minted the claim decides exactly one thing -- whether a
    # failed set-aside may unwind it -- and nothing else. Everything the two
    # arms used to decide separately is decided once, under the lock, in
    # `_settle_claim`: the record a mint published can be made good by another
    # worker before this call gets to act on it, so a mint that trusted its own
    # `_create_owner` return was reading state as stale as the reclaim was.
    minted = _create_owner(owner, identity)
    return _settle_claim(staging, owner, identity, minted=minted)


def release(staging_path, identity: str | None = None, *, force: bool = False) -> bool:
    """Drop THIS job's claim, once its ``.part`` is gone. Returns what it did.

    Rows 492 and 489. This was ``release(staging_path) -> None``: no identity,
    no claim read, an unconditional unlink, and a docstring calling a leaked
    claim inert -- true only for the caller that owns it. It was the single
    state-MUTATING entry point in a module built on identity, where claim()
    refuses a foreign claim and reserve() diverts around one.

    Two proofs are now required before a claim is dropped, and each answers a
    measured defect:

      IDENTITY (492). A claim recording a different job is left alone. Freeing
      it hands that job's staging path to a third while the second is still
      writing into it.

      THE PART IS GONE (489). release's own stated precondition, never checked.
      The browser fallback releases after _http_download raised -- a path that
      removes neither the staged bytes nor the claim -- so the .part outlived
      its claim, and the next job rendering that name adopted the bytes.

    ``force=True`` is the operator-driven sweep (crash_recovery.delete_orphan),
    which genuinely may free a foreign claim; it must say so rather than
    inherit the old behaviour by omission. Calling with no identity at all is
    refused: a caller that cannot name itself cannot prove ownership, and an
    unmeasurable state is never permission (A2).

    Returns True when no claim remains for this job, False when one was
    deliberately retained. Never raises for an I/O failure -- a leaked claim is
    not worth failing a completed download over -- but a retained claim is
    reported so a caller can say so.
    """
    owner = owner_path_for(staging_path)
    if force:
        try:
            owner.unlink(missing_ok=True)
        except OSError:
            return False
        return True
    if identity is None:
        raise ValueError(
            f"release({staging_path!r}) was called with no job identity, so it "
            "cannot prove the claim is its own. Pass the identity, or pass "
            "force=True if this is the operator-driven sweep.")
    try:
        if not owner.exists():
            return True                      # idempotent: nothing to drop
    except OSError:
        return False
    try:
        holder = _read_owner_identity(owner)
    except StagingUnavailable:
        # An unreadable claim is UNKNOWN, and UNKNOWN is not permission to
        # delete somebody's ownership record.
        return False
    if holder != identity:
        return False
    try:
        staged = pathlib.Path(staging_path)
        if staged.exists() and staged.stat().st_size > 0:
            # The bytes outlive the claim. Dropping it here is exactly how an
            # abandoned .part becomes unowned and is adopted by the next job.
            return False
    except OSError:
        return False
    try:
        owner.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def discard_owned_staging(staging_path, identity: str, *, extra_paths=()) -> int:
    """Remove an abandoned partial and its claim only while its record is locked.

    Queue cleanup is destructive, so it cannot decide from an owner record it
    read before unlinking files. The verified descriptor/path lock and re-read
    are the same settle protocol used for reclaim; a foreign or unreadable
    claim is left untouched and reports zero removals.
    """
    staging = Path(staging_path)
    owner = owner_path_for(staging)
    try:
        lock_fd = _acquire_claim_lock(owner)
    except StagingUnavailable:
        return 0
    try:
        try:
            holder = _read_owner_identity(owner)
        except StagingUnavailable:
            return 0
        if holder != identity:
            return 0
        removed = 0
        for path in (staging, *extra_paths):
            try:
                Path(path).unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                return removed
        try:
            owner.unlink()
            return removed + 1
        except OSError:
            return removed
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def reserve(final_path, identity: str):
    """Reserve a free ``(final_path, staging_path)`` pair for ``identity``.

    Replaces ``detect.safe_dest`` on the download path. safe_dest answered
    "is this final name free right now", which two workers can both answer yes
    to; this answers "is this name MINE", and only one worker can be told yes.

    Candidate order mirrors safe_dest exactly (``X``, ``X_1`` .. ``X_999``,
    then a random suffix) so a collision produces the filenames operators
    already recognise.
    """
    final_path = Path(final_path)
    parent, stem, suffix = final_path.parent, final_path.stem, final_path.suffix
    for i in range(MAX_NUMBERED_CANDIDATES):
        candidate = final_path if i == 0 else parent / f"{stem}_{i}{suffix}"
        if candidate.exists():
            # An existing FINAL file is somebody's finished work; skip it for
            # the same reason safe_dest did.
            continue
        try:
            return candidate, claim(candidate, identity)
        except StagingClaimedByAnotherJob:
            continue
    candidate = parent / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
    if not candidate.exists():
        try:
            return candidate, claim(candidate, identity)
        except StagingClaimedByAnotherJob:
            pass
    raise StagingUnavailable(
        f"no free staging path for {final_path}: every candidate name is "
        "claimed by another download, so this transfer refuses rather than "
        "share a staging file")
