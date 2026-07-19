"""Runtime feature flags backed by app_config.json and env vars.

Used by browser/stealth/capture/detection modules to avoid importing app.py.
Canonical config file: app_config.json.
"""
from __future__ import annotations
import os
from typing import Any

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def env_bool(name: str, default: Any = None):
    val = os.environ.get(name)
    if val is None:
        return default
    val = str(val).strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    return default


def cfg_get(key: str, default: Any = None) -> Any:
    try:
        from .global_config import get as _get
        return _get(key, default)
    except Exception:
        return default


def flag(key: str, default: bool = False, env: str | None = None) -> bool:
    if env:
        ev = env_bool(env, None)
        if ev is not None:
            return bool(ev)
    val = cfg_get(key, default)
    if isinstance(val, str):
        return val.strip().lower() in _TRUE
    return bool(val)


def choice(key: str, default: str, env: str | None = None) -> str:
    if env and os.environ.get(env) is not None:
        return str(os.environ.get(env) or default).strip()
    return str(cfg_get(key, default) or default).strip()


def num(key: str, env: str, default: Any, cast=int):
    """Runtime-tunable numeric: store key > env seed > default, cast-safe.

    Read at CALL TIME so a Settings write takes effect live (the promotion
    pattern shared by the v3.66.503 HLS/Live/Captcha tunables). Precedence
    mirrors db._slow_query_threshold_ms: the global_config store wins when set
    (non-blank), else the env seed, else the default. A non-castable value at
    any tier falls through to ``cast(default)`` rather than raising — these are
    operational knobs, never safety gates.
    """
    sv = cfg_get(key, None)
    if sv is not None and str(sv).strip() != "":
        try:
            return cast(sv)
        except (ValueError, TypeError):
            return cast(default)
    ev = os.environ.get(env)
    if ev is not None and str(ev).strip() != "":
        try:
            return cast(ev)
        except (ValueError, TypeError):
            return cast(default)
    return cast(default)
