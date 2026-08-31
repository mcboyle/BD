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
import time
import uuid
from pathlib import Path

# The staging suffix is the one the transport has always used. It is defined
# HERE and consumed by the transport so the reservation and the writer cannot
# drift onto two different filenames -- a reservation that guards a path the
# writer does not use is a gate that cannot see its subject.
STAGING_SUFFIX = ".part"
OWNER_SUFFIX = ".owner"

OWNER_FORMAT_VERSION = 1

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


def _read_owner_identity(owner_path: Path) -> str:
    """The identity recorded in an existing claim, or UNKNOWN.

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
            raise StagingUnavailable(
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
    return identity


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
    ``_read_owner_identity`` covers.
    """
    payload = json.dumps(
        {"v": OWNER_FORMAT_VERSION, "job": identity},
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


def claim(final_path, identity: str) -> Path:
    """Take (or reclaim) the staging path for exactly ``final_path``.

    Returns the staging path on success. Raises ``StagingClaimedByAnotherJob``
    when the claim is measured and belongs to somebody else, and
    ``StagingUnavailable`` when ownership cannot be measured at all.

    Idempotent for one identity: a job that already holds the claim gets it
    back, which is what makes an interrupted download resume its own ``.part``
    instead of being forced to restart.
    """
    staging = staging_path_for(final_path)
    owner = owner_path_for(staging)
    if _create_owner(owner, identity):
        return staging
    if _read_owner_identity(owner) == identity:
        return staging
    raise StagingClaimedByAnotherJob(
        f"staging path {staging} is claimed by a different download; "
        "appending to it would splice two files together")


def release(staging_path) -> None:
    """Drop a claim once its ``.part`` is gone (promoted or deleted).

    Best-effort and idempotent. A leaked claim is inert -- it can only push a
    DIFFERENT job onto the next candidate name, never corrupt a file -- so a
    failure here is not worth failing a completed download over.
    """
    try:
        owner_path_for(staging_path).unlink(missing_ok=True)
    except OSError:
        pass


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
