"""Crash recovery (Phase 195).

Half-finished downloads leave `.part` files on disk + `.meta` sidecars
holding resume state. When the worker crashes mid-download (power
loss, OS reboot, OOM kill), these stick around forever.

The runner's resume logic handles the "operator manually restarts the
download" case, but there's no UI surfacing of orphaned files. They
just take up disk silently.

This module scans each site's download_dir for `.part` files and consults the
same durable staging claim plus live runner population the transport writes.
Only measured absence of both is returned to the Review-tab card.

Two thresholds:
  • <24h old: probably still active, hide from the orphan view
  • ≥24h old: candidate for review (resume / delete / ignore)

Resumption uses the existing runner path — just re-enqueueing the URL
re-engages resume logic. Delete first wins an atomic staging reservation, then
unlinks the part and sidecars and releases that exact reservation.
Ignore just marks the file in the orphans table as "operator chose to
keep" so future scans skip it.
"""
from __future__ import annotations

import os
import stat as _stat
import time
from pathlib import Path
from typing import Optional

from . import db as _db
from . import staging_claim as _staging_claim


_ORPHAN_AGE_THRESHOLD_S = 24 * 3600  # 24h
_TABLE_READY = False
_DELETE_CLAIM_URL = "urn:bulk-downloader:crash-recovery:operator-delete"
_DELETE_CLAIM_IDENTITY = _staging_claim.job_identity(_DELETE_CLAIM_URL)


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _db.db_conn() as cx:
            # Tracks per-file operator decisions so we don't re-prompt.
            # `decision` is one of: 'ignore' (keep, don't ask again),
            # 'deleted' (record of past delete for audit), 'resumed'.
            cx.execute("""
                CREATE TABLE IF NOT EXISTS crash_recovery_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    ts REAL NOT NULL,
                    site_id TEXT DEFAULT ''
                )
            """)
            cx.execute("""
                CREATE INDEX IF NOT EXISTS idx_crash_recovery_path
                ON crash_recovery_decisions(path)
            """)
        _TABLE_READY = True
    except Exception:
        # Fail-open: scan still works, just can't persist decisions
        pass


def _ignored_paths() -> set:
    """Files the operator already told us to leave alone."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT path FROM crash_recovery_decisions
                WHERE decision = 'ignore'
            """).fetchall()
        return {r["path"] for r in rs}
    except Exception:
        return set()


def _active_job_state(runners: dict) -> tuple[set, set, bool]:
    """Return ``(page_urls, job_identities, measured)`` for live runners.

    The owner sidecar records ``job_identity(page_url)``, not the URL itself.
    Any runner/job-map/identity that cannot be read makes the population
    incomplete, so ``measured`` is false and the scanner must withhold a
    destructive verdict rather than mistake missing evidence for no live job.
    """
    active_urls = set()
    active_identities = set()
    if not isinstance(runners, dict):
        return active_urls, active_identities, False
    for sid, runner in runners.items():
        try:
            jobs = getattr(runner, "jobs")
            if jobs is None:
                return active_urls, active_identities, False
            urls = list(jobs.keys())
        except Exception:
            return active_urls, active_identities, False
        for url in urls:
            try:
                identity = _staging_claim.job_identity(url)
            except _staging_claim.StagingUnavailable:
                return active_urls, active_identities, False
            active_urls.add(url)
            active_identities.add(identity)
    return active_urls, active_identities, True


def _active_urls(runners: dict) -> set:
    """Page URLs currently present in runner job maps."""
    return _active_job_state(runners)[0]


def _read_meta_sidecar(part_path: Path) -> dict:
    """Read the `.part.meta` JSON sidecar that the runner writes to
    track resume state. Returns {} if missing/malformed."""
    meta_path = part_path.with_suffix(part_path.suffix + ".meta")
    if not meta_path.is_file():
        return {}
    try:
        import json
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_for_orphans(*, s_cfg: dict, runners: dict,
                    age_threshold_s: int = _ORPHAN_AGE_THRESHOLD_S) -> list:
    """Walk every site's download_dir looking for orphaned `.part`
    files. Returns a list of dicts:

        [{path, site_id, site_name, size_bytes, age_seconds,
          url (from .meta sidecar if available),
          downloaded_bytes (from sidecar),
          total_bytes (from sidecar),
          progress_pct (0-100, computed)}]

    Excludes:
      • Files with a surviving staging claim
      • Unclaimed files while any live job cannot be ruled out
      • Files younger than age_threshold_s
      • Files the operator has marked 'ignore'

    Sorted oldest-first (most likely to be truly stuck)."""
    ignored = _ignored_paths()
    active_urls, _active_identities, active_jobs_measured = (
        _active_job_state(runners))
    now = time.time()
    orphans = []

    for sid, cfg in (s_cfg or {}).items():
        dl_dir = (cfg or {}).get("download_dir", "")
        if not dl_dir:
            continue
        dl_path = Path(dl_dir)
        if not dl_path.is_dir():
            continue
        site_name = (cfg.get("name") or sid)

        try:
            # rglob is recursive — operators sometimes nest into
            # subdirs by template (e.g. /{site}/{date}/file.mp4.part)
            for part_path in dl_path.rglob("*.part"):
                try:
                    if not part_path.is_file():
                        continue
                    path_str = str(part_path)
                    if path_str in ignored:
                        continue
                    stat = part_path.stat()
                    age_s = now - stat.st_mtime
                    if age_s < age_threshold_s:
                        continue
                    if not active_jobs_measured:
                        # An incomplete runner population cannot establish
                        # that this partial has no live owner.
                        continue
                    owner_path = _staging_claim.owner_path_for(part_path)
                    try:
                        owner_stat = owner_path.lstat()
                    except FileNotFoundError:
                        claim_exists = False
                    except OSError:
                        # Claim presence itself is UNKNOWN.
                        continue
                    else:
                        claim_exists = True
                    if claim_exists:
                        if not _stat.S_ISREG(owner_stat.st_mode):
                            # A dangling symlink, directory, device, or other
                            # non-record at the owner name is PRESENT but cannot
                            # establish ownership. It is UNKNOWN, not absence.
                            continue
                        try:
                            _staging_claim._read_owner_identity(owner_path)
                        except _staging_claim.StagingUnavailable:
                            # A claim that cannot be measured is not absence.
                            continue
                        # A readable claim is still a claim even when its
                        # holder is absent from this process's runner snapshot.
                        # Only measured absence of BOTH a job and a claim is an
                        # abandoned part that may be offered for deletion.
                        continue
                    elif active_urls:
                        # A pre-claim partial cannot be joined to one of the
                        # live jobs. Its state is UNKNOWN, not abandoned.
                        continue
                    meta = _read_meta_sidecar(part_path)
                    url = (meta or {}).get("url") or ""
                    total = (meta or {}).get("total_bytes") or 0
                    downloaded = stat.st_size
                    pct = 0
                    if total > 0:
                        pct = min(100, round(downloaded * 100.0 / total, 1))
                    orphans.append({
                        "path": path_str,
                        "site_id": sid,
                        "site_name": site_name,
                        "size_bytes": stat.st_size,
                        "age_seconds": age_s,
                        "url": url,
                        "total_bytes": total,
                        "downloaded_bytes": downloaded,
                        "progress_pct": pct,
                    })
                except OSError:
                    continue
        except OSError:
            continue

    orphans.sort(key=lambda o: -o["age_seconds"])
    return orphans


def mark_decision(path: str, decision: str, *,
                  site_id: str = "") -> bool:
    """Record what the operator chose to do with this orphan. Used
    for 'ignore' (don't show again) and for audit on 'delete' /
    'resume' actions."""
    if decision not in ("ignore", "deleted", "resumed"):
        return False
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                INSERT OR REPLACE INTO crash_recovery_decisions
                (path, decision, ts, site_id)
                VALUES (?, ?, ?, ?)
            """, (path, decision, time.time(), site_id))
        return True
    except Exception:
        return False


def _acquire_delete_claim(part_path: Path) -> tuple[bool, str]:
    """Atomically reserve ``part_path`` against a worker claim.

    The claim publisher is the same ``O_EXCL``-equivalent operation used by
    downloads. Therefore exactly one side wins: a worker claim makes deletion
    refuse, while an operator claim makes a later worker choose another path.
    The stable operator identity also makes an interrupted delete retryable.
    """
    owner_path = _staging_claim.owner_path_for(part_path)
    try:
        minted = _staging_claim._create_owner(
            owner_path, _DELETE_CLAIM_IDENTITY)
        if minted:
            return True, ""
        holder = _staging_claim._read_owner_identity(owner_path)
    except _staging_claim.StagingUnavailable as exc:
        return False, (
            "staging claim is UNKNOWN; refusing delete: "
            f"{str(exc)[:160]}")
    if holder != _DELETE_CLAIM_IDENTITY:
        return False, "staging claim belongs to a download; refusing delete"
    return True, ""


def _finish_interrupted_delete(part_path: Path) -> Optional[dict]:
    """Finish a prior operator delete whose part is already absent.

    ``None`` means there is no operator claim, so ordinary idempotent-absence
    handling applies. A surviving operator identity is durable evidence that a
    previous call removed the part but did not finish its sidecar/claim cleanup.
    Foreign claims are observed but untouched.
    """
    owner_path = _staging_claim.owner_path_for(part_path)
    try:
        owner_stat = owner_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {"ok": False, "error": (
            "staging claim is UNKNOWN for absent part: "
            f"{str(exc)[:160]}")}
    if not _stat.S_ISREG(owner_stat.st_mode):
        return {"ok": False, "error": (
            "staging claim is UNKNOWN for absent part: owner entry is not "
            "a regular claim record")}
    try:
        holder = _staging_claim._read_owner_identity(owner_path)
    except _staging_claim.StagingUnavailable as exc:
        return {"ok": False, "error": (
            "staging claim is UNKNOWN for absent part: "
            f"{str(exc)[:160]}")}
    if holder != _DELETE_CLAIM_IDENTITY:
        return None
    meta_path = part_path.with_suffix(part_path.suffix + ".meta")
    try:
        meta_path.unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    if not _staging_claim.release(part_path, _DELETE_CLAIM_IDENTITY):
        return {
            "ok": False,
            "deleted_bytes": 0,
            "error": "absent part's operator staging claim was retained",
        }
    mark_decision(str(part_path), "deleted")
    return {
        "ok": True,
        "deleted_bytes": 0,
        "note": "completed interrupted delete of already absent part",
    }


def delete_orphan(path: str) -> dict:
    """Delete an unclaimed ``.part`` and its sidecars.

    Deletion first publishes its own staging claim, using the same atomic
    reservation operation as a worker. A worker that claimed after an earlier
    scan therefore wins and is left intact; if deletion wins, no worker can
    begin streaming into this path until the bytes are gone and the operator
    claim is released. Missing files remain idempotent.
    """
    p = Path(path)
    if not p.exists():
        interrupted = _finish_interrupted_delete(p)
        if interrupted is not None:
            return interrupted
        return {"ok": True, "deleted_bytes": 0,
                "note": "already absent"}
    claimed, error = _acquire_delete_claim(p)
    if not claimed:
        return {"ok": False, "error": error}
    try:
        deleted_bytes = p.stat().st_size if p.is_file() else 0
        p.unlink(missing_ok=True)
        # Sidecar
        meta_path = p.with_suffix(p.suffix + ".meta")
        meta_path.unlink(missing_ok=True)
        # The operator reservation is now ours and the part is gone, satisfying
        # release()'s two proofs (identity and absent bytes). A false return is
        # a partial failure, not permission to report/audit a completed delete.
        if not _staging_claim.release(p, _DELETE_CLAIM_IDENTITY):
            return {
                "ok": False,
                "deleted_bytes": deleted_bytes,
                "error": "part deleted but operator staging claim was retained",
            }
        mark_decision(path, "deleted")
        return {"ok": True, "deleted_bytes": deleted_bytes}
    except OSError as e:
        return {"ok": False, "error": str(e)[:200]}


def clear_decisions(*, decision: Optional[str] = None) -> int:
    """Wipe past decisions. If `decision` is given, only that kind."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            if decision:
                cur = cx.execute(
                    "DELETE FROM crash_recovery_decisions WHERE decision=?",
                    (decision,))
            else:
                cur = cx.execute("DELETE FROM crash_recovery_decisions")
            return cur.rowcount or 0
    except Exception:
        return 0


def stats(*, s_cfg: dict, runners: dict) -> dict:
    """Summary numbers for the Review-tab card."""
    orphans = scan_for_orphans(s_cfg=s_cfg, runners=runners)
    total_bytes = sum(o.get("size_bytes", 0) for o in orphans)
    by_site = {}
    for o in orphans:
        sid = o.get("site_id", "?")
        by_site.setdefault(sid, {"count": 0, "bytes": 0,
                                 "site_name": o.get("site_name", sid)})
        by_site[sid]["count"] += 1
        by_site[sid]["bytes"] += o.get("size_bytes", 0)
    return {
        "orphan_count": len(orphans),
        "total_bytes": total_bytes,
        "by_site": by_site,
    }
