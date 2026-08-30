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

# Auto-cycle waits are cancellation-responsive, so this normally measures only
# target finalization.  A real tunnel cycle can still be in flight; fail closed
# after the bound rather than letting its generation cross a reset/re-init.
CYCLE_STOP_TIMEOUT_S = 5.0

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

# Every state mutation receives a process-monotonic version. A callback may be
# delayed between mutation and delivery (including across shutdown/re-init),
# so state text alone cannot distinguish an old "cleared" from a new one.
_transition_versions: dict[str, int] = {}
_transition_counter = 0
_callback_transition_context = threading.local()

_auto_recover_enabled: bool = True

# Every scheduled cycle owns an explicit generation token, stop event, and
# thread handle.  The lifecycle lock serializes scheduling/reset; the action
# lock is the reset boundary around real tunnel cycling and callback
# publication.  Registry invalidation makes a late old worker inert even when
# a bounded stop cannot immediately join it.
_cycle_lifecycle_lock = threading.RLock()
_cycle_registry_lock = threading.RLock()
_cycle_action_lock = threading.RLock()
_cycle_records: dict[object, tuple[str, threading.Thread, threading.Event]] = {}
_cycle_current: dict[str, object] = {}
_cycle_context = threading.local()
_cycle_epoch = 0


def _next_transition_locked(tunnel_id: str) -> int:
    """Publish a new transition version while ``_state_lock`` is held."""
    global _transition_counter
    _transition_counter += 1
    _transition_versions[tunnel_id] = _transition_counter
    return _transition_counter


def _current_transition_locked(
    tunnel_id: str,
    new_state: str,
    version: int,
) -> bool:
    state = _states.get(tunnel_id)
    return (
        state is not None
        and state.state == new_state
        and _transition_versions.get(tunnel_id) == version
    )


def _callback_transition_is_current(tunnel_id: str, new_state: str) -> bool:
    """Revalidate the transition currently delivering on this thread."""
    context = getattr(_callback_transition_context, "value", None)
    if not isinstance(context, tuple) or len(context) != 3:
        return False
    context_tunnel, context_state, version = context
    if context_tunnel != tunnel_id or context_state != new_state:
        return False
    with _state_lock:
        return _current_transition_locked(tunnel_id, new_state, version)


def capture_leak_measurement_token(tunnel_id: str) -> tuple:
    """Bind one probe run to the exact kill/runtime generation it measures."""
    with _cycle_lifecycle_lock:
        with _state_lock:
            return (
                _cycle_epoch,
                _states.get(tunnel_id),
                _transition_versions.get(tunnel_id),
            )


def _measurement_matches_locked(tunnel_id: str, token: Optional[tuple]) -> bool:
    """Validate a probe token while lifecycle then state locks are held."""
    if token is None:
        return True
    if not isinstance(token, tuple) or len(token) != 3:
        return False
    epoch, state_identity, transition_version = token
    return (
        epoch == _cycle_epoch
        and _states.get(tunnel_id) is state_identity
        and _transition_versions.get(tunnel_id) == transition_version
    )


# ─── Public API ─────────────────────────────────────────────────────

def notify_leak_test_result(
    tunnel_id: str,
    agg,
    measurement_token: Optional[tuple] = None,
) -> None:
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

    if crit > 0:
        with _cycle_lifecycle_lock:
            with _state_lock:
                if not _measurement_matches_locked(
                    tunnel_id, measurement_token
                ):
                    return
                existing = _states.get(tunnel_id)
                should_kill = existing is None or existing.state == "cleared"
                cancel_cycle = False
                if not should_kill:
                    existing.auto_cleared_streak = 0
                    existing.last_leak_summary = summary
                    if existing.state == "cycling":
                        existing.state = "killed"
                        _next_transition_locked(tunnel_id)
                        cancel_cycle = True
            if cancel_cycle:
                _cancel_cycle_generation(tunnel_id)
        if should_kill:
            kill_tunnel(
                tunnel_id,
                reason=summary,
                _measurement_token=measurement_token,
            )
            return
        if cancel_cycle:
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
        with _cycle_lifecycle_lock:
            with _state_lock:
                if not _measurement_matches_locked(
                    tunnel_id, measurement_token
                ):
                    return
                existing = _states.get(tunnel_id)
                if existing is None or existing.state == "cleared":
                    return
                existing.auto_cleared_streak = 0
                if existing.state == "cycling":
                    existing.state = "killed"
                    _next_transition_locked(tunnel_id)
                    cancel_cycle = True
                else:
                    cancel_cycle = False
            if cancel_cycle:
                _cancel_cycle_generation(tunnel_id)
        return

    # No critical failures and every critical probe was measured. If the
    # tunnel is killed, count this result toward auto-clear.
    with _cycle_lifecycle_lock:
        with _state_lock:
            if not _measurement_matches_locked(tunnel_id, measurement_token):
                return
            existing = _states.get(tunnel_id)
            if existing is None or existing.state == "cleared":
                return
            existing.auto_cleared_streak += 1
            ready_to_clear = (
                existing.auto_cleared_streak >= AUTO_CLEAR_THRESHOLD)
            admitted_state = existing
            admitted_version = _transition_versions.get(tunnel_id)
    if ready_to_clear:
        clear_kill(
            tunnel_id,
            _expected_state=admitted_state,
            _expected_transition_version=admitted_version,
            _measurement_token=measurement_token,
        )


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


def kill_tunnel(
    tunnel_id: str,
    reason: str = "",
    *,
    _measurement_token: Optional[tuple] = None,
) -> bool:
    """Mark `tunnel_id` killed. Fires registered callbacks. Optionally
    triggers an auto-cycle attempt if enabled and within retry budget."""
    if not tunnel_id:
        return False
    with _cycle_lifecycle_lock:
        admission_epoch = _cycle_epoch
        with _state_lock:
            if not _measurement_matches_locked(
                tunnel_id, _measurement_token
            ):
                return False
            existing = _states.get(tunnel_id)
            if existing is not None and existing.state in ("killed", "cycling"):
                existing.reason = reason or existing.reason
                existing.last_leak_summary = reason or existing.last_leak_summary
                return False
            state = KillState(
                tunnel_id=tunnel_id,
                killed_at=time.time(),
                reason=reason or "manual kill",
                state="killed",
                last_leak_summary=reason,
            )
            _states[tunnel_id] = state
            transition_version = _next_transition_locked(tunnel_id)

    sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: KILLED ({reason or 'manual'})\n")
    _fire_transition_callbacks(
        tunnel_id,
        "killed",
        transition_version,
        admission_epoch=admission_epoch,
    )
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
        _schedule_auto_cycle(tunnel_id, admission_epoch=admission_epoch)
    return True


def clear_kill(
    tunnel_id: str,
    *,
    _expected_state: Optional[KillState] = None,
    _expected_transition_version: Optional[int] = None,
    _measurement_token: Optional[tuple] = None,
) -> bool:
    with _cycle_lifecycle_lock:
        with _cycle_registry_lock:
            with _state_lock:
                if not _measurement_matches_locked(
                    tunnel_id, _measurement_token
                ):
                    return False
                existing = _states.get(tunnel_id)
                if existing is None:
                    return False
                if (_expected_state is not None
                        and existing is not _expected_state):
                    return False
                if (_expected_transition_version is not None
                        and _transition_versions.get(tunnel_id)
                        != _expected_transition_version):
                    return False
                if existing.state == "cleared":
                    return False
                existing.state = "cleared"
                existing.auto_cleared_streak = 0
                transition_version = _next_transition_locked(tunnel_id)
            token = _cycle_current.pop(tunnel_id, None)
            record = _cycle_records.get(token) if token is not None else None
            if record is not None:
                record[2].set()
    sys.stderr.write(f"[vpn-killswitch] {tunnel_id}: cleared\n")
    _fire_transition_callbacks(tunnel_id, "cleared", transition_version)
    return True


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

def _cancel_cycle_generation(tunnel_id: str) -> None:
    """Invalidate and signal one tunnel's pending generation."""
    with _cycle_lifecycle_lock:
        with _cycle_registry_lock:
            token = _cycle_current.pop(tunnel_id, None)
            record = _cycle_records.get(token) if token is not None else None
            if record is not None:
                record[2].set()

def _fire_transition_callbacks(
    tunnel_id: str,
    new_state: str,
    transition_version: int,
    *,
    admission_epoch: Optional[int] = None,
) -> None:
    """Deliver callbacks only while this exact transition remains current.

    No lifecycle, state, or callback lock is held across injected code. The
    transition is checked before the snapshot and again before each callback;
    a thread-local context lets the callback revalidate at its own sink.
    """
    if admission_epoch is not None:
        with _cycle_lifecycle_lock:
            if admission_epoch != _cycle_epoch:
                return
    with _state_lock:
        if not _current_transition_locked(
            tunnel_id, new_state, transition_version
        ):
            return
    with _callbacks_lock:
        callbacks = list(_callbacks)
    for callback in callbacks:
        with _state_lock:
            if not _current_transition_locked(
                tunnel_id, new_state, transition_version
            ):
                return
        previous_context = getattr(
            _callback_transition_context, "value", None)
        _callback_transition_context.value = (
            tunnel_id, new_state, transition_version)
        try:
            callback(tunnel_id, new_state)
        except Exception as exc:
            sys.stderr.write(
                "[vpn-killswitch] callback raised: "
                f"{type(exc).__name__}: {exc}\n")
        finally:
            if previous_context is None:
                try:
                    del _callback_transition_context.value
                except AttributeError:
                    pass
            else:
                _callback_transition_context.value = previous_context


def _schedule_auto_cycle(
    tunnel_id: str,
    admission_epoch: Optional[int] = None,
) -> None:
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
    with _cycle_lifecycle_lock:
        if (admission_epoch is not None
                and admission_epoch != _cycle_epoch):
            return
        with _cycle_registry_lock:
            with _state_lock:
                s = _states.get(tunnel_id)
                if s is None:
                    return
                if s.cycle_attempts >= MAX_AUTO_CYCLE_ATTEMPTS:
                    return
                previous_cycle_attempts = s.cycle_attempts
                previous_last_cycle_at = s.last_cycle_at
                previous_state = s.state
                previous_transition_version = _transition_versions.get(
                    tunnel_id)
                s.cycle_attempts += 1
                s.last_cycle_at = time.time()
                s.state = "cycling"
                _next_transition_locked(tunnel_id)

            # Supersede any older token for this tunnel before publishing the
            # new generation.  Old workers check identity immediately before
            # every cycle/state/callback side effect.
            old_token = _cycle_current.get(tunnel_id)
            if old_token is not None:
                old_record = _cycle_records.get(old_token)
                if old_record is not None:
                    old_record[2].set()
            token = object()
            stop_event = threading.Event()
            thread = threading.Thread(
                target=_cycle_generation_entry,
                args=(tunnel_id, token, stop_event),
                name=f"bd-killswitch-cycle-{tunnel_id}",
                daemon=True,
            )
            _cycle_current[tunnel_id] = token
            _cycle_records[token] = (tunnel_id, thread, stop_event)
            try:
                thread.start()
            except BaseException:
                _cycle_records.pop(token, None)
                if _cycle_current.get(tunnel_id) is token:
                    _cycle_current.pop(tunnel_id, None)
                with _state_lock:
                    current = _states.get(tunnel_id)
                    if current is s:
                        current.cycle_attempts = previous_cycle_attempts
                        current.last_cycle_at = previous_last_cycle_at
                        current.state = previous_state
                        if previous_transition_version is None:
                            _transition_versions.pop(tunnel_id, None)
                        else:
                            _transition_versions[tunnel_id] = (
                                previous_transition_version)
                raise


def _cycle_generation_entry(
    tunnel_id: str,
    token: object,
    stop_event: threading.Event,
) -> None:
    """Bind one tracked generation around the patchable worker target."""
    _cycle_context.token = token
    _cycle_context.stop_event = stop_event
    try:
        # Keep the one-argument target contract: existing safety tests patch
        # this worker before scheduling so no real tunnel operation can run.
        _auto_cycle_worker(tunnel_id)
    finally:
        with _cycle_registry_lock:
            _cycle_records.pop(token, None)
        try:
            del _cycle_context.token
            del _cycle_context.stop_event
        except AttributeError:
            pass


def _cycle_is_current(
    tunnel_id: str,
    token: object,
    stop_event: threading.Event,
) -> bool:
    with _cycle_registry_lock:
        if (stop_event.is_set()
                or _cycle_current.get(tunnel_id) is not token):
            return False
        with _state_lock:
            state = _states.get(tunnel_id)
            return state is not None and state.state == "cycling"


def _auto_cycle_worker(tunnel_id: str) -> None:
    """Wait, then call vpn.cycle_tunnel(tunnel_id). After cycle, the next
    leak-test pass will determine if we clear or remain killed."""
    token = getattr(_cycle_context, "token", None)
    stop_event = getattr(_cycle_context, "stop_event", None)
    if token is None or stop_event is None:
        # Only the tracked entrypoint is allowed to perform a real cycle.
        return
    if stop_event.wait(CYCLE_BACKOFF_S):
        return

    # This lock is the reset boundary.  Reset first invalidates tokens, then
    # crosses this action lock before it clears callbacks/state or permits a
    # new runtime generation.  Thus an already-running action finishes before
    # reset returns; a late action observes its invalid token and is inert.
    with _cycle_action_lock:
        if not _cycle_is_current(tunnel_id, token, stop_event):
            return
        try:
            from .vpn import cycle_tunnel
        except ImportError:
            sys.stderr.write(
                "[vpn-killswitch] cannot import cycle_tunnel — "
                "aborting auto-cycle\n")
            with _cycle_registry_lock:
                if _cycle_current.get(tunnel_id) is token:
                    with _state_lock:
                        state = _states.get(tunnel_id)
                        if state is not None and state.state == "cycling":
                            state.state = "killed"
                            _next_transition_locked(tunnel_id)
            return

        sys.stderr.write(
            f"[vpn-killswitch] {tunnel_id}: auto-cycling tunnel "
            f"(attempt #{_attempt_count(tunnel_id)})\n")
        if not _cycle_is_current(tunnel_id, token, stop_event):
            return
        with _state_lock:
            cycling_version = _transition_versions.get(tunnel_id)
        if cycling_version is None:
            return
        _fire_transition_callbacks(
            tunnel_id, "cycling", cycling_version)
        if not _cycle_is_current(tunnel_id, token, stop_event):
            return
        try:
            ok = cycle_tunnel(tunnel_id)
        except Exception as e:
            sys.stderr.write(f"[vpn-killswitch] cycle raised: {e}\n")
            ok = False
        if not _cycle_is_current(tunnel_id, token, stop_event):
            return
        if not ok:
            sys.stderr.write(
                f"[vpn-killswitch] {tunnel_id}: cycle FAILED, "
                "holding killed\n")
            with _cycle_registry_lock:
                if _cycle_current.get(tunnel_id) is not token:
                    return
                with _state_lock:
                    state = _states.get(tunnel_id)
                    if state is None or state.state != "cycling":
                        return
                    state.state = "killed"
                    failed_version = _next_transition_locked(tunnel_id)
            _fire_transition_callbacks(
                tunnel_id, "killed", failed_version)
    # If cycle succeeded, the next leak-probe pass auto-clears via
    # notify_leak_test_result's auto-clear logic.


def _attempt_count(tunnel_id: str) -> int:
    with _state_lock:
        s = _states.get(tunnel_id)
        return s.cycle_attempts if s else 0


def _stop_cycle_workers_locked(timeout: float) -> bool:
    """Invalidate, cancel, and boundedly quiesce every cycle generation.

    Caller holds ``_cycle_lifecycle_lock``.  Identity invalidation comes first,
    so even a worker that misses the join bound cannot act on later state.
    """
    global _cycle_epoch
    deadline = time.monotonic() + max(0.0, timeout)
    with _cycle_registry_lock:
        _cycle_epoch += 1
        records = list(_cycle_records.values())
        tunnel_ids = {record[0] for record in records}
        for _tunnel_id, _thread, stop_event in records:
            stop_event.set()
        _cycle_current.clear()

    # Establish that no old worker is inside the real cycle/callback action.
    remaining = max(0.0, deadline - time.monotonic())
    action_quiesced = _cycle_action_lock.acquire(timeout=remaining)
    if action_quiesced:
        _cycle_action_lock.release()

    current_thread = threading.current_thread()
    for _tunnel_id, thread, _stop_event in records:
        if thread is current_thread:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            if thread.is_alive():
                thread.join(timeout=remaining)
        except Exception:
            pass

    with _state_lock:
        for tunnel_id in tunnel_ids:
            state = _states.get(tunnel_id)
            if state is not None and state.state == "cycling":
                state.state = "killed"
                _next_transition_locked(tunnel_id)
    with _cycle_registry_lock:
        survivors = [
            thread for _tid, thread, _stop in _cycle_records.values()
            if thread.is_alive()
        ]
    return action_quiesced and not survivors


def shutdown(timeout: float = CYCLE_STOP_TIMEOUT_S) -> bool:
    """Cancel and boundedly quiesce all pending auto-cycle generations."""
    with _cycle_lifecycle_lock:
        return _stop_cycle_workers_locked(timeout)


def _cycle_generation_quiesced() -> bool:
    """Whether no tracked auto-cycle generation remains live."""
    with _cycle_registry_lock:
        return not any(
            thread.is_alive()
            for _tid, thread, _stop in _cycle_records.values()
        )


# ─── Test/introspection helpers ─────────────────────────────────────

def _reset_for_tests() -> None:
    global _auto_recover_enabled
    with _cycle_lifecycle_lock:
        if not _stop_cycle_workers_locked(CYCLE_STOP_TIMEOUT_S):
            raise RuntimeError(
                "VPN kill-switch auto-cycle survived the test-reset bound")
        with _state_lock:
            _states.clear()
            _transition_versions.clear()
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
    "shutdown",
]
