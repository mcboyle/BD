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


def _basename_index(download_dir: str) -> dict:
    """{basename: [paths]} for every file under download_dir.

    Built once per check rather than per row: a library is a few thousand
    files and a per-row rglob would make the doctor quadratic.
    """
    idx: dict = {}
    if not download_dir:
        return idx
    try:
        root = Path(download_dir)
        if not root.is_dir():
            return idx
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    idx.setdefault(p.name, []).append(p)
            except OSError:
                continue
    except OSError:
        return idx
    return idx


def _resolve_recorded(fn: str, download_dir: str, index: dict):
    """(path, state) for a `history.filename` value.

    state is one of:
      "resolved"  -- exactly one real file; `path` is it
      "absent"    -- we know where it should be and it is not there
      "ambiguous" -- the basename matches SEVERAL files; which row owns which
                     cannot be decided, so neither missing nor drift may be
                     claimed
      "unknown"   -- no download_dir to resolve against; nothing can be said

    WHY THIS EXISTS. runner_transport.py:989 records `final_path.name` -- a
    bare BASENAME, not a path. Feeding that to `Path(fn)` resolves it against
    the process CWD, so every production row missed. The two callers then
    failed in opposite directions off that one root cause: missing reported
    every row, drift reported none.

    A flat `download_dir / fn` is not sufficient on its own: the recorded
    basename has already lost any subdirectory the filename template created,
    so the index fallback is what finds nested files.

    "ambiguous" and "unknown" are deliberately NOT folded into "absent".
    Guessing first-match-wins would let a size comparison run against the wrong
    file and report a drift that is an artefact of the guess.
    """
    if not fn:
        return None, "unknown"
    p = Path(fn)
    if p.is_absolute():
        return (p, "resolved") if p.exists() else (p, "absent")
    if not download_dir:
        return None, "unknown"
    direct = Path(download_dir) / fn
    if direct.exists():
        return direct, "resolved"
    hits = index.get(p.name) or []
    if len(hits) == 1:
        return hits[0], "resolved"
    if len(hits) > 1:
        return None, "ambiguous"
    return direct, "absent"


def list_missing_from_disk(*, site_id: Optional[str] = None,
                           limit: int = 500,
                           download_dir: str = "") -> list:
    """Find history rows where status='done' but the file is gone.

    `download_dir` is what the recorded basename is resolved against; without
    it nothing can be decided and no row is reported (see _resolve_recorded).
    It is keyword-only and defaults to "" so existing callers keep working --
    they get the old can't-resolve behaviour, but now it reports NOTHING
    rather than reporting EVERYTHING.
    """
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
    index = _basename_index(download_dir)
    out = []
    for r in rows:
        d = dict(r)
        fn = d.get("filename") or ""
        _path, state = _resolve_recorded(fn, download_dir, index)
        # Only a row we can actually place and find absent is missing.
        # "ambiguous" and "unknown" are not evidence of absence.
        if state == "absent":
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


def size_drift_scan(download_dir: str, *, site_id: Optional[str] = None,
                    limit: int = 1000, tolerance_bytes: int = 0,
                    collect_ids: bool = False) -> dict:
    """``list_size_drift``'s loop, reporting what it EXAMINED as well as what it found.

    The drift rows alone cannot answer "was this population actually checked".
    A row whose recorded basename does not resolve to exactly one on-disk file
    is skipped below, and so is one whose resolved path cannot be stat'ed --
    both silently. A caller counting DB rows therefore reports coverage over a
    denominator that excludes precisely the rows it failed to look at, and a
    sweep that resolved NOTHING renders identically to one that swept clean.

    Returned keys:
      rows          -- the drift rows, exactly as list_size_drift returns them
      considered    -- rows the query returned (post-LIMIT)
      examined      -- rows actually compared against a file on disk
      states        -- histogram of _resolve_recorded states for the skipped
      stat_failed   -- resolved but os.path.getsize raised
      limit_hit     -- considered == limit, so `considered` is a floor
      query_failed  -- the DB read raised; every other number is meaningless
      examined_ids  -- ids compared, when collect_ids (the sweep needs a UNION;
                       summing per-directory counts double-counts a row that
                       resolves under two directories)
      state_ids     -- {state: set(ids)} for the SKIPPED rows, when collect_ids.
                       `states` alone cannot be unioned across directories: a
                       row absent under dir A and compared under dir B is
                       COMPARED, but both histograms count it, so the reason
                       line sums to rows x directories instead of to rows.
                       "stat failed" is a state here so the two agree.

    The accounting lives in the SAME loop as the comparison on purpose. Counted
    in a second pass it would be a second copy of the resolution logic, free to
    drift from this one -- and then the coverage figure and the drift figure
    would be about different passes while claiming to be about one.
    """
    states: dict = {}
    state_ids: dict = {}
    examined_ids: set = set()
    result = {"rows": [], "considered": 0, "examined": 0, "states": states,
              "stat_failed": 0, "limit_hit": False, "query_failed": False,
              "examined_ids": examined_ids, "state_ids": state_ids}
    try:
        from . import db as _db
        sql = ("SELECT id, filename, file_size FROM history "
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
        result["query_failed"] = True
        return result
    result["considered"] = len(rows)
    result["limit_hit"] = len(rows) >= int(limit)
    index = _basename_index(download_dir)
    out = []
    for r in rows:
        d = dict(r)
        fn = d.get("filename") or ""
        recorded = int(d.get("file_size") or 0)
        _rid = d.get("id") if collect_ids else None
        if not fn or recorded <= 0:
            states["empty"] = states.get("empty", 0) + 1
            if _rid is not None:
                state_ids.setdefault("empty", set()).add(_rid)
            continue
        path, state = _resolve_recorded(fn, download_dir, index)
        # Compare only what we resolved to exactly one real file. An absent
        # file is list_missing_from_disk's subject, not drift; an ambiguous
        # basename would be compared against a guess.
        if state != "resolved" or path is None:
            states[state] = states.get(state, 0) + 1
            if _rid is not None:
                state_ids.setdefault(state, set()).add(_rid)
            continue
        try:
            disk = os.path.getsize(path)
        except OSError:
            result["stat_failed"] += 1
            if _rid is not None:
                # same label census's _merge_states uses, so the count-based
                # and id-based histograms carry identical keys
                state_ids.setdefault("stat failed", set()).add(_rid)
            continue
        result["examined"] += 1
        if _rid is not None:
            examined_ids.add(_rid)
        if abs(disk - recorded) > tolerance_bytes:
            _row = {"filename": fn, "recorded_bytes": recorded,
                    "disk_bytes": disk, "delta_bytes": disk - recorded}
            if collect_ids:
                # only under collect_ids, so list_size_drift's row shape --
                # which the audit panel renders -- stays byte-identical
                _row["id"] = d.get("id")
            out.append(_row)
    out.sort(key=lambda x: x["delta_bytes"])
    result["rows"] = out
    return result


def list_size_drift(download_dir: str, *, site_id: Optional[str] = None,
                    limit: int = 1000, tolerance_bytes: int = 0) -> list:
    """History rows (status=done) whose recorded ``file_size`` differs from the
    file's actual on-disk size -- a truncated or altered download. Returns
    [{filename, recorded_bytes, disk_bytes, delta_bytes}], most-truncated
    (largest negative delta) first. ``download_dir`` is what the recorded
    basename is RESOLVED against -- runner_transport.py:989 records
    ``final_path.name``, so without it every production row failed to resolve
    and this check reported 0 while a truncated file sat on disk.

    A projection of ``size_drift_scan``. Callers that need to know how much of
    the population was actually compared want that function instead -- this
    return value cannot distinguish "no drift" from "nothing resolved"."""
    return size_drift_scan(download_dir, site_id=site_id, limit=limit,
                           tolerance_bytes=tolerance_bytes)["rows"]


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
    m = list_missing_from_disk(site_id=site_id, download_dir=download_dir)
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
