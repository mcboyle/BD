"""dev_suite.housekeeping -- maintenance / config-write / feature-toggle

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re

from ._common import (
    _repo_root)



# ── 44. temp-dir cleaner + lock-file scanner (U25: D-114 + D-118) ──
#
# D-114 (A) + D-118 (R) — a structural pair over one orphan-resource
# walk of the system temp dir. D-118 reports; D-114 removes.
#
# SAFETY — both are scoped to BD's OWN artifacts only, by prefix:
#   • temp dirs:  bdback_  bdback_preview_  bdrestore_   (mkdtemp)
#   • vpn dirs:   bd_vpn_ovpn  bd_vpn_wg                 (vpn config)
# They never touch a path outside tempfile.gettempdir(), never match a
# path by anything but these known BD prefixes, and the cleaner only
# removes entries OLDER than a minimum age (default 1h) so a temp dir
# an in-flight backup/restore is using is never swept. D-114 also has
# a dry_run mode (the default) — it reports what it WOULD remove.

# BD's own temp-artifact directory prefixes (see app.py backup/restore
# and vpn_openvpn / vpn_wireguard CONF_DIR).
_BD_TEMP_PREFIXES = ("bdback_", "bdback_preview_", "bdrestore_")

_BD_VPN_DIRNAMES = ("bd_vpn_ovpn", "bd_vpn_wg")

_TEMPDIR_MIN_AGE_S = 3600.0   # never sweep anything younger than 1h



def _scan_bd_temp_artifacts():
    """Walk the system temp dir and return BD's own temp dirs +
    .lock files. Returns (temp_dirs, lock_files, tmp_root, error)."""
    import os as _os
    import tempfile as _tf
    import time as _t
    try:
        tmp_root = _tf.gettempdir()
    except Exception as e:
        return [], [], None, f"cannot resolve temp dir: {e}"
    temp_dirs, lock_files = [], []
    now = _t.time()
    try:
        entries = _os.listdir(tmp_root)
    except Exception as e:
        return [], [], tmp_root, f"cannot list temp dir: {e}"
    for name in entries:
        full = _os.path.join(tmp_root, name)
        try:
            is_dir = _os.path.isdir(full)
            mtime = _os.path.getmtime(full)
        except OSError:
            continue
        age = round(now - mtime, 1)
        if is_dir and (name.startswith(_BD_TEMP_PREFIXES)
                       or name in _BD_VPN_DIRNAMES):
            kind = ("vpn_config" if name in _BD_VPN_DIRNAMES
                    else "backup_restore")
            temp_dirs.append({"name": name, "path": full,
                              "age_seconds": age, "kind": kind,
                              "sweepable": age >= _TEMPDIR_MIN_AGE_S})
        elif not is_dir and name.endswith(".lock"):
            # NOT a BD artifact. Measured v3.66.844: nothing in the tree
            # writes a *.lock file. storage_tier's exclusive placeholder is
            # dest_path ITSELF (storage_tier.py:209-211), never .lock-suffixed,
            # and it is removed inside the same call. This branch is a generic
            # system-temp lock reporter, so any hit belongs to some OTHER
            # program -- and tempdir_clean below would DELETE it. That
            # over-scope is filed in SESSION_CARRY 15.9 and NOT fixed here.
            lock_files.append({"name": name, "path": full,
                               "age_seconds": age})
    temp_dirs.sort(key=lambda d: d["age_seconds"], reverse=True)
    lock_files.sort(key=lambda f: f["age_seconds"], reverse=True)
    return temp_dirs, lock_files, tmp_root, None



def lockfile_scan():
    """D-118 (R) — report BD's lingering temp dirs and .lock files in
    the system temp dir. Read-only — lists, removes nothing. A .lock
    file or an old bdback_/bdrestore_ dir usually means an aborted
    backup/restore/move."""
    temp_dirs, locks, tmp_root, err = _scan_bd_temp_artifacts()
    if err:
        return {"tool": "lockfile_scan", "ok": False, "error": err}
    stale = [d for d in temp_dirs if d["sweepable"]]
    return {
        "tool": "lockfile_scan",
        "ok": True,
        "temp_root": tmp_root,
        "bd_temp_dirs": temp_dirs,
        "lock_files": locks,
        "stale_temp_dir_count": len(stale),
        "lock_file_count": len(locks),
        "verdict": ("no lingering BD temp artifacts"
                    if not stale and not locks
                    else f"{len(stale)} stale temp dir(s), "
                         f"{len(locks)} lock file(s) — "
                         f"likely aborted backup/restore/move"),
    }



def tempdir_clean(dry_run=True, min_age_seconds=None):
    """D-114 (A) — remove BD's OWN stale temp dirs and lingering .lock
    files from the system temp dir. Scoped strictly to bdback_/
    bdback_preview_/bdrestore_/bd_vpn_* dirs and *.lock files; never
    touches anything else. Only removes entries older than
    min_age_seconds (default 1h) so an in-flight backup is safe.

    dry_run=True (the default) reports what it WOULD remove and
    deletes nothing — pass dry_run=False to actually sweep."""
    import os as _os
    import shutil as _sh
    dry_run = bool(dry_run) if not isinstance(dry_run, str) \
        else dry_run.lower() not in ("false", "0", "no", "")
    if min_age_seconds is None:
        min_age = _TEMPDIR_MIN_AGE_S
    else:
        try:
            min_age = max(0.0, float(min_age_seconds))
        except (TypeError, ValueError):
            min_age = _TEMPDIR_MIN_AGE_S
    temp_dirs, locks, tmp_root, err = _scan_bd_temp_artifacts()
    if err:
        return {"tool": "tempdir_clean", "ok": False, "error": err}
    targets = [d for d in temp_dirs if d["age_seconds"] >= min_age]
    target_locks = [f for f in locks if f["age_seconds"] >= min_age]
    removed, failed = [], []
    if not dry_run:
        for d in targets:
            try:
                _sh.rmtree(d["path"])
                removed.append({"path": d["path"], "kind": "dir"})
            except Exception as e:
                failed.append({"path": d["path"],
                               "error": str(e)[:160]})
        for f in target_locks:
            try:
                _os.remove(f["path"])
                removed.append({"path": f["path"], "kind": "lock"})
            except Exception as e:
                failed.append({"path": f["path"],
                               "error": str(e)[:160]})
    would = [{"path": d["path"], "kind": "dir"} for d in targets] + \
            [{"path": f["path"], "kind": "lock"} for f in target_locks]
    return {
        "tool": "tempdir_clean",
        "ok": True,
        "dry_run": dry_run,
        "temp_root": tmp_root,
        "min_age_seconds": min_age,
        "candidate_count": len(would),
        "would_remove" if dry_run else "removed":
            would if dry_run else removed,
        "failed": failed,
        "verdict": (
            (f"dry run — {len(would)} artifact(s) would be removed"
             if would else "dry run — nothing to remove")
            if dry_run else
            (f"removed {len(removed)} artifact(s), "
             f"{len(failed)} failed" if removed or failed
             else "nothing to remove")),
    }



def cache_clear(targets=None):
    """D-123 (A) — drop BD's in-process caches so the next request
    rebuilds them from fresh state. Targets (default: all):
      • index_html  — the legacy /legacy shell's HTML cache (no-op since
        the shell was deleted in P4, but kept as a valid target so the
        cockpit cache-clear API surface and its tests are unchanged)
      • disk_cache  — api_status's 5s disk-free TTL cache
      • ffmpeg      — hls_downloader's ffmpeg-path probe cache
    None of these hold authoritative state — they are all rebuildable
    on demand — so clearing them is safe; worst case is one slightly
    slower request while they refill."""
    all_targets = ("index_html", "disk_cache", "ffmpeg")
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]
    if not targets:
        targets = list(all_targets)
    unknown = [t for t in targets if t not in all_targets]
    if unknown:
        return {"tool": "cache_clear", "ok": False,
                "error": f"unknown cache target(s): {unknown}; "
                         f"valid: {list(all_targets)}"}
    cleared, failed = [], []
    if "index_html" in targets:
        try:
            from bulk_downloader import app as _app
            # P4 (v3.66.334): the legacy shell was deleted, so
            # _INDEX_HTML_CACHE no longer exists on the module. Assigning
            # it is a harmless no-op (re-creates a vestigial attr that
            # nothing reads); the target stays valid so the cockpit
            # cache-clear API contract is unchanged.
            _app._INDEX_HTML_CACHE = None
            cleared.append("index_html")
        except Exception as e:
            failed.append({"target": "index_html",
                            "error": str(e)[:160]})
    if "disk_cache" in targets:
        try:
            from bulk_downloader import app as _app
            # the cache is an attribute on the api_status function;
            # resetting it to {} forces a rebuild on next poll.
            # P4 (v3.66.436): api_status was extracted from app.py onto
            # the app_status blueprint, so it is no longer an attribute of
            # the app module — resolve it from its new home, falling back
            # to the app module for older trees.
            _h = getattr(_app, "api_status", None)
            if _h is None:
                try:
                    from bulk_downloader import app_status as _as
                    _h = getattr(_as, "api_status", None)
                except Exception:
                    _h = None
            if _h is not None:
                _h._disk_cache = {}
            cleared.append("disk_cache")
        except Exception as e:
            failed.append({"target": "disk_cache",
                            "error": str(e)[:160]})
    if "ffmpeg" in targets:
        try:
            from bulk_downloader import hls_downloader as _hls
            _hls._FFMPEG_CACHE = None
            cleared.append("ffmpeg")
        except Exception as e:
            failed.append({"target": "ffmpeg",
                            "error": str(e)[:160]})
    return {
        "tool": "cache_clear",
        "ok": True,
        "requested": targets,
        "cleared": cleared,
        "failed": failed,
        "verdict": (f"cleared {len(cleared)} cache(s)"
                    + (f", {len(failed)} failed" if failed else "")),
    }



# ── 67. disk-usage breakdown + download-folder scanner (T17) ───────
#
# D-112 + D-113 share one disk walk. D-112 reports bytes per
# extension and per age-bucket so the operator can see WHAT is
# eating space; D-113 reports anomalies the operator should clean up
# (zero-byte files, .part orphans without a .meta sidecar, very
# small files that probably failed). Path-allowlist enforced. Cap
# on files walked so a huge dir doesn't time out a dev request.

def disk_usage_breakdown(site_configs=None, max_files=20000):
    """D-112 — per-site, per-extension, and per-age-bucket bytes
    across configured download_dirs (read-only).
    """
    return _filesystem_audit(
        site_configs=site_configs,
        max_files=max_files,
        mode="breakdown",
    )



def download_folder_scan(site_configs=None, max_files=20000):
    """D-113 — scan configured download_dirs for anomalies:
    zero-byte files, very small (<10KB) files that probably failed,
    .part files without a .meta sidecar, and duplicate filenames
    across sites (read-only).
    """
    return _filesystem_audit(
        site_configs=site_configs,
        max_files=max_files,
        mode="scan",
    )



def _filesystem_audit(site_configs=None, max_files=20000,
                      mode="breakdown"):
    """Shared walk for D-112 + D-113. Walks each configured
    download_dir once, classifies every file, and returns whichever
    view the caller asked for. mode='breakdown' returns usage stats;
    mode='scan' returns anomaly findings. Both honour the
    path_allowlist.
    """
    import time as _time
    try:
        max_files = max(1, min(int(max_files), 200000))
    except Exception:
        max_files = 20000
    out = {"tool": "disk_usage_breakdown" if mode == "breakdown"
                   else "download_folder_scan",
           "ok": True, "mode": mode,
           "dirs_scanned": [], "dirs_skipped": []}

    # Collect unique configured download_dirs
    dirs = []
    seen = set()
    for sid, cfg in (site_configs or {}).items():
        dd = ((cfg or {}).get("download_dir") or "").strip()
        if dd and dd not in seen:
            seen.add(dd)
            dirs.append((sid, dd))
    if not dirs:
        out["verdict"] = "no configured download_dirs to scan"
        if mode == "breakdown":
            out["total_files"] = 0
            out["total_bytes"] = 0
            out["per_extension"] = []
            out["per_age_bucket"] = []
            out["per_site"] = []
        else:
            out["zero_byte_files"] = []
            out["very_small_files"] = []
            out["orphaned_partials"] = []
            out["duplicate_filenames"] = []
        return out

    try:
        from bulk_downloader import app as _app
        validator = getattr(_app, "_validate_path", None)
    except Exception:
        validator = None

    # Walk shared state
    now = _time.time()
    AGE_BUCKETS = [
        ("0_1d",   0,         86400),
        ("1_7d",   86400,     7*86400),
        ("7_30d",  7*86400,   30*86400),
        ("30_90d", 30*86400,  90*86400),
        ("90d_plus", 90*86400, None),
    ]
    per_ext: dict = {}            # ext -> (count, bytes)
    per_age: dict = {b[0]: [0, 0] for b in AGE_BUCKETS}
    per_site: dict = {}           # sid -> (count, bytes)
    name_to_sites: dict = {}      # filename -> set(sid)
    zero_bytes = []
    very_small = []
    orphaned_parts = []
    total_files = 0
    total_bytes = 0
    truncated = False

    from pathlib import Path as _Path
    for sid, dd in dirs:
        if validator is not None:
            ok, msg = validator(dd, f"site {sid} download_dir")
            if not ok:
                out["dirs_skipped"].append({"site_id": sid,
                                              "dir": dd,
                                              "reason": msg[:140]})
                continue
        d = _Path(dd)
        if not d.exists() or not d.is_dir():
            out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                          "reason": ("not a directory"
                                                     if d.exists() else
                                                     "does not exist")})
            continue
        out["dirs_scanned"].append({"site_id": sid, "dir": str(d)})
        per_site.setdefault(sid, [0, 0])
        try:
            for p in d.rglob("*"):
                if total_files >= max_files:
                    truncated = True
                    break
                if not p.is_file():
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                size = st.st_size
                name = p.name
                ext = (p.suffix or "").lower() or "<noext>"
                # Per-extension
                if ext not in per_ext:
                    per_ext[ext] = [0, 0]
                per_ext[ext][0] += 1
                per_ext[ext][1] += size
                # Per-age bucket
                age = now - st.st_mtime
                for label, lo, hi in AGE_BUCKETS:
                    if age >= lo and (hi is None or age < hi):
                        per_age[label][0] += 1
                        per_age[label][1] += size
                        break
                # Per-site
                per_site[sid][0] += 1
                per_site[sid][1] += size
                # Duplicate-name tracking (D-113)
                if mode == "scan":
                    name_to_sites.setdefault(name, set()).add(sid)
                    if size == 0:
                        zero_bytes.append({"site_id": sid,
                                            "path": str(p),
                                            "mtime": st.st_mtime})
                    elif 0 < size < 10 * 1024:
                        very_small.append({"site_id": sid,
                                            "path": str(p),
                                            "size_bytes": size,
                                            "mtime": st.st_mtime})
                    # orphaned partial = .part file with no .meta
                    # sidecar. (a .part WITH .meta is resumable —
                    # surfaced separately by D-38 / partials_finder.)
                    if name.endswith(".part"):
                        meta = p.with_suffix(p.suffix + ".meta")
                        if not meta.exists():
                            orphaned_parts.append({
                                "site_id": sid,
                                "path": str(p),
                                "size_bytes": size,
                                "mtime": st.st_mtime,
                            })
                total_files += 1
                total_bytes += size
        except OSError as e:
            out["dirs_skipped"].append({"site_id": sid, "dir": dd,
                                          "reason": ("walk failed: "
                                                     + str(e)[:120])})
            continue
        if truncated:
            break

    out["truncated"] = truncated
    out["total_files"] = total_files
    out["total_bytes"] = total_bytes

    if mode == "breakdown":
        # Sorted descending by bytes
        out["per_extension"] = [
            {"ext": e, "count": v[0], "bytes": v[1]}
            for e, v in sorted(per_ext.items(),
                                 key=lambda kv: -kv[1][1])
        ]
        out["per_age_bucket"] = [
            {"bucket": b, "count": per_age[b][0],
             "bytes": per_age[b][1]}
            for b in (x[0] for x in AGE_BUCKETS)
        ]
        out["per_site"] = [
            {"site_id": s, "count": v[0], "bytes": v[1]}
            for s, v in sorted(per_site.items(),
                                 key=lambda kv: -kv[1][1])
        ]
        gb = total_bytes / (1024**3)
        out["verdict"] = (
            f"{total_files} file(s), {gb:.2f} GB across "
            f"{len(out['dirs_scanned'])} dir(s)"
            + (f"; truncated at {max_files}" if truncated else ""))
    else:
        # mode == "scan"
        dup_names = [
            {"filename": n, "site_ids": sorted(s)}
            for n, s in name_to_sites.items() if len(s) > 1
        ]
        out["zero_byte_files"] = zero_bytes
        out["very_small_files"] = very_small
        out["orphaned_partials"] = orphaned_parts
        out["duplicate_filenames"] = dup_names
        anomalies = (len(zero_bytes) + len(very_small)
                     + len(orphaned_parts) + len(dup_names))
        out["anomaly_count"] = anomalies
        out["verdict"] = (
            f"scanned {total_files} file(s); "
            f"{anomalies} anomal{'y' if anomalies == 1 else 'ies'}"
            f" ({len(zero_bytes)} zero, "
            f"{len(very_small)} tiny, "
            f"{len(orphaned_parts)} orphan-part, "
            f"{len(dup_names)} dup-name)")
    return out



# ── T34 / D-107 — dead-CSS finder ──────────────────────────────────

def _extract_css_selectors(css_text: str) -> dict:
    """Pull class + id selectors out of one CSS file. Returns
    {"classes": set, "ids": set}. Best-effort: strips comments, splits
    on `{`, takes the selector head, walks each selector list."""
    import re as _re
    # Strip /* ... */ comments
    cleaned = _re.sub(r"/\*.*?\*/", " ", css_text, flags=_re.DOTALL)
    classes: set = set()
    ids: set = set()
    # Each rule block looks like "sel1, sel2, sel3 { ... }".
    # We only want the selector head before `{`.
    for block_head in _re.split(r"\{[^{}]*\}", cleaned):
        # `block_head` is everything between the previous `}` and the
        # next `{` — selector list for the upcoming rule, plus stray
        # at-rules. Trim @-rules; we don't track @keyframes names etc.
        head = block_head.split("}")[-1].strip()
        if not head or head.startswith("@"):
            continue
        for sel in head.split(","):
            sel = sel.strip()
            if not sel:
                continue
            # Pull class tokens: `.foo` `.foo-bar` `.foo_bar`
            for m in _re.finditer(r"\.([a-zA-Z_][\w-]*)", sel):
                classes.add(m.group(1))
            # Pull id tokens: `#foo`
            for m in _re.finditer(r"#([a-zA-Z_][\w-]*)", sel):
                ids.add(m.group(1))
    return {"classes": classes, "ids": ids}



def dead_css_finder(repo_root=None):
    """T34 / D-107 — scan shipped CSS files for selectors not
    referenced anywhere in the templates or JS. Read-only.

    Returns {tool, ok, files_scanned, total_classes, total_ids,
    unused_classes[], unused_ids[], ambiguous[], verdict}.

    "ambiguous" = a class whose stem appears in a JS string
    concatenation context — we can't know whether the runtime
    constructs the full name, so we report rather than flag as dead.

    Accepts an optional repo_root= for synthetic-tree tests.
    """
    import re as _re
    root = Path(repo_root) if repo_root else _repo_root()
    static_dir = root / "bulk_downloader" / "static"
    templates_dir = root / "bulk_downloader" / "templates"
    out = {
        "tool": "dead_css_finder",
        "ok": True,
        "files_scanned": [],
        "total_classes": 0,
        "total_ids": 0,
        "unused_classes": [],
        "unused_ids": [],
        "ambiguous": [],
        "verdict": "",
    }
    if not static_dir.is_dir():
        out["ok"] = False
        out["verdict"] = f"static dir not found: {static_dir}"
        return out
    # Collect selectors from every .css under static/
    classes: set = set()
    ids: set = set()
    css_files = sorted(static_dir.glob("*.css"))
    for css_path in css_files:
        try:
            txt = css_path.read_text(encoding="utf-8")
        except Exception:
            continue
        sel = _extract_css_selectors(txt)
        classes |= sel["classes"]
        ids |= sel["ids"]
        out["files_scanned"].append(css_path.name)
    out["total_classes"] = len(classes)
    out["total_ids"] = len(ids)
    # Read the search corpus: templates + every shipped JS file (NOT
    # the CSS files themselves — a class only used inside CSS is dead).
    corpus_paths = []
    if templates_dir.is_dir():
        corpus_paths.extend(sorted(templates_dir.glob("*.html")))
    corpus_paths.extend(sorted(static_dir.glob("*.js")))
    corpus_parts = []
    for p in corpus_paths:
        try:
            corpus_parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    corpus = "\n".join(corpus_parts)
    # For each selector, look for any literal token match. We avoid
    # over-tight word boundaries — a class can appear in `classList.add('foo')`,
    # `class="foo bar"`, ` foo ` etc. The `\b` boundary works because
    # class tokens use [\w-] and `\b` recognises `-` as a word boundary
    # (so `.foo-bar` is one boundary block).
    # ambiguous-detection regex: hyphenated stems built via template
    # literals like `${prefix}-active`.
    for c in sorted(classes):
        # Plain token match — covers attribute values, classList calls,
        # JS string literals. Hyphens are NOT word chars, so `\b` works
        # as a token boundary even for hyphenated classes.
        if _re.search(r"\b" + _re.escape(c) + r"\b", corpus):
            continue
        # Ambiguous: the token has a hyphen and the prefix or suffix
        # alone shows up in a template-literal context. E.g. class
        # `scope-tab-active` may be built from `'scope-tab-' + state`.
        ambiguous = False
        if "-" in c:
            parts = c.split("-")
            # Check whether prefix-N or suffix-N parts appear in a
            # template-literal context (backtick + ${).
            for n in range(1, len(parts)):
                pref = "-".join(parts[:n]) + "-"
                suf = "-" + "-".join(parts[n:])
                if (_re.search(r"`[^`]*" + _re.escape(pref)
                                + r"\$\{", corpus) or
                    _re.search(r"\$\}" + _re.escape(suf), corpus) or
                    _re.search(r"['\"]\s*" + _re.escape(pref)
                                + r"['\"]?\s*\+", corpus)):
                    ambiguous = True
                    break
        if ambiguous:
            out["ambiguous"].append(c)
        else:
            out["unused_classes"].append(c)
    for i in sorted(ids):
        if _re.search(r"\b" + _re.escape(i) + r"\b", corpus):
            continue
        out["unused_ids"].append(i)
    out["unused_classes"].sort()
    out["unused_ids"].sort()
    out["ambiguous"].sort()
    out["verdict"] = (
        f"scanned {len(out['files_scanned'])} CSS file(s); "
        f"{out['total_classes']} class selector(s), "
        f"{out['total_ids']} id selector(s); "
        f"{len(out['unused_classes'])} unused class(es), "
        f"{len(out['unused_ids'])} unused id(s), "
        f"{len(out['ambiguous'])} ambiguous (dynamic).")
    return out



# ── T35 / D-117 — storage-tier inspector ───────────────────────────

def storage_tier_status(site_configs=None):
    """T35 / D-117 — surface the StorageTierScheduler state plus a
    per-site dry-run of find_candidates (eligible-for-migration
    counts). Read-only — calls find_candidates which is a SELECT.

    Returns {tool, ok, scheduler{}, per_site[], total_candidates,
    verdict}.
    """
    out = {
        "tool": "storage_tier_status",
        "ok": True,
        "scheduler": {},
        "per_site": [],
        "total_candidates": 0,
        "verdict": "",
    }
    try:
        from bulk_downloader import storage_tier as _st
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"storage_tier import failed: {e}"
        return out
    # Scheduler snapshot — never call .start(), only read the state
    try:
        sched = _st.get_scheduler()
        out["scheduler"] = sched.get_status()
    except Exception as e:
        out["scheduler"] = {"error": str(e)[:200]}
    # Per-site candidate counts. If site_configs is None, we can't
    # enumerate; the caller (app.py route) passes s_cfg.
    if not isinstance(site_configs, dict):
        site_configs = {}
    total = 0
    for sid, cfg in sorted(site_configs.items()):
        if not isinstance(cfg, dict):
            continue
        # Pull tier params with defensive defaults; if unset the site
        # is reported as not-configured rather than crashing
        tier_cfg = cfg.get("storage_tier") or {}
        if not isinstance(tier_cfg, dict) or not tier_cfg.get("enabled"):
            out["per_site"].append({
                "site_id": sid,
                "configured": False,
                "candidate_count": 0,
            })
            continue
        try:
            age = int(tier_cfg.get("age_days", 30))
            min_mb = int(tier_cfg.get("min_size_mb", 0))
        except (TypeError, ValueError):
            age = 30
            min_mb = 0
        try:
            cands = _st.find_candidates(sid, age_days=age,
                                         min_size_mb=min_mb,
                                         limit=10000)
            n = len(cands)
        except Exception:
            n = 0
        total += n
        out["per_site"].append({
            "site_id": sid,
            "configured": True,
            "age_days": age,
            "min_size_mb": min_mb,
            "candidate_count": n,
        })
    out["total_candidates"] = total
    sites_on = sum(1 for s in out["per_site"] if s["configured"])
    out["verdict"] = (
        f"{sites_on}/{len(out['per_site'])} site(s) have tier "
        f"enabled; {total} candidate file(s) across all sites; "
        f"scheduler "
        f"{'running' if out['scheduler'].get('running') else 'idle'}.")
    return out



# ── T36 / D-122 — maintenance-mode status (read-side) ──────────────

def maintenance_mode_status():
    """T36 / D-122 — surface current maintenance-windows state plus
    which actions are currently paused. Read-only.

    The mutating side (immediate on/off override) is in
    bulk_downloader.maintenance via add_window_now() and
    end_active_overrides(); it is reached via separate POST routes
    that are CSRF-gated, NOT through this inspector.

    Returns {tool, ok, active_windows[], all_windows[],
    paused_actions[], verdict}.
    """
    out = {
        "tool": "maintenance_mode_status",
        "ok": True,
        "active_windows": [],
        "all_windows": [],
        "paused_actions": [],
        "verdict": "",
    }
    try:
        from bulk_downloader import maintenance as _mw
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"maintenance import failed: {e}"
        return out
    try:
        out["active_windows"] = _mw.active_now()
    except Exception as e:
        out["active_windows"] = []
        out["verdict"] = f"active_now failed: {e}"
        return out
    try:
        out["all_windows"] = _mw.list_windows(include_past=False)
    except Exception:
        out["all_windows"] = []
    # Which actions are currently paused (across any active window)?
    actions = ("workers", "discovery", "exports", "webhooks")
    paused = []
    for a in actions:
        try:
            if _mw.is_action_paused(a):
                paused.append(a)
        except Exception:
            continue
    out["paused_actions"] = paused
    if out["active_windows"]:
        labels = [w.get("label", "") or f"window#{w.get('id')}"
                  for w in out["active_windows"]]
        out["verdict"] = (
            f"{len(out['active_windows'])} active window(s): "
            f"{', '.join(labels)}; "
            f"paused: {', '.join(paused) or '(none)'}")
    else:
        out["verdict"] = (
            f"no active windows; "
            f"{len(out['all_windows'])} window(s) configured")
    return out



# ── T38 / D-108 — i18n string-coverage ─────────────────────────────

def i18n_coverage(repo_root=None):
    """T38 / D-108 — extract translatable strings from templates +
    shipped JS, diff against each shipped locale. Read-only.

    Reports per locale: how many catalog entries cover the extracted
    strings, how many strings have no translation, and how many catalog
    entries are present but stale (no longer referenced in source).

    Accepts repo_root= override for synthetic-tree tests.
    """
    root = Path(repo_root) if repo_root else _repo_root()
    templates_dir = root / "bulk_downloader" / "templates"
    static_dir = root / "bulk_downloader" / "static"
    locales_dir = root / "bulk_downloader" / "locales"
    out = {
        "tool": "i18n_coverage",
        "ok": True,
        "sources_scanned": [],
        "extracted_strings": 0,
        "locales": [],
        "verdict": "",
    }
    try:
        from bulk_downloader import i18n as _i18n
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"i18n module import failed: {e}"
        return out
    # Build the source list: every template + every JS file. The i18n
    # extract_strings was written for HTML but its regex strips
    # <script>/<style>/comments and then splits on tags — JS files
    # have no tags so it returns the whole file as one chunk; for our
    # purposes we want to skim JS the same way, but to avoid noisy
    # output we use a JS-specific scan: pick out user-visible string
    # literals (not selectors, not keys).
    source_paths = []
    if templates_dir.is_dir():
        source_paths.extend(sorted(templates_dir.glob("*.html")))
    # JS scanning is best-effort and noisy. We skip it for now and
    # only count templates, mirroring i18n.dump_template's behavior.
    if not source_paths:
        out["ok"] = False
        out["verdict"] = "no template sources found"
        return out
    out["sources_scanned"] = [p.name for p in source_paths]
    catalog = _i18n.extract_from_files([str(p) for p in source_paths])
    extracted = set(catalog.keys())
    out["extracted_strings"] = len(extracted)
    if not locales_dir.is_dir():
        out["verdict"] = (
            f"no locales dir; {len(extracted)} string(s) extracted "
            f"from {len(source_paths)} template(s)")
        return out
    locale_files = sorted(locales_dir.glob("*.json"))
    for lf in locale_files:
        try:
            import json as _json
            data = _json.loads(lf.read_text(encoding="utf-8"))
        except Exception as e:
            out["locales"].append({
                "file": lf.name,
                "ok": False,
                "error": str(e)[:200],
            })
            continue
        if not isinstance(data, dict):
            out["locales"].append({
                "file": lf.name,
                "ok": False,
                "error": "locale file is not a JSON object",
            })
            continue
        # Distinguish meta keys ("_meta") from translation entries
        translation_keys = {
            k for k in data.keys() if not k.startswith("_")
        }
        translation_values = {
            v for k, v in data.items()
            if not k.startswith("_") and isinstance(v, str)
        }
        # Strings extracted from source that DON'T appear as VALUES
        # in the locale are uncovered. (Operators write source in
        # English; en.json maps id->English; other locales map id->
        # translation. Coverage is "does the catalog contain this
        # English literal as a value somewhere?")
        uncovered = sorted(extracted - translation_values)
        # Catalog entries that don't correspond to any source string
        # are stale — translator-written but no longer in source.
        stale_values = sorted(translation_values - extracted)
        out["locales"].append({
            "file": lf.name,
            "ok": True,
            "translation_entries": len(translation_keys),
            "uncovered_strings": uncovered[:50],
            "uncovered_count": len(uncovered),
            "stale_entries": stale_values[:50],
            "stale_count": len(stale_values),
            "coverage_pct": (
                round(100.0 * (len(extracted) - len(uncovered))
                       / max(1, len(extracted)), 1)),
        })
    summary = ", ".join(
        f"{loc['file']}={loc.get('coverage_pct', 0)}%"
        for loc in out["locales"] if loc.get("ok"))
    out["verdict"] = (
        f"{len(extracted)} string(s) from {len(source_paths)} "
        f"template(s); locales: {summary or '(none)'}")
    return out



# ── T40 / D-121 — feature-flag console ─────────────────────────────
#
# Tiny JSON-state subsystem: bulk_downloader.feature_flags.
# The toggle is mutating, lives in its own module like maintenance.
# Read-side inspector wraps that module's read API.

def feature_flags_status():
    """T40 / D-121 — read-only view of all currently-defined feature
    flags + their values. Mutating routes (POST /api/dev/feature_flag_set,
    POST /api/dev/feature_flag_delete) live separately and are
    CSRF-gated.
    """
    out = {
        "tool": "feature_flags_status",
        "ok": True,
        "flags": {},
        "state_file": "",
        "verdict": "",
    }
    try:
        from bulk_downloader import feature_flags as _ff
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"feature_flags import failed: {e}"
        return out
    try:
        out["flags"] = _ff.list_flags()
        out["state_file"] = str(_ff.state_path())
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"list_flags failed: {e}"
        return out
    on_n = sum(1 for v in out["flags"].values() if v)
    out["verdict"] = (
        f"{len(out['flags'])} flag(s) defined, {on_n} enabled")
    return out
