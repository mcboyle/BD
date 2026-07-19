"""In-process auth attempt throttle — NEW-9.

Opt-in escalating back-off for the endpoints that verify the master
password (``/api/secrets/unlock`` and ``/api/secrets/change_password``).
Both verify the SAME secret, so they share one throttle label — an
attacker who holds a session token cannot split guesses across the two
endpoints to evade the limit.

OFF by default. When off, :func:`check` always allows and
:func:`record_failure` is a no-op, so endpoint behaviour is
byte-identical to before. (:func:`record_success` always clears state —
that is cheap and keeps the table tidy if the flag is toggled at
runtime.)

Why this is defense-in-depth, not the primary control: both endpoints
already sit behind ``BD_AUTH_TOKEN`` and a PBKDF2 600k key derivation
(~150 ms per verify, the natural throttle). This adds an explicit
escalating lockout so a session-token holder cannot brute-force the
master password at machine speed.

Configuration (environment, read fresh per call):

  BD_AUTH_THROTTLE
      off / unset (default)        -> disabled (no-op)
      1 | on | true | yes          -> enabled

  BD_AUTH_THROTTLE_FREE
      consecutive failures allowed before back-off starts (default 5 —
      generous enough that an operator fat-fingering the master password
      a few times is never locked out)

  BD_AUTH_THROTTLE_BASE
      first cooldown in seconds once over the free allowance (default 2.0)

  BD_AUTH_THROTTLE_MAX
      cooldown ceiling in seconds (default 300.0 = 5 min)

Back-off after the free allowance is ``base * 2 ** (over - 1)`` seconds,
capped at ``max``, where ``over`` is the count of failures beyond the
free allowance. A successful verify resets the counter for that label.

State is process-local and uses ``time.monotonic()`` (immune to wall-
clock changes). A process restart clears it — acceptable, since
restarting the service needs host access, a higher bar than an API
session. No module-level work beyond a lock and an empty dict.
"""
from __future__ import annotations

import os
import threading
import time

# Shared label for both master-password verification endpoints, so their
# failed attempts accumulate together (can't be split to evade the cap).
LABEL_MASTER_PASSWORD = "master_password"

_DEFAULT_FREE = 5
_DEFAULT_BASE = 2.0
_DEFAULT_MAX = 300.0
_MULTIPLIER = 2

_ENABLED_VALUES = frozenset({"1", "on", "true", "yes"})

_lock = threading.Lock()
_state: dict = {}  # label -> {"fails": int, "until": float (monotonic)}


def _store_get(key):
    """v3.66.315 (CLI->GUI parity): the global_config store value for ``key``,
    or None when unset/blank. Lazy import, fail-safe -> env/default path."""
    try:
        from bulk_downloader import global_config as _gc
        v = _gc.get(key, None)
        return v if v not in (None, "") else None
    except Exception:
        return None


def is_enabled() -> bool:
    sv = _store_get("auth_throttle")
    if sv is not None:
        return bool(sv)
    return (os.environ.get("BD_AUTH_THROTTLE") or "").strip().lower() in _ENABLED_VALUES


def _cfg() -> tuple[int, float, float]:
    def _num(env, store_key, default, cast):
        sv = _store_get(store_key)
        if sv is not None:
            try:
                return cast(sv)
            except (TypeError, ValueError):
                pass
        try:
            return cast(os.environ[env])
        except (KeyError, TypeError, ValueError):
            return default
    free = _num("BD_AUTH_THROTTLE_FREE", "auth_throttle_free", _DEFAULT_FREE, int)
    base = _num("BD_AUTH_THROTTLE_BASE", "auth_throttle_base", _DEFAULT_BASE, float)
    mx = _num("BD_AUTH_THROTTLE_MAX", "auth_throttle_max", _DEFAULT_MAX, float)
    return max(0, free), max(0.0, base), max(0.0, mx)


def check(label: str) -> tuple[bool, float]:
    """Return ``(allowed, retry_after_seconds)``.

    ``allowed`` is False while a cooldown is in effect; ``retry_after`` is
    the remaining cooldown (rounded). When disabled, always allows.
    """
    if not is_enabled():
        return True, 0.0
    with _lock:
        st = _state.get(label)
        if not st:
            return True, 0.0
        remaining = st.get("until", 0.0) - time.monotonic()
        if remaining > 0:
            return False, round(remaining, 2)
        return True, 0.0


def record_failure(label: str) -> None:
    """Record one failed verification. No-op when disabled."""
    if not is_enabled():
        return
    free, base, mx = _cfg()
    with _lock:
        st = _state.setdefault(label, {"fails": 0, "until": 0.0})
        st["fails"] += 1
        over = st["fails"] - free
        if over > 0:
            cooldown = min(base * (_MULTIPLIER ** (over - 1)), mx)
            st["until"] = time.monotonic() + cooldown


def record_success(label: str) -> None:
    """Clear the failure counter for a label after a successful verify.

    Always clears (even when disabled) so toggling the flag at runtime
    never leaves a stale lockout behind."""
    with _lock:
        _state.pop(label, None)


def reset(label: str | None = None) -> None:
    """Clear throttle state — all labels, or one. For tests/operators."""
    with _lock:
        if label is None:
            _state.clear()
        else:
            _state.pop(label, None)


def snapshot() -> dict:
    """Read-only copy of the current state (for diagnostics/UI)."""
    with _lock:
        return {k: dict(v) for k, v in _state.items()}
