"""v3.43.60: Dashboard widget configuration persistence.

Stores the user's dashboard widget preferences — which 21 KPIs they've
pinned, in what order, at what size — for both the global scope and any
per-site overrides.

# File location

  Linux/macOS: ~/.config/bulk-downloader/widgets.json
  Windows:     %APPDATA%\\BulkDownloader\\widgets.json

Overridable via BD_WIDGETS_CONFIG_PATH for tests/Docker.

# Schema

{
  "schema_version": 1,
  "global": [
    {"id": "done_today", "size": "sm"},
    {"id": "throughput", "size": "md"},
    ...
  ],
  "per_site": {
    "wowgirls": [
      {"id": "queue_depth", "size": "sm"},
      ...
    ]
  }
}

# Defaults

If no config file exists, the API returns the 4-widget default selection
per the answer to Q1 of the v3.43.59 design session.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


SCHEMA_VERSION = 1

# v3.43.59 Q1 answer: default 4-widget selection for new users.
DEFAULT_WIDGETS = [
    {"id": "done_today", "size": "sm"},
    {"id": "throughput", "size": "sm"},
    {"id": "queue_depth", "size": "sm"},
    {"id": "action_req", "size": "sm"},
]

# Valid widget ids — kept in sync with widgets.js WIDGETS list.
VALID_WIDGET_IDS = frozenset({
    # activity
    "done_today", "done_hour", "bytes_today", "files_hour",
    # performance
    "throughput", "success_rate", "avg_speed", "avg_size", "avg_quality",
    # capacity
    "queue_depth", "workers", "disk_free", "bandwidth",
    # health
    "action_req", "stuck", "failures", "retries",
    # system
    "cpu_ram", "eta_clear", "cookies", "sites_active",
    # v3.63.6: gpu — NVIDIA-only via nvidia-smi, fail-open on missing
    # binary or non-NVIDIA hardware (renders '—' rather than 0%).
    # AMD/Intel deliberately not collected (same posture as gpu_check.sh).
    "gpu",
    # collection (v3.51 Phase 4) — surfaces the Phase 3 library data
    "lib_total", "lib_size", "lib_watched", "lib_unrated", "lib_missing",
    "lib_recent", "lib_top_studio", "audit_recent",
    # diagnostics (v3.55 Phase 4 backlog) — site health + error signals
    "success_rate_24h", "error_top_cluster", "captcha_24h",
    "cookie_warnings", "sites_need_attention",
    # v3.58 (Phase 10) — alerts widget
    "active_alerts",
})

VALID_SIZES = frozenset({"sm", "md", "lg"})

MAX_WIDGETS_PER_SCOPE = 24  # generous; defends against runaway PUT payloads


# ─── File location ──────────────────────────────────────────────────

def _config_path() -> Path:
    override = os.environ.get("BD_WIDGETS_CONFIG_PATH")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "BulkDownloader" / "widgets.json"
    return Path(os.path.expanduser("~")) / ".config" / "bulk-downloader" / "widgets.json"


# ─── State ──────────────────────────────────────────────────────────

_state: dict = {
    "schema_version": SCHEMA_VERSION,
    "global": list(DEFAULT_WIDGETS),
    "per_site": {},
}
_lock = threading.RLock()
_loaded = False


# ─── Public API ─────────────────────────────────────────────────────

def load() -> dict:
    """Read widgets.json. Idempotent — repeated calls return cached state."""
    global _loaded
    path = _config_path()
    with _lock:
        if _loaded:
            return _snapshot()
        if not path.exists():
            _loaded = True
            return _snapshot()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            sys.stderr.write(f"[widgets-config] could not read {path}: {e}\n")
            _loaded = True
            return _snapshot()
        _state["schema_version"] = int(data.get("schema_version", SCHEMA_VERSION))
        gs = data.get("global")
        if isinstance(gs, list):
            _state["global"] = _sanitize_list(gs)
        ps = data.get("per_site") or {}
        if isinstance(ps, dict):
            _state["per_site"] = {
                str(k): _sanitize_list(v) for k, v in ps.items()
                if isinstance(v, list)
            }
        _loaded = True
        return _snapshot()


def save() -> None:
    """Atomic write of current state."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        snapshot = {
            "schema_version": _state["schema_version"],
            "global": list(_state["global"]),
            "per_site": dict(_state["per_site"]),
            "_saved_at": time.time(),
        }
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        sys.stderr.write(f"[widgets-config] could not save {path}: {e}\n")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def get_global() -> list[dict]:
    """Return the global widget list (defensive copy)."""
    with _lock:
        return [dict(w) for w in _state["global"]]


def get_for_site(site_id: str) -> list[dict]:
    """Return per-site widget list, or the global list if no per-site override."""
    with _lock:
        if site_id in _state["per_site"]:
            return [dict(w) for w in _state["per_site"][site_id]]
        return [dict(w) for w in _state["global"]]


def get_all() -> dict:
    """Return full config: {global: [...], per_site: {...}}."""
    with _lock:
        return _snapshot()


def set_global(widgets: list[dict]) -> list[dict]:
    """Replace the global widget list. Returns the sanitized list."""
    sanitized = _sanitize_list(widgets)
    with _lock:
        _state["global"] = sanitized
    save()
    return sanitized


def set_for_site(site_id: str, widgets: list[dict]) -> list[dict]:
    """Replace a site's widget list. Returns sanitized list.
    Passing an empty list removes the per-site override (site falls back to global)."""
    if not site_id or not isinstance(site_id, str):
        raise ValueError("site_id required")
    sanitized = _sanitize_list(widgets)
    with _lock:
        if sanitized:
            _state["per_site"][site_id] = sanitized
        else:
            _state["per_site"].pop(site_id, None)
    save()
    return sanitized


def reset_global() -> list[dict]:
    """Reset global widgets to defaults."""
    with _lock:
        _state["global"] = list(DEFAULT_WIDGETS)
    save()
    return get_global()


def reset_for_site(site_id: str) -> bool:
    """Remove per-site override. Returns True if there was an override to remove."""
    with _lock:
        existed = site_id in _state["per_site"]
        _state["per_site"].pop(site_id, None)
    if existed:
        save()
    return existed


# ─── Internals ──────────────────────────────────────────────────────

def _snapshot() -> dict:
    return {
        "schema_version": _state["schema_version"],
        "global": [dict(w) for w in _state["global"]],
        "per_site": {k: [dict(w) for w in v] for k, v in _state["per_site"].items()},
    }


def _sanitize_list(raw) -> list[dict]:
    """Validate + filter incoming widget list. Drops unknown ids/sizes, caps length."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen = set()
    for item in raw[:MAX_WIDGETS_PER_SCOPE]:
        if not isinstance(item, dict):
            continue
        wid = item.get("id")
        if wid not in VALID_WIDGET_IDS:
            continue
        if wid in seen:
            continue  # dedupe — UI shouldn't allow but defense in depth
        seen.add(wid)
        size = item.get("size", "sm")
        if size not in VALID_SIZES:
            size = "sm"
        out.append({"id": wid, "size": size})
    return out


def _reset_for_tests() -> None:
    """Test-only: wipe state and force reload on next access."""
    global _loaded
    with _lock:
        _state["schema_version"] = SCHEMA_VERSION
        _state["global"] = list(DEFAULT_WIDGETS)
        _state["per_site"] = {}
    _loaded = False


__all__ = [
    "SCHEMA_VERSION", "DEFAULT_WIDGETS", "VALID_WIDGET_IDS", "VALID_SIZES",
    "load", "save",
    "get_global", "get_for_site", "get_all",
    "set_global", "set_for_site",
    "reset_global", "reset_for_site",
]
