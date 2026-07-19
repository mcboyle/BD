"""Per-site retention policies (Phase 173, Block Q).

Lets operators set per-site rules for auto-deleting old downloads
to keep library size bounded. Two policy axes:

  • retention_days: delete files older than N days
  • retention_max_gb: delete oldest files until library size fits
  • retention_keep_tagged_with: list of tags whose rows are NEVER deleted
    (operator-marked "must-keep" content)

Schema additions to history (lazy migration):
  • retention_excluded INTEGER DEFAULT 0  — operator-set "do not delete"

A separate retention_audit table records every deletion:
  (id, history_id, site_id, file_path, deleted_at, reason)

Public API:
  • find_candidates(s_cfg_entry, *, dry_run=True) → list of candidate rows
  • apply_retention(s_cfg, *, dry_run=True) → per-site result dict
  • mark_excluded(history_id, excluded) → bool
  • audit_log(*, limit=100) → recent deletions

The bg_scheduler.retention_sweep task invokes apply_retention nightly
when at least one site has retention configured.

CRITICAL: deletes real files. Default behavior is dry_run=True so the
operator can review candidates before flipping the switch.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import db as _db


_tables_ready = False


def _ensure_tables():
    """Idempotent schema."""
    global _tables_ready
    if _tables_ready:
        return
    try:
        with _db.db_conn() as cx:
            # Lazy-add retention_excluded column
            cols = [r[1] for r in cx.execute(
                "PRAGMA table_info(history)").fetchall()]
            if "retention_excluded" not in cols:
                cx.execute("ALTER TABLE history "
                           "ADD COLUMN retention_excluded INTEGER DEFAULT 0")
            cx.execute("""CREATE TABLE IF NOT EXISTS retention_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                history_id INTEGER,
                site_id TEXT,
                file_path TEXT,
                file_size_bytes INTEGER,
                deleted_at REAL NOT NULL,
                reason TEXT,
                dry_run INTEGER DEFAULT 0
            )""")
            cx.execute("""CREATE INDEX IF NOT EXISTS idx_retention_audit_ts
                ON retention_audit(deleted_at)""")
        _tables_ready = True
    except Exception:
        pass


def mark_excluded(history_id: int, excluded: bool = True) -> bool:
    """Toggle 'do not delete' flag on a history row."""
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            cx.execute("UPDATE history SET retention_excluded = ? "
                       "WHERE id = ?",
                       (1 if excluded else 0, int(history_id)))
        return True
    except Exception:
        return False


def _tagged_with_any(history_id: int, tag_list: list) -> bool:
    """True if the row has any of the protect-tags."""
    if not tag_list:
        return False
    try:
        from . import tags as _tags
        row_tags = set(_tags.tags_for(history_id))
        return any(t in row_tags for t in tag_list)
    except Exception:
        return False


def find_candidates(site_id: str, s_cfg_entry: dict) -> list:
    """Return rows that this site's retention policy would delete.
    Each entry is {id, site_id, filename, file_size, ts, reason}."""
    _ensure_tables()

    retention_days = int(s_cfg_entry.get("retention_days", 0) or 0)
    retention_max_gb = float(s_cfg_entry.get("retention_max_gb", 0) or 0)
    keep_tags = s_cfg_entry.get("retention_keep_tagged_with") or []
    if isinstance(keep_tags, str):
        keep_tags = [t.strip() for t in keep_tags.split(",") if t.strip()]

    if retention_days <= 0 and retention_max_gb <= 0:
        return []

    candidates = []
    now = time.time()
    cutoff_seconds = now - retention_days * 86400 if retention_days > 0 else 0

    try:
        with _db.db_conn() as cx:
            rows = cx.execute("""
                SELECT id, site_id, filename, file_size,
                       CAST(strftime('%s', ts) AS REAL) as ts_sec,
                       COALESCE(retention_excluded, 0) as excluded
                FROM history
                WHERE site_id = ? AND status = 'done'
                  AND filename IS NOT NULL AND filename != ''
                ORDER BY ts_sec DESC
            """, (site_id,)).fetchall()
    except Exception:
        return []

    # Age-based pass
    if retention_days > 0:
        for r in rows:
            hid, sid, fn, sz, ts_sec, excluded = r
            if excluded:
                continue
            if _tagged_with_any(hid, keep_tags):
                continue
            if ts_sec and ts_sec < cutoff_seconds:
                candidates.append({
                    "id": hid, "site_id": sid, "filename": fn,
                    "file_size": sz or 0, "ts_sec": ts_sec,
                    "reason": f"older than {retention_days}d",
                })

    # Size-based pass — sum non-excluded sizes newest-first, then mark
    # candidates everything past the cap (oldest first)
    if retention_max_gb > 0:
        cap_bytes = retention_max_gb * 1024 * 1024 * 1024
        running = 0
        already_picked = {c["id"] for c in candidates}
        # Walk newest-first; once running > cap, the rest are candidates
        for r in rows:
            hid, sid, fn, sz, ts_sec, excluded = r
            if excluded:
                continue
            if _tagged_with_any(hid, keep_tags):
                continue
            running += sz or 0
            if running > cap_bytes and hid not in already_picked:
                candidates.append({
                    "id": hid, "site_id": sid, "filename": fn,
                    "file_size": sz or 0, "ts_sec": ts_sec,
                    "reason": f"over {retention_max_gb}GB cap",
                })

    # Sort: oldest first (so audit log reads naturally)
    candidates.sort(key=lambda c: c.get("ts_sec") or 0)
    return candidates


def _record_audit(hid: int, site_id: str, file_path: str,
                  file_size: int, reason: str, dry_run: bool):
    try:
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO retention_audit
                (history_id, site_id, file_path, file_size_bytes,
                 deleted_at, reason, dry_run)
                VALUES (?,?,?,?,?,?,?)""",
                (hid, site_id, file_path, file_size,
                 time.time(), reason, 1 if dry_run else 0))
    except Exception:
        pass


def apply_retention(s_cfg: Optional[dict] = None, *,
                   dry_run: bool = True,
                   confirm_ids: Optional[list] = None,
                   site_id: Optional[str] = None) -> dict:
    """Walk every site's retention policy and (optionally) delete
    files. Returns {sites: {sid: {candidates, deleted, errors, dry_run}}}.

    Preview-verbatim binding (v3.66.227, F4.2): when ``confirm_ids`` is
    provided, deletion is restricted to the INTERSECTION of the currently
    computed candidates and ``confirm_ids`` (the history ids the operator
    saw in a preceding ``find_candidates`` / preview). This is the safety
    guarantee for the destructive SPA path: apply can delete FEWER than the
    preview disclosed (e.g. a file that has since been excluded/tagged or
    is already gone is silently skipped) but can NEVER delete a file that
    was not in the previewed set. ``confirm_ids=None`` preserves the legacy
    unbound all-sites sweep. ``site_id`` optionally scopes the run to one
    site (the per-site SPA flow); ``None`` walks every site.
    """
    _ensure_tables()
    s_cfg = s_cfg or {}
    confirm_set = None if confirm_ids is None else {int(i) for i in confirm_ids
                                                    if str(i).strip() != ""}
    out = {"dry_run": dry_run, "sites": {}, "ts": time.time(),
           "total_candidates": 0, "total_deleted": 0,
           "total_bytes_freed": 0,
           "preview_bound": confirm_set is not None,
           "scoped_site": site_id}

    for site_id_k, cfg in s_cfg.items():
        if not cfg:
            continue
        if site_id is not None and site_id_k != site_id:
            continue
        candidates = find_candidates(site_id_k, cfg)
        # Preview-verbatim: keep only candidates the operator confirmed.
        # The intersection is what makes "never delete more than previewed"
        # hold even if find_candidates now returns a different/larger set.
        if confirm_set is not None:
            candidates = [c for c in candidates if c.get("id") in confirm_set]
        site_result = {
            "candidates": len(candidates),
            "deleted": 0,
            "errors": [],
            "bytes_freed": 0,
        }
        out["total_candidates"] += len(candidates)

        for c in candidates:
            file_path = c.get("filename") or ""
            if not file_path:
                continue
            if dry_run:
                _record_audit(c["id"], site_id_k, file_path,
                              c.get("file_size", 0),
                              c["reason"], dry_run=True)
                continue
            # Actually delete
            try:
                p = Path(file_path)
                if p.is_file():
                    sz = p.stat().st_size
                    p.unlink()
                    site_result["deleted"] += 1
                    site_result["bytes_freed"] += sz
                    out["total_deleted"] += 1
                    out["total_bytes_freed"] += sz
                    _record_audit(c["id"], site_id_k, file_path,
                                  sz, c["reason"], dry_run=False)
                # else: file already gone, treat as success but record
            except Exception as e:
                site_result["errors"].append(
                    f"{file_path}: {str(e)[:80]}")

        out["sites"][site_id_k] = site_result
    return out


def audit_log(*, limit: int = 100,
              dry_run_only: Optional[bool] = None) -> list:
    """Recent retention deletions (or dry-run candidates)."""
    _ensure_tables()
    try:
        with _db.db_conn() as cx:
            if dry_run_only is None:
                rs = cx.execute("""SELECT * FROM retention_audit
                    ORDER BY deleted_at DESC LIMIT ?""",
                    (int(limit),)).fetchall()
            else:
                rs = cx.execute("""SELECT * FROM retention_audit
                    WHERE dry_run = ?
                    ORDER BY deleted_at DESC LIMIT ?""",
                    (1 if dry_run_only else 0, int(limit))).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []


# ── Phase 1 Cut 1.4 (v3.66.614): capture-retention engine ────────────────
# A SECOND retention axis, over the db `captures` index (Phase 1 Cut 1.1),
# distinct from the per-site download/history retention above. Governance
# (RETENTION_AND_TAKEDOWN_POLICY.md): captures are KEEP-FOREVER by default;
# capture retention is strictly OPT-IN. Policy axes:
#   * capture_ttl_days        : candidate captures older than N days
#   * capture_max_gb          : candidate oldest captures until the store fits
#   * capture_keep_n_per_host : keep the N newest captures per host; older = candidate
# Safety mirrors apply_retention: apply defaults to dry_run=True; a real apply
# with confirm_paths deletes only the INTERSECTION of current candidates and the
# previewed set (can delete fewer, never more/other). The §3 takedown floor
# (minors / illegal content) is a separate, non-negotiable path — NOT gated by
# any of these preference knobs — and is intentionally not implemented as an
# auto-sweep here (it is operator/▲external-signal-driven, out of this engine's scope).

def _capture_policy_active(policy) -> bool:
    """True iff the operator has configured any capture-retention rule. When
    False, find_capture_candidates returns [] (the keep-forever default)."""
    p = policy or {}
    return bool(
        (p.get("capture_ttl_days") or 0) > 0
        or (p.get("capture_max_gb") or 0) > 0
        or (p.get("capture_keep_n_per_host") or 0) > 0
    )


def find_capture_candidates(policy, *, now=None):
    """Return the capture index rows that the given policy would delete — a
    value-free preview. Returns [] when no rule is configured (keep-forever). Each
    returned row is a plain dict (the db_captures_all shape) with an added
    ``reason`` string. A capture is a candidate if ANY active rule selects it.
    """
    if not _capture_policy_active(policy):
        return []
    import time as _t
    now = float(now if now is not None else _t.time())
    rows = _db_all_captures()
    p = policy or {}
    ttl_days = int(p.get("capture_ttl_days") or 0)
    keep_n = int(p.get("capture_keep_n_per_host") or 0)
    max_gb = float(p.get("capture_max_gb") or 0)

    reasons = {}  # rel_path -> reason (first rule that selected it)

    # TTL: older than the cutoff.
    if ttl_days > 0:
        cutoff = now - ttl_days * 86400
        for r in rows:
            if (r.get("captured_at") or 0) < cutoff:
                reasons.setdefault(r["rel_path"], f"older than {ttl_days}d")

    # keep-N-per-host: keep the N newest per host; the older overflow is candidate.
    if keep_n > 0:
        by_host = {}
        for r in rows:
            by_host.setdefault(r.get("host") or "", []).append(r)
        for host, hrows in by_host.items():
            hrows.sort(key=lambda x: x.get("captured_at") or 0, reverse=True)
            for r in hrows[keep_n:]:
                reasons.setdefault(r["rel_path"], f"beyond keep-{keep_n} for host")

    # size-cap: delete oldest until total <= max_gb. Applies to whatever remains
    # after the above (a capture already a candidate needn't be double-counted).
    if max_gb > 0:
        cap_bytes = max_gb * (1024 ** 3)
        ordered = sorted(rows, key=lambda x: x.get("captured_at") or 0, reverse=True)
        running = 0
        for r in ordered:
            running += int(r.get("size") or 0)
            if running > cap_bytes:
                reasons.setdefault(r["rel_path"], f"over {max_gb}GB store cap")

    by_rel = {r["rel_path"]: r for r in rows}
    out = []
    for rel, reason in reasons.items():
        row = dict(by_rel[rel])
        row["reason"] = reason
        out.append(row)
    out.sort(key=lambda x: x.get("captured_at") or 0)  # oldest first (deletion order)
    return out


def apply_capture_retention(policy, *, dry_run=True, confirm_paths=None, now=None):
    """(Optionally) delete the captures the policy selects. Returns
    ``{candidates, deleted, errors, dry_run}``.

    Safety (mirrors apply_retention):
      * dry_run=True (default) computes + returns the candidate count but deletes
        nothing (no file, no index row).
      * confirm_paths, when provided, restricts deletion to the INTERSECTION of the
        current candidates and confirm_paths — the preview-verbatim binding. A
        capture not in the previewed set is never deleted, even if it is a current
        candidate.
    Deleting a capture removes both the on-disk file and its `captures` index row.
    """
    cands = find_capture_candidates(policy, now=now)
    result = {"candidates": len(cands), "deleted": 0, "errors": [],
              "dry_run": bool(dry_run)}
    if dry_run:
        return result
    confirm = set(confirm_paths) if confirm_paths is not None else None
    to_delete = [c for c in cands
                 if confirm is None or c["rel_path"] in confirm]
    if not to_delete:
        return result
    from .dom_analyzer import resolve_capture_token as _resolve
    deleted_rels = []
    for c in to_delete:
        rel = c["rel_path"]
        try:
            # Resolve via the FS-authoritative gate (symlink/is_file/is_under) so a
            # stale index row can never point deletion at an out-of-tree file.
            fp = _resolve(rel)
            if fp is not None:
                _os_remove(str(fp))
            deleted_rels.append(rel)
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"{rel}: {str(e)[:120]}")
    # drop the deleted rows from the index (prune-by-keep: keep everything except
    # the ones we deleted).
    if deleted_rels:
        all_rels = {r["rel_path"] for r in _db_all_captures()}
        keep = all_rels - set(deleted_rels)
        _db_prune_captures(keep)
    result["deleted"] = len(deleted_rels)
    return result


# thin indirections so tests + callers share one db entry point (and to keep the
# db import local to this module's existing `_db` alias).
def _db_all_captures():
    return _db.db_captures_all()


def _db_prune_captures(keep_rel_paths):
    return _db.db_captures_prune_missing(keep_rel_paths)


def _os_remove(path):
    os.remove(path)
