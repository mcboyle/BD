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

AND UNKNOWN IS NOT PERMANENT EITHER. Refusing is the right answer to a state
this module cannot measure; refusing FOR EVER is not, and one shape had that
property. A killed publish on the no-hardlink fallback leaves the claim
pathname taken by a zero-byte file, which names no owner, guards no ``.part``,
and is therefore invisible to the orphan scan that globs ``*.part`` -- so it
refused every job whose template rendered that name, at the in-flight deadline
per attempt, with nothing anywhere that could clear it. A claim publishes only
what it has made true, and a publish that made nothing true must not outlive
the attempt that abandoned it: ``_adopt_abandoned_claim`` recovers that
pathname where the filesystem PROVES no live publisher can be standing in that
state, and the recovered claim is republished unproven so the bytes beneath it
are measured exactly as a fresh mint measures them. Every other UNKNOWN -- an
unreadable record, an unparseable one, a blank one where the window is real --
still refuses, because those states may be somebody's ownership and this one
cannot be.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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

# Version 2 added OWNER_PROOF_KEY. Version 3 adds OWNER_RESOURCE_KEY: the
# stable identity of the media resource whose bytes may occupy the staging
# path. A v1 record has no proof field because v1
# minted and set the bytes aside in ONE synchronous call, so reaching the
# reclaim branch at all meant the mint had completed -- see
# ``_read_owner_record`` for why that makes key-absent-on-v1 equal to proven,
# and why the same absence on a v2 record is UNKNOWN instead. Older code
# reading a v2 record still finds ``v`` and ``job`` where it expects them, so
# the format is readable in both directions across a rolling deploy.
OWNER_PROOF_FORMAT_VERSION = 2
OWNER_FORMAT_VERSION = 3

# The claim's second proof. ``job`` says WHO owns the staging path; this says
# whether the bytes AT that path have been accounted for by the mint that
# published the claim. The two are separate questions and a claim that answers
# only the first is exactly the state a crashed mint leaves behind.
OWNER_PROOF_KEY = "proven"
OWNER_RESOURCE_KEY = "resource"

# Mirrors detect.safe_dest's range: X, X_1 .. X_999, then a random suffix.
MAX_NUMBERED_CANDIDATES = 1000

_IDENTITY_HEX_LEN = 64  # sha256

# Only reachable on the no-hardlink fallback below, where a claim is created
# empty and filled a microsecond later. A reader that catches that instant sees
# an EMPTY file, which is in-flight rather than malformed; it waits this long
# for the writer to finish before calling ownership UNKNOWN. Every other
# unparseable shape is UNKNOWN immediately. On the primary path the claim is
# published complete by a single link(), so this window does not exist.
#
# ROW 528. Outliving this wait used to be the END of the story, and that turned
# a transient failure into a permanent one: a single zero-byte claim refuses
# every job whose template renders that name, at this cost per attempt, for
# ever. `crash_recovery.scan_for_orphans` globs `*.part` and such a claim has
# none, so nothing enumerated it and nothing could reap it. `_settle_claim` now
# RECOVERS the pathname instead -- but only where the filesystem PROVES no live
# publisher can be standing in that state, which is `_adopt_abandoned_claim`'s
# hardlink probe. The wait itself is unchanged and load-bearing: a publisher
# that finishes inside it is still honoured, and a recovery that fired on
# elapsed time alone would rob it.
_INFLIGHT_CLAIM_DEADLINE_S = 2.0
_INFLIGHT_POLL_S = 0.005

# `_adopt_abandoned_claim` publishes through a private temporary name, exactly
# as `_create_owner` and `_prove_owner` do. It is named here because the
# recovery is the one step whose interruption must leave the claim exactly as
# it was found, and a test that interrupts it has to be able to name it.
_ADOPT_TMP_SUFFIX = ".adopt"

# A settle may republish the record it is about to act on, and the lock it
# holds is then a lock on a detached inode -- so it re-acquires and re-reads
# rather than acting on what it just wrote. Bounded for the same reason
# `_CLAIM_LOCK_INODE_ATTEMPTS` is: an unbounded retry over a record somebody
# else is rewriting in a loop is a livelock wearing a safety hat.
_SETTLE_ATTEMPTS = 3

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


class EmptyStagingClaim(StagingUnavailable):
    """A claim file exists, is EMPTY, and stayed empty past the in-flight wait.

    A SUBCLASS on purpose, so every caller that refuses on
    ``StagingUnavailable`` -- and that is all of them -- keeps refusing exactly
    as it did before this name existed. It is a separate name for one reader
    only: ``_settle_claim`` has to tell this state apart from the other
    UNKNOWNs, because an empty file names NOBODY. An unreadable or unparseable
    record may still be somebody's ownership, so it is never overwritten; an
    empty one asserts nothing about anybody, and where the filesystem proves no
    live publisher could be inside a create-then-write window it is recoverable
    rather than permanent (row 528).
    """


class _ClaimRecovered(Exception):
    """Internal control signal: an abandoned blank claim was republished.

    Not a failure, and never visible outside this module. It says the record
    the current hold was taken on is no longer the record on disk, so
    ``_settle_claim`` must take the lock again and read what is actually there
    rather than act on what it has just written. See that function for why
    continuing instead would be the stale read this module exists to prevent.
    """


class StagingClaimedByAnotherJob(RuntimeError):
    """Ownership WAS measured and it belongs to a different job.

    This is a determinate answer, not an UNKNOWN: the claim was read and it
    names an identity that is not ours. ``reserve`` handles it by moving to the
    next candidate name; a caller that has no alternative name refuses.
    """


class StagingResourceMismatch(RuntimeError):
    """This job owns the path, but its staged bytes name another resource.

    This is determinate rather than UNKNOWN: both resource identities were
    measured and differ. The transport refuses before issuing a Range request,
    leaving the claim and its bytes untouched for an operator or later retry.
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


def resource_identity(file_url) -> str:
    """Stable identity of the media resource a sequential resume targets.

    Query strings and fragments are deliberately excluded: signed CDN URLs
    routinely rotate those components while continuing to name the same
    resource. A host or path change is treated conservatively as a different
    resource; a safe false mismatch costs a resume, while a false match splices
    bytes from two objects.
    """
    if not isinstance(file_url, str) or not file_url:
        raise StagingUnavailable(
            f"cannot derive resource provenance from {file_url!r}: no "
            "file_url was supplied, so a nonzero resume cannot be proven")
    parsed = urlsplit(file_url)
    if not parsed.scheme or not parsed.netloc:
        raise StagingUnavailable(
            f"cannot derive resource provenance from {file_url!r}: the media "
            "URL has no scheme or host")
    canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                            parsed.path or "/", "", ""))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _read_owner_details(owner_path: Path) -> tuple[str, bool, str | None]:
    """``(job identity, proven, resource identity)`` or UNKNOWN.

    An EMPTY claim means "another worker is publishing this right now" rather
    than "this is corrupt", and it is only reachable on the no-hardlink
    fallback in ``_create_owner``. It is waited out, briefly and against a
    deadline, so that losing a race does not present as an unreadable claim --
    which would be the self-inflicted version of the very defect this module
    exists to fix. Every other unparseable shape is UNKNOWN at once.

    Outliving that wait raises ``EmptyStagingClaim``, which IS a
    ``StagingUnavailable`` and refuses here exactly as every other UNKNOWN
    does. The distinct name exists so ``_settle_claim`` can recover the
    pathname where the filesystem proves no publisher could still be inside
    that window (row 528); this function never recovers anything itself,
    because ``release`` and ``crash_recovery`` read through it too and neither
    may rewrite a record it was merely inspecting.
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
            raise EmptyStagingClaim(
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
    return (identity, _read_proof(owner_path, record),
            _read_resource(owner_path, record))


def _read_owner_record(owner_path: Path) -> tuple[str, bool]:
    """Compatibility view of a claim's job identity and byte proof."""
    identity, proven, _resource = _read_owner_details(owner_path)
    return identity, proven


def _read_resource(owner_path: Path, record) -> str | None:
    """Read the bound media-resource identity, preserving safe migration.

    Claims written before resource binding cannot prove which media URL
    produced their bytes. That absence is represented as ``None``. A proven
    legacy claim is grandfathered to the first resource-aware reclaim: the
    older writer established that these were this job's bytes, preserving
    in-flight resumes across a rolling deployment.
    """
    if OWNER_RESOURCE_KEY in record:
        resource = record[OWNER_RESOURCE_KEY]
        if resource is None:
            return None
        if (isinstance(resource, str)
                and len(resource) == _IDENTITY_HEX_LEN
                and all(c in "0123456789abcdef" for c in resource)):
            return resource
        raise StagingUnavailable(
            f"staging claim {owner_path} carries a malformed "
            f"{OWNER_RESOURCE_KEY!r} field; resource provenance is UNKNOWN. "
            "Remove that file if it is stale.")
    return None


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
    if isinstance(version, int) and version < OWNER_PROOF_FORMAT_VERSION:
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


def _owner_payload(identity: str, proven: bool,
                   resource: str | None) -> bytes:
    return json.dumps(
        {"v": OWNER_FORMAT_VERSION, "job": identity,
         OWNER_PROOF_KEY: proven, OWNER_RESOURCE_KEY: resource},
        sort_keys=True).encode("utf-8")


def _create_owner(owner_path: Path, identity: str,
                  resource: str | None = None) -> bool:
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
    payload = _owner_payload(identity, False, resource)
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


def _rewrite_owner(owner_path: Path, identity: str, *, proven: bool,
                   resource: str | None) -> None:
    """Atomically replace a claim while retaining its measured fields."""
    payload = _owner_payload(identity, proven, resource)
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
    holder, _proven, resource = _read_owner_details(owner_path)
    if holder != identity:
        raise StagingClaimedByAnotherJob(
            f"staging claim {owner_path} changed owner before it could be "
            "proved")
    _rewrite_owner(owner_path, identity, proven=True, resource=resource)


def _adopt_abandoned_claim(owner_path: Path, identity: str) -> None:
    """Republish an ABANDONED empty claim as this job's complete UNPROVEN one.

    ROW 528. A zero-byte claim is what a killed no-hardlink publish leaves, and
    it names nobody: ``_create_owner``'s fallback creates the file with
    ``O_CREAT|O_EXCL`` and only then writes and fsyncs it, so a SIGKILL, a
    power loss, or a CIFS disconnect between those syscalls leaves the
    pathname taken and the record blank. Reading that as UNKNOWN for ever --
    which is what this module did -- refuses every job whose template renders
    that name, permanently, because ``reserve`` walks past a claim held by
    another job and NOT past an UNKNOWN, and ``crash_recovery`` globs ``*.part``
    and this state has none. A transient failure became a permanent one with no
    surface that could clear it.

    THE PROOF, AND WHY IT IS NOT A TIMEOUT. Elapsed time cannot decide this: a
    publisher inside its window is exactly what the in-flight wait exists to
    honour, and stealing its pathname would be this module inflicting its own
    defect on itself. What decides it is the FILESYSTEM. An empty claim can
    only be published by the fallback, and the fallback only runs where
    ``os.link`` does not. So this asks that question directly, here, at the
    moment of the decision: link the record we are about to publish to a second
    private name. If that works, hardlink publication is available in this
    directory, therefore ``_create_owner`` published by ``link`` and NOTHING
    can be standing in a create-then-write window at this path, therefore the
    empty file is abandoned residue. If it does not work, the window is real
    and the refusal stands -- UNKNOWN is not permission (A2).

    REPLACE, NEVER UNLINK. ``os.replace`` leaves the pathname occupied for
    every instant of the transition, so a worker waiting on the claim lock
    never sees the file absent -- which would raise its own distinct UNKNOWN
    and turn one worker's recovery into another's refusal. It also means the
    claim this call publishes is a claim, not a hole: a second recoverer
    serialised behind the lock reads a complete record naming somebody and
    diverts, rather than racing to mint over the same name.

    PUBLISHED UNPROVEN, because that is all this call has made true. It has
    established a name; it has measured nothing at the staging path. The
    caller's next act is the unproven branch of ``_settle_claim`` -- set the
    unaccounted-for bytes aside, then prove -- which is the same accounting a
    fresh mint does, for the same reason: bytes under a claim that nobody
    completed were accounted for by nobody.

    CALLED WITH THE CLAIM LOCK HELD, by ``_settle_claim_once`` and nothing
    else. The blank read and this republish are one hold precisely because a
    second worker that had also read blank could otherwise land here after the
    first had already proved its claim and started streaming.

    An interruption anywhere in here leaves the claim exactly as it was found
    -- still empty, no residue -- or, past the ``replace``, a complete record
    this job may reclaim and any other job diverts around. Neither wedges the
    name, which is the whole point of the row.
    """
    payload = _owner_payload(identity, False, None)
    unique = f"{os.getpid()}.{uuid.uuid4().hex}"
    tmp = owner_path.parent / f"{owner_path.name}.{unique}{_ADOPT_TMP_SUFFIX}"
    probe = owner_path.parent / f"{owner_path.name}.{unique}.probe"
    try:
        try:
            _write_complete(tmp, payload)
        except OSError as exc:
            raise StagingUnavailable(
                f"the abandoned staging claim {owner_path} cannot be "
                f"recovered: its replacement record cannot be staged "
                f"({type(exc).__name__}: {exc}); ownership stays UNKNOWN"
            ) from exc
        try:
            os.link(str(tmp), str(probe))
        except FileExistsError:
            # The probe name collided, which still reached the target check and
            # so still proves link() is implemented here. Nothing to clean up
            # that is ours, and nothing to conclude against the recovery.
            pass
        except OSError as exc:
            raise EmptyStagingClaim(
                f"staging claim {owner_path} is still empty after "
                f"{_INFLIGHT_CLAIM_DEADLINE_S}s; ownership is UNKNOWN. "
                "Remove that file if it is stale. It cannot be recovered "
                "automatically because this filesystem does not publish claims "
                f"atomically ({type(exc).__name__}: {exc}), so a live worker "
                "may still be filling it."
            ) from exc
        else:
            try:
                os.unlink(str(probe))
            except OSError:
                pass
        try:
            os.replace(str(tmp), str(owner_path))
        except OSError as exc:
            raise StagingUnavailable(
                f"the abandoned staging claim {owner_path} cannot be replaced "
                f"with a complete record ({type(exc).__name__}: {exc}); "
                "ownership stays UNKNOWN and the claim is left exactly as it "
                "was found") from exc
        _fsync_dir(owner_path.parent)
    finally:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass


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
                  minted: bool, resource: str | None = None) -> Path:
    """Settle the claim, RECOVERING an abandoned empty one first (row 528).

    ``_settle_claim_once`` does the whole settle under one lock. The only state
    it cannot settle is a claim file that is present and blank: it names no
    owner, so there is nothing to compare an identity against and nothing to
    prove, and reading it as UNKNOWN for ever is what made one killed publish
    refuse a filename permanently. It recovers that state IN PLACE, under the
    lock it already holds, and raises ``_ClaimRecovered`` to say the record it
    settled is no longer the record on disk.

    RE-RUN, not continue, and that is the load-bearing word. The recovery
    replaces the claim's inode, so the lock held across it is a lock on a
    detached inode the instant it lands: continuing to act under that lock --
    setting bytes aside, proving -- would be the stale-read defect
    ``_settle_claim_once`` exists to prevent, reintroduced by its own recovery
    (A7). The next attempt takes the lock on the record that is actually there
    and reads it again, so a rival that recovered the same claim first is seen
    as what it now is: a different owner, and it diverts.

    The recovery happening UNDER the lock is what makes one attempt enough.
    Were the blank read and the republish split across a release, two workers
    could each read blank, each wait the deadline out, and each republish --
    and the second would land on top of the first's already PROVEN record while
    its transport was streaming, then set those live bytes aside. That is the
    corruption this module exists to stop, so the read and the act on it are
    one hold, exactly as they are for the unproven branch.
    """
    for _ in range(_SETTLE_ATTEMPTS):
        try:
            return _settle_claim_once(
                staging, owner, identity, minted=minted, resource=resource)
        except _ClaimRecovered:
            continue
    raise StagingUnavailable(
        f"the staging claim {owner} was still blank after "
        f"{_SETTLE_ATTEMPTS} attempts to recover it, so whether this path is "
        "safe to stage into is UNKNOWN. Remove that file if it is stale.")


def _settle_claim_once(staging: Path, owner: Path, identity: str, *,
                       minted: bool, resource: str | None = None) -> Path:
    """Finish the claim at ``owner`` and return the staging path it guards.

    Raises ``_ClaimRecovered`` when it recovered an abandoned blank claim
    instead of settling one; the record on disk is then no longer the one this
    hold was taken on, and ``_settle_claim`` re-runs. See that function for why
    the recovery is inside this hold rather than around it.

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
    # `_acquire_claim_lock` returns a descriptor or raises; the one case it
    # returns nothing for is an interpreter with no `fcntl` at all. Name that
    # here, because two branches below turn on it and "we hold the lock" is
    # what they are actually asking.
    exclusive = lock_fd is not None
    try:
        try:
            holder, proven, held_resource = _read_owner_details(owner)
        except EmptyStagingClaim as blank:
            # ROW 528. The claim pathname is taken by a file that names nobody,
            # which is what a killed no-hardlink publish leaves. Recover it
            # here, holding this lock, and hand the caller back a `None` that
            # says the record it must act on is a different one now.
            #
            # Each refusal below carries its own reason on top of the blank
            # claim's message. Three distinct conditions decline to recover
            # this state and they lead to three different operator actions --
            # look for what truncated the record, install a working `fcntl`,
            # move the download directory off a filesystem without hardlinks --
            # so collapsing them into one diagnostic would cost exactly the
            # investigation A7 says it costs.
            if minted:
                # A MINT NEVER RECOVERS. `_create_owner` publishes a complete
                # record, so a blank one under a claim THIS call minted is a
                # file something else truncated: corruption, not residue.
                raise EmptyStagingClaim(
                    f"{blank} It is not recovered automatically because this "
                    "call published that claim complete moments ago, so a "
                    "blank record there was truncated by something else "
                    "rather than abandoned by a killed publish."
                ) from blank
            if not exclusive:
                # NO LOCK, NO RECOVERY, for the same reason the unproven branch
                # below refuses without one: two unexclusive recoverers could
                # each read blank and each republish, and the second would land
                # on the first's proven record and set its live bytes aside.
                raise EmptyStagingClaim(
                    f"{blank} It is not recovered automatically because this "
                    "interpreter cannot take a lock on it (no fcntl), so two "
                    "workers recovering the same claim could not be kept "
                    "apart."
                ) from blank
            _adopt_abandoned_claim(owner, identity)
            raise _ClaimRecovered()
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
            if resource is None:
                return staging
            if held_resource == resource:
                return staging

            # Job equality proves WHO owns the path, not WHICH media object
            # produced its bytes. Measure before binding or refusing. Claims
            # from before resource provenance are grandfathered to their first
            # resource-aware reclaim: their proof already establishes that the
            # bytes belong to this job, preserving in-flight upgrades. Once a
            # resource is bound, however, a different identity must never
            # append, regardless of byte count.
            try:
                staged_size = staging.stat().st_size
            except FileNotFoundError:
                staged_size = 0
            except OSError as exc:
                raise StagingUnavailable(
                    f"staging path {staging} cannot be measured while binding "
                    f"resource provenance ({type(exc).__name__}: {exc}); the "
                    "resume offset is UNKNOWN and is refused") from exc
            if held_resource is None:
                _rewrite_owner(
                    owner, identity, proven=True, resource=resource)
                return staging
            if staged_size > 0:
                raise StagingResourceMismatch(
                    f"staging resource mismatch for {staging}: this job's "
                    f"{staged_size} staged byte(s) belong to a different "
                    "media URL; refusing before a Range request")
            _rewrite_owner(owner, identity, proven=True, resource=resource)
            return staging
        if not exclusive and not minted:
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
        # ROW 535, refutation rank 3, and the general statement of it at rank
        # 43. The claim names us but was never completed, so the bytes beneath
        # it were never accounted for by anybody. The only way to reach this
        # state is a crash -- a SIGKILL or a deploy restart between
        # `_create_owner` and the `os.replace` inside
        # `_set_aside_unowned_bytes` -- which raises no exception, so the
        # row-533 unwind cannot see it. Measure them exactly as a fresh mint
        # would, then complete the claim so the NEXT reclaim is the resume path
        # above. Under the lock, "was never completed" is now a fact about the
        # present rather than a memory of an earlier read.
        try:
            _set_aside_unowned_bytes(staging)
            if resource is not None and resource != held_resource:
                _rewrite_owner(
                    owner, identity, proven=True, resource=resource)
            else:
                _prove_owner(owner, identity)
        except BaseException:
            # UNWIND ONLY WHAT THIS CALL PUBLISHED. Row 533: a claim this call
            # minted and could not make good on must not outlive the call, or
            # the next attempt reclaims it and resumes over foreign bytes.
            #
            # THE ASYMMETRY IS LOAD-BEARING. A reclaim must NOT unwind: the
            # claim pre-existed the call and may be guarding crash residue, and
            # deleting it is precisely how an ownerless `.part` is minted --
            # the rank-1 corruption this module's whole history is about. A
            # heal that cannot finish leaves the claim unproven and refuses;
            # the next attempt heals again.
            #
            # Reached only from inside the lock, having read UNPROVEN under it,
            # so the record being unlinked is still the one this call created:
            # a failure to ACQUIRE the lock raises before this point and
            # deliberately unwinds nothing, because by then another worker may
            # have proved that claim and be streaming under it.
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


def claim(final_path, identity: str, *, resource_url: str | None = None) -> Path:
    """Take (or reclaim) the staging path for exactly ``final_path``.

    Returns the staging path on success. Raises ``StagingClaimedByAnotherJob``
    when the claim is measured and belongs to somebody else, and
    ``StagingUnavailable`` when ownership cannot be measured at all.

    Idempotent for one identity and one media resource: a job that already
    holds the claim gets it back, which is what makes an interrupted download
    resume its own ``.part`` instead of being forced to restart. When
    ``resource_url`` is supplied, nonzero bytes are returned only when the
    owner record proves they were staged for that resource.
    """
    staging = staging_path_for(final_path)
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
    resource = (resource_identity(resource_url)
                if resource_url is not None else None)
    minted = _create_owner(owner, identity)
    return _settle_claim(
        staging, owner, identity, minted=minted, resource=resource)


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
