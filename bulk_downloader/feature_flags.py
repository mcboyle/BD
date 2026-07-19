"""Feature flags — T40 / D-121.

Tiny JSON-state subsystem for operator-toggleable feature flags.
Mirrors the maintenance.py pattern: a small file, atomic .tmp +
replace, in-process cache keyed on mtime, fail-open on missing/
malformed state.

Flags are bools. Names are short snake_case strings. Reading is cheap
(cached); writing is rare and explicit. The HTTP surface is:

  GET  /api/dev/feature_flags         — read all (via dev_suite)
  POST /api/dev/feature_flag_set      — set one flag (CSRF-gated)
  POST /api/dev/feature_flag_delete   — remove one flag (CSRF-gated)

Runtime consumers call `is_on(name)` — that is the only function the
hot path should touch.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path


_LOCK = threading.Lock()
_CACHE: dict = {}
_CACHE_MTIME: float = 0.0

# Flag-name regex: snake_case, letters + digits + underscore. Short
# enough to type, long enough to be descriptive.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def state_path() -> Path:
    """Where feature_flags.json lives. INSTALL_DIR-rooted so operator
    state stays out of the package tree."""
    try:
        from .constants import INSTALL_DIR
        return Path(INSTALL_DIR) / "feature_flags.json"
    except Exception:
        return Path.cwd() / "feature_flags.json"


def _load() -> dict:
    """Read flags with mtime-keyed cache. Returns {} on any error."""
    global _CACHE, _CACHE_MTIME
    p = state_path()
    try:
        st = p.stat()
    except Exception:
        with _LOCK:
            _CACHE = {}
            _CACHE_MTIME = 0.0
        return {}
    if st.st_mtime == _CACHE_MTIME and _CACHE:
        return dict(_CACHE)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    # Coerce values: only keep bool, drop anything else (defensive
    # against a hand-edited file with garbage values)
    cleaned = {k: bool(v) for k, v in data.items()
               if isinstance(k, str) and _NAME_RE.match(k)}
    with _LOCK:
        _CACHE = cleaned
        _CACHE_MTIME = st.st_mtime
    return dict(cleaned)


def list_flags() -> dict:
    """Read-only snapshot of all flags. Returns {name: bool}."""
    return _load()


def is_on(name: str) -> bool:
    """Hot-path predicate: is `name` enabled? Unknown flag → False.
    Cheap: cached lookup, no disk read unless mtime changed."""
    if not isinstance(name, str):
        return False
    return bool(_load().get(name, False))


def set_flag(name: str, value: bool) -> dict:
    """Mutating: set `name` to `value`. Atomic .tmp + replace.

    `value` must be a bool (not truthy coercion of arbitrary types —
    a misrouted form value should not silently flip a flag).
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return {
            "ok": False,
            "error": ("name must match ^[a-z][a-z0-9_]{1,63}$"),
        }
    if not isinstance(value, bool):
        return {
            "ok": False,
            "error": "value must be a bool",
        }
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False,
                "error": f"cannot create state dir: {e}"}
    current = _load()
    current[name] = value
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False,
                      sort_keys=True)
        os.replace(tmp, p)
    except Exception as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return {"ok": False, "error": f"atomic write failed: {e}"}
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        _CACHE = dict(current)
        try:
            _CACHE_MTIME = p.stat().st_mtime
        except Exception:
            _CACHE_MTIME = 0.0
    return {"ok": True, "name": name, "value": value}


def delete_flag(name: str) -> dict:
    """Mutating: remove `name` from the flag set. Returns ok=True
    even if the flag wasn't present (idempotent)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return {"ok": False,
                "error": "name must match ^[a-z][a-z0-9_]{1,63}$"}
    p = state_path()
    current = _load()
    if name not in current:
        return {"ok": True, "name": name, "removed": False}
    del current[name]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"cannot create state dir: {e}"}
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False,
                      sort_keys=True)
        os.replace(tmp, p)
    except Exception as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return {"ok": False, "error": f"atomic write failed: {e}"}
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        _CACHE = dict(current)
        try:
            _CACHE_MTIME = p.stat().st_mtime
        except Exception:
            _CACHE_MTIME = 0.0
    return {"ok": True, "name": name, "removed": True}
