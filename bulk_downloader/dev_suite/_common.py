"""dev_suite._common -- shared leaf helpers (imports nothing from sibling submodules)

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

def _dev_mode() -> bool:
    try:
        from bulk_downloader import dev_tools as _dt
        return _dt.is_dev_mode()
    except Exception:
        return False



# ── 6. effective settings ──────────────────────────────────────────

_BD_ENV_VARS = (
    "BD_DEV_MODE", "BD_DEV_MODE_DISABLE", "BD_DISABLE_KEEPALIVE",
    "BD_TEST_MODE", "BD_HOME", "BD_HOST", "BD_PORT",
    "BULK_DOWNLOADER_DEBUG",
)



# ── 7. config dump (redacted) ──────────────────────────────────────

_SECRET_KEY_HINTS = ("password", "passwd", "secret", "token", "apikey",
                     "api_key", "cookie", "cred", "private", "auth")



def _redact(obj):
    """Recursively mask values whose key name looks secret. Structure
    and non-secret values are preserved so the dump stays useful."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(h in str(k).lower() for h in _SECRET_KEY_HINTS):
                out[k] = "***redacted***" if v else v
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj



# ── 8. process info ────────────────────────────────────────────────

def _proc_uptime_seconds():
    """Real process age in seconds, from /proc. None when unavailable."""
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            system_uptime = float(f.read().split()[0])
        with open("/proc/self/stat", encoding="utf-8") as f:
            starttime_ticks = float(f.read().split()[21])
        hz = os.sysconf("SC_CLK_TCK")
        return round(system_uptime - (starttime_ticks / hz), 1)
    except Exception:
        return None



# ── 12. config integrity ───────────────────────────────────────────

def _collect_cred_refs(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_cred_refs(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_cred_refs(v, acc)
    elif isinstance(obj, str) and obj.startswith("@cred:"):
        acc.append(obj)



# ════════════ release & integrity tools (backlog Tier 0) ══════════
# Read-only checks over the on-disk source tree, the route table, and
# the DB. The tree-scanning tools resolve the project root from the
# package location — Path(__file__).parents[1] — NOT the cwd, so they
# validate the real install even under a test chdir. Nothing here runs
# at import time.

def _repo_root() -> Path:
    """The project root — the directory holding CHANGELOG.md, the
    installers, and the bulk_downloader package."""
    return Path(__file__).resolve().parents[2]


def _pkg_dir() -> Path:
    """The bulk_downloader package directory (parent of dev_suite/)."""
    return Path(__file__).resolve().parents[1]



def _read_version() -> str:
    """The single source of truth: bulk_downloader/__init__.py."""
    from bulk_downloader import __version__
    return str(__version__)



def _iter_route_sources(app):
    """Yield (rule, sorted-methods, endpoint, source_text) for every
    registered route. source_text is '' when the view source cannot
    be read (a built-in or a lambda)."""
    import inspect
    for rule in app.url_map.iter_rules():
        methods = sorted(m for m in rule.methods
                         if m not in ("HEAD", "OPTIONS"))
        view = app.view_functions.get(rule.endpoint)
        src = ""
        if view is not None:
            try:
                src = inspect.getsource(view)
            except (OSError, TypeError):
                src = ""
        yield str(rule), methods, rule.endpoint, src



# ════════════ request-metrics & thread tools (Tier 1) ═════════════
# The latency / slow-endpoint / error-rate / exception tools read the
# in-process ring buffers in dev_metrics (fed by the app.py request
# hooks). dev_suite stays read-only — it only reads dev_metrics, never
# writes it. The thread tools use sys._current_frames() and hold no
# state at all.

def _percentile(sorted_vals, pct):
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    k = int(round(pct / 100.0 * (len(sorted_vals) - 1)))
    k = max(0, min(len(sorted_vals) - 1, k))
    return sorted_vals[k]



def _resolve_site_config(site_id, runners):
    """Read-only best-effort load of a site's config dict — live
    runner first, then sites_config.json on disk. Returns
    (config_dict_or_None, source_str)."""
    if not site_id:
        return None, "no site_id given"
    if runners:
        try:
            rn = runners.get(site_id)
        except Exception:
            rn = None
        if rn is not None:
            cfg = getattr(rn, "config", None)
            if isinstance(cfg, dict):
                return dict(cfg), "live runner"
    import json as _json
    try:
        path = Path("sites_config.json")
        if path.exists():
            data = _json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(data, dict) and isinstance(data.get(site_id),
                                                      dict)):
                return dict(data[site_id]), "sites_config.json"
    except Exception:
        pass
    return None, f"site '{site_id}' not in runners or sites_config.json"



# ── 34. config schema audit / import preflight (U15: D-86 + D-89) ──
#
# A structural pair sharing one schema definition (site_editor):
#   • config_schema_audit (D-86) runs site_editor.validate_config
#     across EVERY site at once — the fleet-wide sweep the per-site
#     editor validator does not do — plus a list/dict-in-scalar check.
#   • import_preflight (D-89) is that same validation aimed at an
#     inbound CSV/XLSX import file, before it is imported.
# Both reuse existing code (site_editor.validate_config,
# csv_bulk.parse_import) rather than reimplementing validation.

def _resolve_all_site_configs(runners):
    """Read-only best-effort load of every site's config — live
    runners first, then sites_config.json. Returns (dict, source).

    v3.66.9: distinguish runners=None ("caller doesn't know, look it
    up") from runners={} ("caller explicitly says nothing"). Previously
    both fell through to the disk fallback because `if runners` is
    falsy for both -- which meant config_schema_audit(runners={}) would
    silently load sites_config.json from cwd. Now only None falls
    back; explicit {} returns empty.
    """
    if runners is None:
        import json as _json
        try:
            path = Path("sites_config.json")
            if path.exists():
                data = _json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return ({k: v for k, v in data.items()
                             if isinstance(v, dict)}, "sites_config.json")
        except Exception:
            pass
        return {}, "no runners or sites_config.json"
    if runners:
        try:
            out = {sid: dict(getattr(rn, "config", {}) or {})
                   for sid, rn in runners.items()}
            if out:
                return out, "live runners"
        except Exception:
            pass
    # runners is an explicit empty dict (or unpacking failed)
    return {}, "no runners or sites_config.json"



def _human_secs(s):
    """Seconds to a short human string: 3600 -> '1h', 5400 -> '1h30m'."""
    s = int(s)
    if s <= 0:
        return "0s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if s >= size:
            parts.append(f"{s // size}{unit}")
            s %= size
    return "".join(parts)



# -- the "not source" canon ---------------------------------------------
# v3.66.749: moved here from release_lint so every dev_suite check that
# walks the tree derives its exclusions from ONE set instead of
# re-typing it (the re-typed copy in audit_security._SECRET_SKIP_DIRS
# had silently drifted 7 dirs behind). release_lint re-exports the name
# unchanged. History of individual entries lives with them below.
_MANIFEST_EXCLUDE_DIRS = {"__pycache__", ".git", "venv", ".venv",
                          "node_modules", "screenshots",
                          ".pytest_cache", "results", "profiles",
                          ".mypy_cache",
                          # v3.66.798 (BUILD-HYG): app.py resolves
                          # _live_state_dir to <DATA_DIR or .>/
                          # live_recordings (~5418) and
                          # live_recorder.init() mkdirs it;
                          # recordings.json is _load_state()
                          # persistence, regenerated on init, never
                          # source. Unexcluded, a release built where
                          # the recorder ever initialized ships that
                          # state and the unzip -o overlay clobbers
                          # the operator's live recordings on stash.
                          "live_recordings",
                          # v3.65.1 B5: state/ is the
                          # _heartbeat_to_disk_loop's exclusive working
                          # directory (mkdir'd at runtime, never source).
                          # Without this exclusion, importing
                          # bulk_downloader.app during the
                          # build_release.py endpoint-catalog gate
                          # spins up the heartbeat thread, which writes
                          # state/heartbeat.json — then the release
                          # zip ships with the developer's last
                          # heartbeat snapshot. Cosmetic, but pollutes
                          # release archeology.
                          # v3.66.748 (audit R18): Hypothesis's example DB +
                          # constants cache. Pure test-run state, regenerated on
                          # demand, never source -- 27 entries were shipping in
                          # the release zip. Excluding it here keeps it OUT of
                          # the build; tools/diff_release_zips.py now also FLAGS
                          # it, so if it ever gets back in, the gate says so
                          # instead of reporting clean over it.
                          ".hypothesis",
                          "state"}
