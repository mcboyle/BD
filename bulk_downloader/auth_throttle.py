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
free allowance (the exponent stops at 20, far past any cap, so a long
flood never overflows the float). A successful verify resets the counter
for that label. A ``soft`` label (cooldown capped at ``base``) counts only
the failures of the last ``max`` seconds: older ones drop out.

Callers that verify AFTER asking (``/api/auth/login``) use :func:`reserve` and
:func:`release` instead of check/record_failure: the check and the count are one
step under the lock, so a burst of concurrent requests cannot all pass the check
before any of them has failed, and each label has at most one verify in flight.

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

from .lock_monitor import register_monitored_lock

_lock = register_monitored_lock("auth_throttle._lock", threading.Lock())
_state: dict = {}  # label -> {"fails": int, "until": float, "last": float} (monotonic)
#                    + "times": the counted failure stamps of a soft label (window)
#                    + "held": the owner of the attempt in flight, while one is
#                    + "prior": that label as it was before the attempt (refund)
# Per-client labels are tuples (app_auth login keys: one per username/address), so
# a caller can mint them at will. Past this many, the tuple label with the fewest
# failures (then the least recent) is dropped: a flood of fresh one-failure labels
# evicts its own kind, never an escalated backoff. Named str labels are never evicted.
_MAX_CLIENT_LABELS = 4096
# The back-off exponent stops here: base * 2**20 is past any sane cap, and an
# unbounded exponent turns a long flood into an OverflowError (dva-auth r3 F4).
_MAX_EXPONENT = 20


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


def check(label) -> tuple[bool, float]:
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


def _evict_client_label() -> None:
    """Drop the fewest-failures, least recent tuple label (caller holds _lock).
    A label with an attempt in flight is never dropped."""
    keys = [k for k in _state if isinstance(k, tuple)]
    if len(keys) >= _MAX_CLIENT_LABELS:
        idle = [k for k in keys if "held" not in _state[k]]
        if idle:
            _state.pop(min(idle, key=lambda k: (_state[k]["fails"], _state[k].get("last", 0.0))))


def _count(label, free, base, mx, now, window=None) -> dict:
    """Add one failure to ``label`` and arm its cooldown (caller holds _lock).

    With ``window`` (soft labels), only the failures of the last ``window``
    seconds count; at most ``free + 1`` stamps are kept, as more than that
    cannot change a cooldown already capped at ``mx`` <= ``base``.
    The new count and cooldown are computed first, then stored together."""
    if label not in _state and isinstance(label, tuple):
        _evict_client_label()
    prior = _state.get(label, {"fails": 0, "until": 0.0})
    new = {}
    if window is None:
        fails = prior["fails"] + 1
    else:
        times = [t for t in prior.get("times", ()) if now - t < window] + [now]
        new["times"] = tuple(times[-(free + 1):])
        fails = len(new["times"])
    until = prior["until"]
    over = fails - free
    if over > 0:
        exp = min(over - 1, _MAX_EXPONENT)
        until = now + min(base * (_MULTIPLIER ** exp), mx)
    new.update(fails=fails, until=until, last=now)
    st = _state.setdefault(label, {})
    st.update(new)
    return st


def record_failure(label, soft: bool = False) -> None:
    """Record one failed verification. No-op when disabled.

    ``soft`` caps this label's cooldown at BD_AUTH_THROTTLE_BASE: a ceiling
    that slows guesses but never makes the real user wait longer than that.
    Its failures older than BD_AUTH_THROTTLE_MAX seconds no longer count."""
    if not is_enabled():
        return
    free, base, mx = _cfg()
    window = mx if soft else None
    if soft:
        mx = min(mx, base)
    with _lock:
        _count(label, free, base, mx, time.monotonic(), window)


def reserve(labels, soft=(), owner=True) -> tuple[bool, float]:
    """Check AND count one attempt on every label, in one lock hold.

    Returns ``(allowed, retry_after)`` like :func:`check`. Refused (nothing
    counted) while any label is in cooldown or already has an attempt in flight
    (``retry_after`` >= 1 s then). Allowed: each label has one more failure
    BEFORE the caller verifies (labels in ``soft`` as ``record_failure(soft=True)``)
    and is marked in flight by ``owner``. The caller must call :func:`release`
    with the same ``owner`` in a ``finally``, also when reserve itself raises:
    an owner that was refused frees nothing. When disabled, always allows and
    records nothing."""
    if not is_enabled():
        return True, 0.0
    free, base, mx = _cfg()
    with _lock:
        now = time.monotonic()
        for label in labels:
            st = _state.get(label)
            if not st:
                continue
            remaining = st.get("until", 0.0) - now
            if remaining > 0 or "held" in st:
                return False, round(max(remaining, 1.0), 2)
        for label in labels:
            prior = dict(_state[label]) if label in _state else None
            if label in soft:
                st = _count(label, free, base, min(mx, base), now, window=mx)
            else:
                st = _count(label, free, base, mx, now)
            st["held"], st["prior"] = owner, prior
    return True, 0.0


def release(labels, success: bool = False, owner=True, refund: bool = False) -> None:
    """End the attempts :func:`reserve` started for ``owner``; another owner's
    attempt stays in flight. A failure (or an exception in the verify) leaves
    each attempt counted. On ``success`` every label not in flight for another
    owner is dropped (as :func:`record_success`), not refunded. With ``refund``
    (the verify never reached a verdict) each label ``owner`` holds is put back
    as it was before :func:`reserve`. Always runs, even when disabled."""
    with _lock:
        for label in labels:
            st = _state.get(label)
            if not st:
                continue
            held = st.get("held")
            if held is not None and held is not owner:
                continue
            if success:
                _state.pop(label)
            elif held is not None:
                prior = st.pop("prior", None)
                del st["held"]
                if refund:
                    if prior is None:
                        _state.pop(label)
                    else:
                        _state[label] = prior


def record_success(label) -> None:
    """Clear the failure counter for a label after a successful verify.

    Always clears (even when disabled) so toggling the flag at runtime
    never leaves a stale lockout behind."""
    with _lock:
        _state.pop(label, None)


def reset(label=None) -> None:
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
