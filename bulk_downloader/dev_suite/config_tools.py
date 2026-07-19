"""dev_suite.config_tools -- configuration inspection / snapshot / schema audit

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
    _BD_ENV_VARS, _SECRET_KEY_HINTS, _collect_cred_refs, _dev_mode, _redact, _resolve_all_site_configs)



def effective_settings() -> dict:
    """Resolved values of the env flags and on-disk markers that
    actually change BulkDownloader's behaviour."""
    return {
        "env": {k: os.environ.get(k) for k in _BD_ENV_VARS},
        "debug_flag_present": Path("debug.flag").exists(),
        "dev_mode": _dev_mode(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cwd": os.getcwd(),
    }



def config_dump(app_cfg=None, site_cfg=None) -> dict:
    """The live app config and per-site config, with any password /
    token / cookie / credential value masked. Caller passes the
    app._app_cfg and app.s_cfg objects."""
    return {
        "app_config": _redact(app_cfg or {}),
        "sites": _redact(site_cfg or {}),
        "site_count": len(site_cfg or {}),
    }



def config_integrity(site_cfg=None) -> dict:
    """Validate the per-site config: empty configs, URLs shared across
    sites, and an inventory of @cred: references. Caller passes
    app.s_cfg."""
    site_cfg = site_cfg or {}
    issues = []
    urls: dict = {}
    cred_refs: list = []
    empty: list = []
    for sid, cfg in site_cfg.items():
        if not cfg:
            empty.append(sid)
            continue
        refs: list = []
        _collect_cred_refs(cfg, refs)
        cred_refs.extend(refs)
        if isinstance(cfg, dict):
            for k in ("url", "base_url", "site_url", "home_url"):
                if cfg.get(k):
                    urls.setdefault(cfg[k], []).append(sid)
                    break
    dup_urls = {u: s for u, s in urls.items() if len(s) > 1}
    if empty:
        issues.append(f"{len(empty)} site(s) with empty config: {empty}")
    if dup_urls:
        issues.append(f"{len(dup_urls)} URL(s) used by multiple sites")
    return {
        "site_count": len(site_cfg),
        "empty_configs": empty,
        "duplicate_urls": dup_urls,
        "cred_reference_count": len(cred_refs),
        "cred_references": sorted(set(cred_refs)),
        "issues": issues,
        "verdict": "config looks consistent" if not issues
                   else f"{len(issues)} issue(s) found",
    }



def _scalar_type_mismatches(cfg):
    """Fields holding a list/dict where site_editor's schema expects a
    scalar — the structural-corruption case, checked fleet-wide and
    across every typed field (validate_config only checks 8)."""
    try:
        from bulk_downloader import site_editor as _se
        field_types = getattr(_se, "_FIELD_TYPES", {})
    except Exception:
        field_types = {}
    bad = []
    for field, spec in field_types.items():
        val = cfg.get(field)
        if isinstance(val, (list, dict)):
            jtype = spec[0] if isinstance(spec, tuple) else "scalar"
            bad.append(f"'{field}': expected {jtype}, got "
                       f"{type(val).__name__}")
    return bad



def config_schema_audit(runners=None, site_configs=None):
    """D-86 — validate every site config against the site_editor
    schema in one fleet-wide pass.

    For each site: site_editor.validate_config (errors + warnings) plus
    a list/dict-in-scalar-field check. Aggregates how many sites are
    clean / have errors / have warnings. Read-only.
    """
    try:
        from bulk_downloader import site_editor as _se
    except Exception as e:
        return {"tool": "config_schema_audit", "ok": False,
                "error": f"site_editor unavailable: {e}"}
    if site_configs is not None:
        configs, source = dict(site_configs), "caller-supplied"
    else:
        configs, source = _resolve_all_site_configs(runners)
    sites = []
    n_err = n_warn = n_type = 0
    for sid in sorted(configs):
        cfg = configs[sid] or {}
        try:
            v = _se.validate_config(cfg)
        except Exception as e:
            sites.append({"site_id": sid, "ok": False,
                          "errors": [f"validate_config raised: {e}"],
                          "warnings": [], "type_mismatches": []})
            n_err += 1
            continue
        type_bad = _scalar_type_mismatches(cfg)
        errs = list(v.get("errors") or [])
        warns = list(v.get("warnings") or [])
        ok = not errs and not type_bad
        if errs:
            n_err += 1
        if warns:
            n_warn += 1
        if type_bad:
            n_type += 1
        sites.append({"site_id": sid, "ok": ok, "errors": errs,
                      "warnings": warns, "type_mismatches": type_bad})
    return {
        "tool": "config_schema_audit",
        "ok": True,
        "config_source": source,
        "sites_total": len(sites),
        "sites_clean": sum(1 for s in sites if s["ok"]
                           and not s["warnings"]),
        "sites_with_errors": n_err,
        "sites_with_warnings": n_warn,
        "sites_with_type_mismatches": n_type,
        "sites": sites,
    }



# ── 47. config hot-reload + cache-clear (U28: D-119 + D-123) ───────
#
# D-119 + D-123 — a maintenance (A) pair. Both POST + CSRF + dev-gate.
#   • config_hot_reload (D-119) — re-run app._load_app_config(), which
#     re-reads app_config.json from disk and re-applies it (concurrency
#     cap, AI config, log level). Saves a process restart after an
#     out-of-band config edit.
#   • cache_clear (D-123) — drop BD's in-process caches (rendered index
#     HTML, disk-free TTL cache, ffmpeg-path probe) so the next request
#     rebuilds them from fresh state.
#
# CAVEAT (D-119): _load_app_config() does _app_cfg.update(file_data) —
# a MERGE. A key REMOVED from app_config.json on disk is not removed
# from the live config by a hot reload; only added/changed keys take
# effect. A full reset still needs a process restart. The tool states
# this in its result so the caller is not misled.

def config_hot_reload():
    """D-119 (A) — re-read app_config.json from disk and re-apply it
    without restarting the process (concurrency cap, AI config, log
    level are all re-applied by _load_app_config).

    NOTE: this is a merge-reload — keys added or changed on disk take
    effect; a key DELETED from the file is not removed from the live
    config. A full reset needs a process restart."""
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "config_hot_reload", "ok": False,
                "error": f"app module unavailable: {e}"}
    if not hasattr(_app, "_load_app_config"):
        return {"tool": "config_hot_reload", "ok": False,
                "error": "_load_app_config not found in app"}
    # snapshot a few visible keys before/after so the caller can see
    # what the reload changed
    watch = ("global_max_concurrent", "log_level", "ai_enabled",
             "ai_provider")
    before = {k: _app._app_cfg.get(k) for k in watch}
    try:
        _app._load_app_config()
    except Exception as e:
        return {"tool": "config_hot_reload", "ok": False,
                "error": f"reload failed: {type(e).__name__}: {e}"}
    after = {k: _app._app_cfg.get(k) for k in watch}
    changed = {k: {"from": before[k], "to": after[k]}
               for k in watch if before[k] != after[k]}
    return {
        "tool": "config_hot_reload",
        "ok": True,
        "reloaded": True,
        "merge_reload": True,
        "changed_keys": changed,
        "live_key_count": len(_app._app_cfg),
        "verdict": (f"config reloaded — {len(changed)} watched key(s) "
                    f"changed" if changed
                    else "config reloaded — no watched key changed "
                         "(file may be unchanged, or only non-watched "
                         "keys differ)"),
        "note": ("merge reload: deleted keys are NOT removed from the "
                 "live config — a full reset needs a process restart"),
    }



# ── 48. config snapshot/restore (U29: D-90) ────────────────────────
#
# D-90 (A) — snapshot the live app_config.json and restore a named
# snapshot. POST + CSRF + dev-gate. Useful before a risky config
# change: snapshot, experiment, restore if it goes wrong.
#
# SAFETY:
#  • Snapshots live in config_snapshots/ next to app_config.json.
#  • A restore routes the config back through app._save_app_config()
#    (the atomic .tmp + replace writer — DANGER_MAP: never write a
#    long-lived JSON state file non-atomically) and then
#    app._load_app_config() so the live process picks it up. It does
#    NOT hand-write app_config.json.
#  • Restore is a MERGE (same _app_cfg.update semantics as a hot
#    reload) — a key absent from the snapshot is not removed from the
#    live config. The result says so.
#  • Snapshot names are slug-validated; a restore can only name a file
#    that already exists inside config_snapshots/ — no path traversal.


_SNAPSHOT_DIRNAME = "config_snapshots"

_SNAPSHOT_NAME_RE = _cfg_re.compile(r"^[A-Za-z0-9._-]{1,64}$")



def _snapshot_dir():
    """config_snapshots/ next to app_config.json (cwd-relative, like
    the app's other state). Created on demand."""
    import os as _os
    d = _os.path.join(_os.getcwd(), _SNAPSHOT_DIRNAME)
    _os.makedirs(d, exist_ok=True)
    return d



def config_snapshot(name=None):
    """D-90 (A) — write the current live app config to a named
    snapshot file under config_snapshots/. With no name, a timestamp
    is used. The snapshot is a plain JSON copy of the live config."""
    import os as _os
    import time as _t
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "config_snapshot", "ok": False,
                "error": f"app module unavailable: {e}"}
    if name:
        name = str(name).strip()
        if not _SNAPSHOT_NAME_RE.match(name):
            return {"tool": "config_snapshot", "ok": False,
                    "error": ("name must be 1-64 chars of "
                              "letters/digits/.-_")}
    else:
        name = "snap_" + _t.strftime("%Y%m%d_%H%M%S")
    fname = name if name.endswith(".json") else name + ".json"
    dest = _os.path.join(_snapshot_dir(), fname)
    try:
        # atomic write — .tmp sibling then replace, same contract as
        # _save_app_config
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _cfg_json.dump(dict(_app._app_cfg), fh, indent=2,
                           ensure_ascii=False)
        _os.replace(tmp, dest)
    except Exception as e:
        return {"tool": "config_snapshot", "ok": False,
                "error": f"snapshot write failed: {e}"}
    return {
        "tool": "config_snapshot",
        "ok": True,
        "snapshot": fname,
        "path": _os.path.join(_SNAPSHOT_DIRNAME, fname),
        "key_count": len(_app._app_cfg),
        "verdict": f"config snapshot saved as '{fname}'",
    }



def config_snapshot_list():
    """List available config snapshots (read-only). A helper for the
    restore tool — names here are what restore accepts."""
    import os as _os
    d = _snapshot_dir()
    out = []
    try:
        for fn in sorted(_os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            full = _os.path.join(d, fn)
            try:
                out.append({"snapshot": fn,
                            "size_bytes": _os.path.getsize(full),
                            "modified_epoch": round(
                                _os.path.getmtime(full), 1)})
            except OSError:
                continue
    except Exception as e:
        return {"tool": "config_snapshot_list", "ok": False,
                "error": str(e)[:160]}
    return {"tool": "config_snapshot_list", "ok": True,
            "snapshot_count": len(out), "snapshots": out}



def config_restore(name=None):
    """D-90 (A) — restore a named config snapshot. The snapshot is
    validated as JSON, then applied via app._save_app_config()
    (atomic) + app._load_app_config() (so the live process picks it
    up). Merge semantics: keys absent from the snapshot are NOT
    removed from the live config — a full reset needs a restart."""
    import os as _os
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "config_restore", "ok": False,
                "error": f"app module unavailable: {e}"}
    if not name or not str(name).strip():
        return {"tool": "config_restore", "ok": False,
                "error": "a snapshot name is required"}
    name = str(name).strip()
    fname = name if name.endswith(".json") else name + ".json"
    # the base-name guard plus the membership check below makes path
    # traversal impossible — a restore can only name a file that
    # already exists inside config_snapshots/
    if _os.path.basename(fname) != fname \
            or not _SNAPSHOT_NAME_RE.match(fname[:-5]):
        return {"tool": "config_restore", "ok": False,
                "error": "invalid snapshot name"}
    src = _os.path.join(_snapshot_dir(), fname)
    if not _os.path.exists(src):
        return {"tool": "config_restore", "ok": False,
                "error": f"no snapshot named '{fname}' — "
                         f"use config_snapshot_list to see available"}
    try:
        with open(src, encoding="utf-8") as fh:
            data = _cfg_json.load(fh)
    except Exception as e:
        return {"tool": "config_restore", "ok": False,
                "error": f"snapshot is not valid JSON: {e}"}
    if not isinstance(data, dict):
        return {"tool": "config_restore", "ok": False,
                "error": "snapshot is not a JSON object"}
    try:
        # apply to the live config, then persist atomically via the
        # app's own writer, then reload so every dependent subsystem
        # re-applies (concurrency cap, AI, log level)
        _app._app_cfg.update(data)
        _app._save_app_config()
        _app._load_app_config()
    except Exception as e:
        return {"tool": "config_restore", "ok": False,
                "error": f"restore failed: {type(e).__name__}: {e}"}
    return {
        "tool": "config_restore",
        "ok": True,
        "restored_from": fname,
        "keys_in_snapshot": len(data),
        "merge_restore": True,
        "verdict": f"config restored from '{fname}'",
        "note": ("merge restore: keys absent from the snapshot are "
                 "NOT removed from the live config — a full reset "
                 "needs a process restart"),
    }



def config_snapshot_diff(name=None):
    """D-90 — diff the live app config against a named snapshot (READ-ONLY).

    Returns top-level keys that are added (in live, not the snapshot), removed
    (in the snapshot, not live), and changed (in both, different value). Secret
    values are NEVER echoed: a value whose key looks secret (per
    _SECRET_KEY_HINTS) is shown as ``***redacted***`` in every section. Writes
    nothing and mutates nothing — same name/path-traversal guard as
    config_restore so a diff can only name a file already inside
    config_snapshots/.
    """
    import os as _os
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": f"app module unavailable: {e}"}
    if not name or not str(name).strip():
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": "a snapshot name is required"}
    name = str(name).strip()
    fname = name if name.endswith(".json") else name + ".json"
    if _os.path.basename(fname) != fname \
            or not _SNAPSHOT_NAME_RE.match(fname[:-5]):
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": "invalid snapshot name"}
    src = _os.path.join(_snapshot_dir(), fname)
    if not _os.path.exists(src):
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": f"no snapshot named '{fname}' — "
                         f"use config_snapshot_list to see available"}
    try:
        with open(src, encoding="utf-8") as fh:
            snap = _cfg_json.load(fh)
    except Exception as e:
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": f"snapshot is not valid JSON: {e}"}
    if not isinstance(snap, dict):
        return {"tool": "config_snapshot_diff", "ok": False,
                "error": "snapshot is not a JSON object"}

    live = dict(_app._app_cfg)

    def _is_secret(k):
        return any(h in str(k).lower() for h in _SECRET_KEY_HINTS)

    def _safe(k, v):
        # never echo a secret value, regardless of section
        return "***redacted***" if _is_secret(k) else v

    live_keys, snap_keys = set(live), set(snap)
    added = sorted(live_keys - snap_keys)
    removed = sorted(snap_keys - live_keys)
    changed = []
    for k in sorted(live_keys & snap_keys):
        if live[k] != snap[k]:
            if _is_secret(k):
                changed.append({"key": k, "from": "***redacted***",
                                "to": "***redacted***", "secret": True})
            else:
                changed.append({"key": k, "from": snap[k], "to": live[k]})

    return {
        "tool": "config_snapshot_diff",
        "ok": True,
        "snapshot": fname,
        "added": [{"key": k, "value": _safe(k, live[k])} for k in added],
        "removed": [{"key": k, "value": _safe(k, snap[k])} for k in removed],
        "changed": changed,
        "summary": {"added": len(added), "removed": len(removed),
                    "changed": len(changed)},
        "identical": not (added or removed or changed),
        "verdict": (f"diff vs '{fname}': +{len(added)} -{len(removed)} "
                    f"~{len(changed)} (secret values redacted)"),
    }
