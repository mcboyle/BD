"""Library finalization — NFO sidecar generation + polish helpers
(Phase 91, Block J).

After downloads accumulate, the operator wants a one-shot pass that:
  • Writes <video>.nfo sidecars (Plex/Jellyfin/Kodi-friendly) from
    available metadata sources (TPDB, MP4 atoms, filename inference)
  • Finds orphaned files (in download_dir but not in history) and
    inferred-missing files (in history with status='done' but file
    gone from disk)
  • Generates a cross-library report

Pure read on the library; only writes are NFO sidecars (idempotent —
re-running over the same files updates rather than duplicates).

NFO format: Kodi's <movie>...</movie> XML. Same shape Jellyfin and
Plex's "Plex Movie" agent both consume. Compact subset — title,
sorttitle, plot, year, runtime, studio, tags, actors. No images
referenced from NFO (cover art lives next to the file as
<video>-poster.jpg per Plex convention).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import xml.sax.saxutils as _x
from pathlib import Path
from typing import Optional


# ─── NFO generation ───────────────────────────────────────────────────

def build_nfo_xml(meta: dict) -> str:
    """Render a Kodi-style <movie> NFO from a metadata dict.

    Recognized keys (all optional):
      title, sorttitle, plot, year, runtime (minutes), studio,
      tags (list), performers (list), tpdb_id, date (YYYY-MM-DD),
      audio_fingerprint, quality_grade, source_url

    Returns the XML as a string with declaration. Idempotent: same
    input always produces same output (no timestamps embedded)."""
    if not isinstance(meta, dict):
        meta = {}
    root = ET.Element("movie")

    def add(tag: str, value):
        if value is None:
            return
        s = str(value).strip()
        if not s:
            return
        e = ET.SubElement(root, tag)
        e.text = s

    add("title", meta.get("title"))
    add("sorttitle", meta.get("sorttitle") or meta.get("title"))
    add("plot", meta.get("plot") or meta.get("description"))
    add("year", meta.get("year"))
    if not meta.get("year") and meta.get("date"):
        m = re.match(r"^(\d{4})", str(meta["date"]))
        if m:
            add("year", m.group(1))
    add("premiered", meta.get("date"))
    add("studio", meta.get("studio"))
    add("runtime", meta.get("runtime"))
    if meta.get("duration_seconds") and not meta.get("runtime"):
        add("runtime", int(int(meta["duration_seconds"]) / 60))
    add("source", meta.get("source_url"))
    # TPDB ID becomes a uniqueid block
    if meta.get("tpdb_id"):
        uid = ET.SubElement(root, "uniqueid", {"type": "tpdb", "default": "true"})
        uid.text = str(meta["tpdb_id"])
    # Tags
    for t in (meta.get("tags") or []):
        if t:
            add("tag", str(t))
    # Performers as actors
    for name in (meta.get("performers") or []):
        if not name:
            continue
        actor = ET.SubElement(root, "actor")
        n = ET.SubElement(actor, "name")
        n.text = str(name)
    # Quality grade as a custom field — Plex ignores; Jellyfin shows
    if meta.get("quality_grade") is not None:
        add("quality_grade", meta["quality_grade"])
    if meta.get("audio_fingerprint"):
        add("audio_fingerprint", meta["audio_fingerprint"])
    # Pretty-print (Python 3.9+ has ET.indent)
    try:
        ET.indent(root, space="  ", level=0)
    except AttributeError:
        pass  # 3.8 doesn't have indent
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode")


def write_nfo(video_path: str, meta: dict, *,
              overwrite: bool = True) -> Optional[str]:
    """Write the NFO sidecar next to `video_path`. Returns the NFO
    path on success, None on failure.

    Convention: <basename without extension>.nfo. If `overwrite=False`
    and the file exists, skip and return existing path."""
    if not video_path or not isinstance(video_path, str):
        return None
    p = Path(video_path)
    nfo_path = p.with_suffix(".nfo")
    if nfo_path.exists() and not overwrite:
        return str(nfo_path)
    xml = build_nfo_xml(meta or {})
    try:
        nfo_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to .nfo.tmp then rename
        tmp = nfo_path.with_suffix(".nfo.tmp")
        tmp.write_text(xml, encoding="utf-8")
        tmp.replace(nfo_path)
        return str(nfo_path)
    except OSError as e:
        import sys
        sys.stderr.write(f"[library_final] write_nfo {nfo_path}: {e}\n")
        return None


# ─── Library health audit ─────────────────────────────────────────────

_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv",
               ".webm", ".flv", ".ts", ".mpg", ".mpeg"}


def list_orphans(download_dir: str, *, site_id: Optional[str] = None) -> list:
    """Find video files in `download_dir` that don't have a matching
    history row. Useful for the operator hunting downloads from
    pre-BD or hand-saved files.

    Returns list of {path, size_bytes}."""
    d = Path(download_dir)
    if not d.is_dir():
        return []
    known: set = set()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            sql = "SELECT filename FROM history WHERE status='done' AND filename != ''"
            params: list = []
            if site_id:
                sql += " AND site_id = ?"
                params.append(site_id)
            for r in cx.execute(sql, params):
                fn = r[0] if not hasattr(r, "keys") else r["filename"]
                if fn:
                    known.add(os.path.normpath(str(fn)))
                    known.add(os.path.basename(str(fn)))
    except Exception:
        pass
    out = []
    for root, _dirs, files in os.walk(str(d)):
        for f in files:
            if Path(f).suffix.lower() not in _VIDEO_EXTS:
                continue
            full = os.path.join(root, f)
            if os.path.normpath(full) in known or f in known:
                continue
            try:
                sz = os.path.getsize(full)
            except OSError:
                sz = 0
            out.append({"path": full, "size_bytes": sz})
    return out


def list_missing_from_disk(*, site_id: Optional[str] = None,
                           limit: int = 500) -> list:
    """Find history rows where status='done' but the file is gone."""
    try:
        from . import db as _db
        sql = "SELECT id, site_id, filename, ts FROM history WHERE status='done' AND filename != ''"
        params: list = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _db.db_conn() as cx:
            rows = cx.execute(sql, params).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        fn = d.get("filename") or ""
        if fn and not Path(fn).exists():
            out.append(d)
    return out


def list_duplicate_candidates(download_dir: str) -> list:
    """Video files that share an exact byte size -- likely duplicate copies (e.g.
    the same file saved under two site dirs). Stat-only + ADVISORY: a size
    collision is a candidate, not a confirmed duplicate. Returns
    [{size_bytes, count, paths}] per colliding group, most-reclaimable first."""
    d = Path(download_dir)
    if not d.is_dir():
        return []
    by_size: dict = {}
    for root, _dirs, files in os.walk(str(d)):
        for f in files:
            if Path(f).suffix.lower() not in _VIDEO_EXTS:
                continue
            full = os.path.join(root, f)
            try:
                sz = os.path.getsize(full)
            except OSError:
                continue
            if sz <= 0:
                continue
            by_size.setdefault(sz, []).append(full)
    groups = [{"size_bytes": sz, "count": len(paths), "paths": sorted(paths)[:10]}
              for sz, paths in by_size.items() if len(paths) > 1]
    # largest reclaimable space first: (count-1) redundant copies * size
    groups.sort(key=lambda g: -(g["size_bytes"] * (g["count"] - 1)))
    return groups


def list_size_drift(download_dir: str, *, site_id: Optional[str] = None,
                    limit: int = 1000, tolerance_bytes: int = 0) -> list:
    """History rows (status=done) whose recorded ``file_size`` differs from the
    file's actual on-disk size -- a truncated or altered download. Returns
    [{filename, recorded_bytes, disk_bytes, delta_bytes}], most-truncated
    (largest negative delta) first. ``download_dir`` is accepted for signature
    symmetry with the other doctor checks; drift is keyed off the history rows."""
    try:
        from . import db as _db
        sql = ("SELECT filename, file_size FROM history "
               "WHERE status='done' AND filename != '' AND file_size > 0")
        params: list = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _db.db_conn() as cx:
            rows = cx.execute(sql, params).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        fn = d.get("filename") or ""
        recorded = int(d.get("file_size") or 0)
        if not fn or recorded <= 0:
            continue
        try:
            if not Path(fn).exists():
                continue
            disk = os.path.getsize(fn)
        except OSError:
            continue
        if abs(disk - recorded) > tolerance_bytes:
            out.append({"filename": fn, "recorded_bytes": recorded,
                        "disk_bytes": disk, "delta_bytes": disk - recorded})
    out.sort(key=lambda x: x["delta_bytes"])
    return out


def audit(*, download_dir: str, site_id: Optional[str] = None) -> dict:
    """One-call library-doctor summary for the dashboard.

    Returns COUNTS, not lists -- the lists are the capped sample_* fields.
    Exact keys (the SPA panel and api-types.ts LibraryAuditResult must agree
    with this set; tests/test_library_audit_panel_contract.py pins that):

      orphans                  int   files on disk with no history row
      missing                  int   history rows whose file is gone
                                     (the key is `missing`, NOT
                                     `missing_from_disk` -- that name was
                                     rendered by the SPA for releases and
                                     never existed here)
      duplicate_groups         int   size-collision groups (advisory)
      duplicate_reclaimable_gb float
      size_drift               int   recorded vs on-disk size mismatches
      orphan_size_gb           float
      sample_orphans / sample_missing / sample_duplicates /
      sample_size_drift        list  first 10 rows of each
    """
    o = list_orphans(download_dir, site_id=site_id)
    m = list_missing_from_disk(site_id=site_id)
    dupes = list_duplicate_candidates(download_dir)
    drift = list_size_drift(download_dir, site_id=site_id)
    total_orphan = sum(x["size_bytes"] for x in o)
    reclaimable = sum(g["size_bytes"] * (g["count"] - 1) for g in dupes)
    return {
        "orphans": len(o),
        "missing": len(m),
        "duplicate_groups": len(dupes),
        "duplicate_reclaimable_gb": round(reclaimable / (1024**3), 2),
        "size_drift": len(drift),
        "orphan_size_gb": round(total_orphan / (1024**3), 2),
        "sample_orphans": o[:10],
        "sample_missing": m[:10],
        "sample_duplicates": dupes[:10],
        "sample_size_drift": drift[:10],
    }


# ─── Batch NFO regen ──────────────────────────────────────────────────

def regen_nfos_from_history(
    *,
    site_id: Optional[str] = None,
    overwrite: bool = False,
    max_files: int = 1000,
    dry_run: bool = False,
) -> dict:
    """Generate (or update) NFO sidecars for every existing file in
    history. Uses whatever metadata is recorded — TPDB if it was
    enriched, otherwise just filename + message.

    Returns {written: N, skipped: N, missing_files: N, errors: N}."""
    out = {"written": 0, "skipped": 0, "missing_files": 0, "errors": 0}
    try:
        from . import db as _db
        sql = "SELECT * FROM history WHERE status='done' AND filename != ''"
        params: list = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(max_files))
        with _db.db_conn() as cx:
            rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return out
    for r in rows:
        fn = r.get("filename") or ""
        if not fn or not Path(fn).exists():
            out["missing_files"] += 1
            continue
        nfo_path = Path(fn).with_suffix(".nfo")
        if nfo_path.exists() and not overwrite:
            out["skipped"] += 1
            continue
        meta = {
            "title": Path(fn).stem,
            "sorttitle": Path(fn).stem,
            "plot": r.get("message", ""),
            "studio": r.get("site_name", ""),
            "source_url": r.get("url", ""),
        }
        # v3.66.522 (VR-P06): honor dry_run (the handler defaults it True =
        # preview). Count the row as a would-write but never touch disk.
        if dry_run:
            out["written"] += 1
            continue
        if write_nfo(fn, meta, overwrite=overwrite):
            out["written"] += 1
        else:
            out["errors"] += 1
    return out
