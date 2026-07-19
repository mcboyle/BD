"""v3.43.41: Per-site download windows.

Gates worker start by time-of-day. Distinct from the existing
`bandwidth_schedule` feature which only varies the SPEED CAP during
day/night windows — this one gates whether workers run AT ALL
during configured hours.

Use cases this addresses:
  - Shared bandwidth living situations: "wowgirls workers must not
    run between 9am-6pm so my partner's video calls don't stutter"
  - ISP fair-use policy: "throttle starts at 200GB/day; pause
    overnight to spread the bill across calendar days"
  - Stash/Plex CPU contention: "scanning runs nightly at 3am, don't
    start new downloads then"

## Configuration

Three new per-site fields:

  - `window_enabled` (bool, default False) — opt-in
  - `window_active_hours` (str, e.g. "21:00-06:00,12:00-13:00") —
    comma-separated HH:MM-HH:MM ranges during which the site is
    ALLOWED to run. Outside these windows the runner is paused.
  - `window_action_outside` ("paused" | "stopped") — what state
    the runner enters outside the window. "paused" = no new work
    starts but in-flight downloads finish naturally; "stopped" =
    explicit stop() so even in-flight workers wind down.

## Time semantics

Times are local (host's timezone), parsed as HH:MM. Windows that
cross midnight are supported: `22:00-06:00` means 22:00 → next-day
06:00. Multiple windows on the same day are comma-separated.

## Interaction with the existing scheduler

The existing `_watch_done` thread auto-restarts workers when
retry timers fire. We hook into that path so a retry that arrives
outside the window doesn't bring the workers back. Same for
manual start() calls — checked against the window.

## Restart-survival

Window config persists in sites_config.json like everything else.
A site that's in window=outside state when the server starts
will boot into "paused" state, log a single event explaining why,
and resume when the next active window arrives.

## Why a separate module

The window check is consulted from multiple call sites:
  - SiteRunner.start() — gates initial / restart
  - SiteRunner._watch_done() — gates auto-retry restart
  - A new background scheduler thread that watches the clock and
    transitions sites in/out of paused state at window boundaries

Keeping the time parsing + window matching as a focused module
avoids duplicating the logic across those call sites.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional


def parse_hm(s: str, default_min: int = 0) -> int:
    """Parse 'HH:MM' to minutes-since-midnight. Returns `default_min`
    on parse failure or for non-string input. Defensive — a corrupted
    sites_config.json could have integer or None values here."""
    if not isinstance(s, str):
        return default_min
    try:
        parts = s.strip().split(":", 1)
        if len(parts) != 2:
            return default_min
        h, m = parts
        hi = int(h)
        mi = int(m)
        if 0 <= hi < 24 and 0 <= mi < 60:
            return hi * 60 + mi
    except (ValueError, TypeError):
        pass
    return default_min


def parse_windows(spec: str) -> list:
    """Parse a window spec like '21:00-06:00,12:00-13:00' into a
    list of (start_min, end_min) tuples. Empty or malformed input
    returns []. Each tuple's end_min may be less than start_min
    when the window crosses midnight — `in_window` handles that
    correctly.

    Examples:
      '09:00-17:00'           → [(540, 1020)]
      '22:00-06:00'           → [(1320, 360)]   # crosses midnight
      '08:00-12:00,14:00-18:00' → [(480, 720), (840, 1080)]
      '' or None              → []
    """
    if not isinstance(spec, str):
        return []
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start_s, _, end_s = part.partition("-")
        start_min = parse_hm(start_s.strip(), -1)
        end_min = parse_hm(end_s.strip(), -1)
        # parse_hm returns -1 sentinel only when we passed -1 default;
        # but here we pass -1 only in this function and want to detect
        # parse failures. Use -1 as the rejection signal.
        if start_min < 0 or end_min < 0:
            continue
        # Reject same-minute windows (zero-duration); they're almost
        # certainly user error.
        if start_min == end_min:
            continue
        out.append((start_min, end_min))
    return out


def in_window(windows: list, now: Optional[datetime] = None) -> bool:
    """True if the current time falls inside any of the configured
    windows. Empty window list → True (no restriction). Handles
    midnight-crossing windows correctly.

    `now` is for testability — production callers omit it and we
    use datetime.now() in local time."""
    if not windows:
        return True
    if now is None:
        now = datetime.now()
    cur = now.hour * 60 + now.minute
    for start_min, end_min in windows:
        if start_min < end_min:
            # Normal window (e.g. 09:00-17:00)
            if start_min <= cur < end_min:
                return True
        else:
            # Crosses midnight (e.g. 22:00-06:00 → cur >= 22:00 OR cur < 06:00)
            if cur >= start_min or cur < end_min:
                return True
    return False


def site_in_window(cfg: dict, now: Optional[datetime] = None) -> bool:
    """Top-level helper: given a site config, is the site currently
    in its active window? Returns True if window feature is disabled
    (no restriction) or if no windows are configured. Defensive on
    bad input — returns True (= no restriction) when in doubt rather
    than locking out the site permanently."""
    if not isinstance(cfg, dict):
        return True
    if not cfg.get("window_enabled"):
        return True
    spec = cfg.get("window_active_hours") or ""
    windows = parse_windows(spec)
    if not windows:
        # Window enabled but no valid spec — treat as no restriction
        # rather than "always blocked" (which is almost certainly
        # not what the user intended)
        return True
    return in_window(windows, now=now)


def next_transition_seconds(cfg: dict,
                              now: Optional[datetime] = None) -> Optional[int]:
    """How many seconds until the site's window state flips? Used by
    the background scheduler to wake up at the right moment instead
    of polling every minute.

    Returns:
      - Integer seconds until next transition (in-window → out, or
        out → in)
      - None when there's no transition (window disabled, no windows
        configured, or every minute is in-window like '00:00-23:59')

    Caps at 24h (86400) since windows repeat daily — a "next
    transition" further out than that doesn't make physical sense.
    """
    if not isinstance(cfg, dict):
        return None
    if not cfg.get("window_enabled"):
        return None
    spec = cfg.get("window_active_hours") or ""
    windows = parse_windows(spec)
    if not windows:
        return None
    if now is None:
        now = datetime.now()
    cur_min = now.hour * 60 + now.minute
    cur_sec_in_min = now.second
    currently_in = in_window(windows, now=now)
    # Build the set of all transition minutes (window starts + ends)
    transitions = set()
    for s, e in windows:
        transitions.add(s)
        transitions.add(e)
    # Find the smallest forward delta to any transition. Since
    # windows repeat daily, search the next 24h (1440 min).
    best_delta_min = 1440
    for t in transitions:
        delta = (t - cur_min) % 1440
        # delta=0 means we're AT the boundary right now — skip and
        # pick the next one (otherwise the scheduler would wake up
        # and immediately decide "same state" on every tick)
        if delta == 0:
            delta = 1440
        if delta < best_delta_min:
            best_delta_min = delta
    if best_delta_min >= 1440:
        return None
    # Convert minutes-to-next-boundary into seconds-to-next-boundary,
    # subtracting the seconds we've already consumed within the
    # current minute. Minimum 1s so a transition exactly at the next
    # minute still wakes us.
    seconds = best_delta_min * 60 - cur_sec_in_min
    return max(1, seconds)


# ── Background scheduler ──────────────────────────────────────────────

class WindowScheduler:
    """Single daemon thread that watches the clock and notifies
    runners when they should pause/resume based on their window
    configuration.

    Design choices:
      - One thread for ALL sites, not one per site. Sites at typical
        scale (5-50) don't justify thread-per-site, and the
        synchronization cost of a single thread is trivial.
      - Wakes at the next transition (via
        `next_transition_seconds`), not on a fixed interval. A site
        configured `21:00-06:00` has only 2 transitions per 24h
        — polling every minute wastes 1438 wake-ups.
      - Respects the BD_DISABLE_KEEPALIVE env gate (same as
        session_keeper) so tests don't spin up a real scheduler.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Callback the runner installs so we can transition state
        # without importing app.py / runner.py here (would create
        # circular imports during module load)
        self._transition_cb = None

    def set_transition_callback(self, cb):
        """Caller (app.py at startup) registers a function called
        as `cb(site_id, should_run)` whenever a site's window state
        changes. The callback handles the actual pause/resume."""
        with self._lock:
            self._transition_cb = cb

    def start(self):
        """Start the background daemon thread. Idempotent — calling
        twice is a no-op."""
        import os
        if os.environ.get("BD_DISABLE_KEEPALIVE"):
            # Tests / CI: don't spin up a scheduler thread
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True,
                name="bd-window-scheduler")
            self._thread.start()

    def stop(self):
        """Signal the thread to exit. The daemon flag means it'll
        also die at process shutdown."""
        self._stop.set()

    def _loop(self):
        """Main scheduler loop. Wakes up at the next state transition
        across all sites, fires the callback for any site whose
        in-window status changed, then sleeps until the next
        boundary."""
        import sys
        # Lazy import to avoid circular dependency
        try:
            from . import app as _app
        except Exception as e:
            sys.stderr.write(f"  window_scheduler: app import failed: {e}\n")
            return
        last_state: dict = {}  # site_id → bool (last known in_window)
        while not self._stop.is_set():
            try:
                cfg_snapshot = list(_app.s_cfg.items())
            except Exception:
                # App not ready yet — wait a moment and retry
                self._stop.wait(5.0)
                continue
            # Compute current state for every site with window_enabled
            now = datetime.now()
            next_wake = 60.0  # fallback if we can't compute boundaries
            for sid, cfg in cfg_snapshot:
                if not isinstance(cfg, dict):
                    continue
                if not cfg.get("window_enabled"):
                    # If the site had a state before but now is
                    # disabled, treat as "should run" (resume)
                    if sid in last_state and not last_state[sid]:
                        self._fire_transition(sid, True)
                    last_state.pop(sid, None)
                    continue
                cur_state = site_in_window(cfg, now=now)
                prev_state = last_state.get(sid)
                if prev_state is None:
                    # First observation. Don't fire — initial state
                    # is set by the start() path. Just record.
                    last_state[sid] = cur_state
                elif prev_state != cur_state:
                    self._fire_transition(sid, cur_state)
                    last_state[sid] = cur_state
                # Track when this site needs us back
                t = next_transition_seconds(cfg, now=now)
                if t is not None and t < next_wake:
                    next_wake = float(t)
            # Sleep until the next transition boundary, but wake up
            # often enough that config changes are picked up within
            # a reasonable time. 5-minute floor.
            sleep_for = max(5.0, min(next_wake, 3600.0))
            self._stop.wait(sleep_for)

    def _fire_transition(self, site_id: str, should_run: bool):
        """Invoke the registered callback. Defensive — a callback
        crash should not take down the scheduler."""
        cb = None
        with self._lock:
            cb = self._transition_cb
        if cb is None:
            return
        try:
            cb(site_id, should_run)
        except Exception as e:
            import sys
            sys.stderr.write(
                f"  window_scheduler: transition cb for {site_id} "
                f"raised: {type(e).__name__}: {e}\n")


# Module singleton
_SCHEDULER: Optional[WindowScheduler] = None
_SCHEDULER_LOCK = threading.Lock()


def get_scheduler() -> WindowScheduler:
    """Lazy singleton accessor."""
    global _SCHEDULER
    if _SCHEDULER is None:
        with _SCHEDULER_LOCK:
            if _SCHEDULER is None:
                _SCHEDULER = WindowScheduler()
    return _SCHEDULER
