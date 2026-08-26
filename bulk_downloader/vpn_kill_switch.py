"""v3.43.60: Process-level kill switch (default).

When a leak-detection probe flags a critical leak on a tunnel, this module:

  1. Marks the tunnel as "killed" in its own state store
  2. Notifies registered callbacks (runner.py uses this to halt its workers)
  3. Logs the reason
  4. Optionally schedules an auto-cycle attempt to recover

Workers consult `is_killed(tunnel_id)` before claiming a URL and before
each HTTP request. URLs already in flight are returned to the queue when
the worker sees the kill and bails.

This is the DEFAULT kill switch — runs without admin/root, doesn't touch
the OS firewall, only affects this process tree. The system-level kill
switch in vpn_kill_switch_system.py is the opt-in heavier alternative.

# Kill state transitions

    cleared → killed  (on critical leak, or manual call to kill_tunnel)
    killed  → cleared (manual clear_kill, or auto after fully measured success)
    killed  → cycling (auto-recovery in progress)
    cycling → cleared (cycle succeeded + fully measured probes pass)
    cycling → killed  (cycle failed; back to killed, no further auto-attempts)

# Public API

    notify_leak_test_result(tunnel_id, agg)  - called by vpn_leak_tests
    is_killed(tunnel_id) -> bool             - cheap check for hot paths
    get_kill_state(tunnel_id) -> dict | None - for UI
    kill_tunnel(tunnel_id, reason)           - manual / external
    clear_kill(tunnel_id)                    - manual
    register_kill_callback(fn)               - runner subscribes here
    set_auto_recover(enabled: bool)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional


# ─── Constants ──────────────────────────────────────────────────────

# How many cycle attempts before we give up and require manual intervention.
MAX_AUTO_CYCLE_ATTEMPTS = 2

# Min seconds between cycle attempts.
CYCLE_BACKOFF_S = 30

# After this many consecutive successful probes following a kill, we
# automatically clear the kill state (defense against transient probe
# failures).
AUTO_CLEAR_THRESHOLD = 2


# ─── State ──────────────────────────────────────────────────────────

@dataclass
class KillState:
    tunnel_id: str
    killed_at: float
    reason: str
    state: str = "killed"  # "killed" | "cycling" | "cleared"
    cycle_attempts: int = 0
    last_cycle_at: Optional[float] = None
    last_leak_summary: str = ""
    auto_cleared_streak: int = 0  # consecutive successful probes since kill

    def to_dict(self) -> dict:
        return asdict(self)


_states: dict[str, KillState] = {}
_state_lock = threading.RLock()

_callbacks: list[Callable[[str, str], None]] = []
_callbacks_lock = threading.Lock()

_auto_recover_enabled: bool = True


# ─── Public API ─────────────────────────────────────────────────────

def notify_leak_test_result(tunnel_id: str, agg) -> None:
    """Called by vpn_leak_tests.run_all_probes after each probe run.

    `agg` is an AggregateResult. Confirmed critical failures arm the switch;
    unknown critical measurements hold an already-armed switch without being
    described as a leak. Only an all-measured, zero-failure aggregate advances
    the consecutive-success auto-clear streak.
    """
    crit = getattr(agg, "critical_failures", 0)
    unknown = getattr(agg, "critical_unknowns", 0)
    all_critical_measured = getattr(agg, "all_critical_measured", unknown == 0)
    summary = getattr(agg, "summary", "") or ""

    with _state_lock:
        existing = _states.get(tunnel_id)

    if crit > 0:
        if existing is None or existing.state == "cleared":
            kill_tunnel(tunnel_id, reason=summary)
            return
        # Already killed/cycling — reset the auto-clear streak.
        with _state_lock:
            existing.auto_cleared_streak = 0
            existing.last_leak_summary = summary
        # If currently cycling, the cycle didn't help — revert to killed.
        if existing.state == "cycling":
            with _state_lock:
                existing.state = "killed"
            sys.stderr.write(
                f"[vpn-killswitch] {tunnel_id}: auto-cycle did not clear the leak, "
                f"holding in killed state\n"
            )
        return

    # UNKNOWN is not a confirmed leak and must not arm a clear tunnel. It is
    # also not successful evidence: reset the consecutive-success streak and
    # hold an active switch. A cycling tunnel likewise returns to killed until
    # a later fully measured aggregate establishes recovery.
    if unknown > 0 or not all_critical_measured:
        if existing is None or existing.state == "cleared":
            return
        with _state_lock:
            existing.auto_cleared_streak = 0
            if existing.state == "cycling":
                existing.state = "killed"
        return

    # No critical failures and every critical probe was measured. If the
    # tunnel is killed, count this result toward auto-clear.
    if existing is None or existing.state == "cleared":
        return

    with _state_lock:
        existing.auto_cleared_streak += 1
        ready_to_clear = existing.auto_cleared_streak >= AUTO_CLEAR_THRESHOLD
    if ready_to_clear:
        clear_kill(tunnel_id)


def is_killed(tunnel_id: str) -> bool:
    """Cheap hot-path check. Workers call this before claiming work."""
    with _state_lock:
        s = _states.get(tunnel_id)
        return s is not None and s.state in ("killed", "cycling")


def get_kill_state(tunnel_id: str) -> Optional[dict]:
    with _state_lock:
        s = _states.get(tunnel_id)
        return s.to_dict() if s else None


def list_kill_states() -> list[dict]:
    with _state_lock:
        return [s.to_dict() for s in _states.values()]


def kill_tunnel(tunnel_id: str, reason: str = "") -> None:
    """Mark `tunnel_id` killed. Fires registered callbacks. Optionally
    triggers an auto-cycle attempt if enabled and within retry budget."""
    if not tunnel_id:
        return
    with _state_lock:
        existing = _states.get(tunnel_id)
        if existing is not None and existing.state in ("killed", "cycling"):
            existing.reason = reason or existing.reason
            existing.last_leak_summary = reason or existing.last_leak_summary
            return
        state = KillState(
            tunnel_id=tunnel_id,
            killed_at=time.time(),
            reason=reason or "manual kill",
            state="killed",
            last_leak_summary=reason,
        )
        _states[tunnel_id] = state

    sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: KILLED ({reason or 'manual'})\n")
    _fire_callbacks(tunnel_id, "killed")
    # E1 (v3.66.494): isolated plugin event. Only reached on a FRESH kill --
    # a re-kill of an already-killed/cycling tunnel returns early above, so
    # the kill switch arming fires exactly once per arm.
    try:
        from . import plugins as _pl
        _pl.emit("vpn.killswitch_armed",
                 {"tunnel_id": tunnel_id,
                  "reason": reason or "manual kill",
                  "ts": int(time.time())})
    except Exception:
        pass

    if get_auto_recover():
        _schedule_auto_cycle(tunnel_id)


def clear_kill(tunnel_id: str) -> None:
    with _state_lock:
        existing = _states.get(tunnel_id)
        if existing is None:
            return
        if existing.state == "cleared":
            return
        existing.state = "cleared"
        existing.auto_cleared_streak = 0
    sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: cleared\n")
    _fire_callbacks(tunnel_id, "cleared")


def register_kill_callback(fn: Callable[[str, str], None]) -> None:
    """Register a callable invoked as fn(tunnel_id, new_state) on state changes.

    new_state ∈ {"killed", "cleared", "cycling"}. Callbacks must not raise
    or block — runner.py uses this to flip its own per-tunnel allow flag,
    which workers poll.
    """
    with _callbacks_lock:
        _callbacks.append(fn)


def unregister_kill_callback(fn: Callable[[str, str], None]) -> None:
    with _callbacks_lock:
        try:
            _callbacks.remove(fn)
        except ValueError:
            pass


def set_auto_recover(enabled: bool) -> None:
    global _auto_recover_enabled
    with _state_lock:
        _auto_recover_enabled = bool(enabled)


def get_auto_recover() -> bool:
    with _state_lock:
        return _auto_recover_enabled


# ─── Internals ──────────────────────────────────────────────────────

def _fire_callbacks(tunnel_id: str, new_state: str) -> None:
    with _callbacks_lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb(tunnel_id, new_state)
        except Exception as e:
            sys.stderr.write(
                f"[vpn-killswitch] callback raised: {type(e).__name__}: {e}\n"
            )


def _schedule_auto_cycle(tunnel_id: str) -> None:
    """Fire a background thread that waits CYCLE_BACKOFF_S then attempts
    one cycle of the tunnel. Limited by MAX_AUTO_CYCLE_ATTEMPTS.

    NOT under BD_DISABLE_KEEPALIVE. That flag means "no background thread may
    outlive the run that started it", and this is exactly such a thread: it
    sleeps CYCLE_BACKOFF_S and then performs a REAL tunnel stop/start. Under
    pytest the arming test is long finished by then and an unrelated test owns
    the worker, so the teardown kills it -- measured 2026-08-12 on two of three
    fleet hosts as `[gw1] node down: Not properly terminated` at 99%, followed
    by an unbounded hang. pytest-timeout cannot save it: the timeout is
    enforced inside the worker that just died.

    Checked BEFORE the state mutation below, deliberately. Incrementing
    cycle_attempts and setting state="cycling" and THEN declining to cycle
    would leave the tunnel advertising an in-flight recovery that nothing is
    performing -- a state that lies about work, which is worse than the absent
    feature it is standing in for.
    """
    if os.environ.get("BD_DISABLE_KEEPALIVE"):
        return
    with _state_lock:
        s = _states.get(tunnel_id)
        if s is None:
            return
        if s.cycle_attempts >= MAX_AUTO_CYCLE_ATTEMPTS:
            return
        s.cycle_attempts += 1
        s.last_cycle_at = time.time()
        s.state = "cycling"

    t = threading.Thread(
        target=_auto_cycle_worker,
        args=(tunnel_id,),
        name=f"bd-killswitch-cycle-{tunnel_id}",
        daemon=True,
    )
    t.start()


def _auto_cycle_worker(tunnel_id: str) -> None:
    """Wait, then call vpn.cycle_tunnel(tunnel_id). After cycle, the next
    leak-test pass will determine if we clear or remain killed."""
    time.sleep(CYCLE_BACKOFF_S)
    try:
        from .vpn import cycle_tunnel
    except ImportError:
        sys.stderr.write("[vpn-killswitch] cannot import cycle_tunnel — aborting auto-cycle\n")
        with _state_lock:
            s = _states.get(tunnel_id)
            if s is not None:
                s.state = "killed"
        return

    sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: auto-cycling tunnel (attempt #{_attempt_count(tunnel_id)})\n")
    _fire_callbacks(tunnel_id, "cycling")
    try:
        ok = cycle_tunnel(tunnel_id)
    except Exception as e:
        sys.stderr.write(f"[vpn-killswitch] cycle raised: {e}\n")
        ok = False
    if not ok:
        sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: cycle FAILED, holding killed\n")
        with _state_lock:
            s = _states.get(tunnel_id)
            if s is not None:
                s.state = "killed"
        _fire_callbacks(tunnel_id, "killed")
    # If cycle succeeded, the next leak-probe pass auto-clears via
    # notify_leak_test_result's auto-clear logic.


def _attempt_count(tunnel_id: str) -> int:
    with _state_lock:
        s = _states.get(tunnel_id)
        return s.cycle_attempts if s else 0


# ─── Test/introspection helpers ─────────────────────────────────────

def _reset_for_tests() -> None:
    global _auto_recover_enabled
    with _state_lock:
        _states.clear()
        _auto_recover_enabled = True
    with _callbacks_lock:
        _callbacks.clear()


__all__ = [
    "KillState",
    "notify_leak_test_result",
    "is_killed", "get_kill_state", "list_kill_states",
    "kill_tunnel", "clear_kill",
    "register_kill_callback", "unregister_kill_callback",
    "set_auto_recover", "get_auto_recover",
]
