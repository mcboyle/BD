"""v3.66.688 (F6) — bg_scheduler idle scaling: back off when idle, wake on demand.

The coordinator polls on a fixed cadence. F6 makes that cadence adaptive:
when nothing has happened for a while it backs the poll interval off
(fewer wakeups = "scaling down" while idle), and an activity signal
(note_activity) both refreshes the active window AND wakes a sleeping loop
immediately (wake-on-demand). The idle interval never exceeds the fastest
registered task interval (300s), so tasks are not starved.

Pure policy is unit-tested; the wake path is tested deterministically via
the interruptible-wait helper (no thread timing / no flakiness).
"""
import threading
import time

from bulk_downloader import bg_scheduler as bg


# ── pure idle policy ────────────────────────────────────────────────

def test_idle_poll_interval_active_when_recent():
    now = 1000.0
    assert bg.idle_poll_interval(now - 5, now, idle_after=600,
                                 active_interval=30, idle_interval=300) == 30


def test_idle_poll_interval_idle_when_stale():
    now = 1000.0
    assert bg.idle_poll_interval(now - 700, now, idle_after=600,
                                 active_interval=30, idle_interval=300) == 300


def test_idle_poll_interval_boundary_is_idle():
    now = 1000.0
    # exactly at the threshold counts as idle (>=)
    assert bg.idle_poll_interval(now - 600, now, idle_after=600,
                                 active_interval=30, idle_interval=300) == 300


def test_idle_poll_interval_uses_module_defaults():
    now = time.time()
    assert bg.idle_poll_interval(now, now) == bg.ACTIVE_POLL_S
    assert bg.idle_poll_interval(now - bg.IDLE_AFTER_S - 1, now) == bg.IDLE_POLL_S
    # idle cadence must not exceed the fastest task interval (no starvation)
    assert bg.IDLE_POLL_S <= 300


# ── activity signal + wake ──────────────────────────────────────────

def test_note_activity_refreshes_and_wakes():
    bg._wake_event.clear()
    before = bg._last_activity
    time.sleep(0.01)
    bg.note_activity()
    assert bg._last_activity > before          # timer refreshed
    assert bg._wake_event.is_set()             # a sleeping loop would wake


def test_internal_mark_activity_refreshes_without_waking():
    bg._wake_event.clear()
    before = bg._last_activity
    time.sleep(0.01)
    bg._mark_activity()
    assert bg._last_activity > before
    assert not bg._wake_event.is_set()         # running a task must not self-wake


def test_wait_next_returns_immediately_when_woken():
    bg._wake_event.set()
    t0 = time.monotonic()
    woken = bg._wait_next(100)                  # would block 100s if not woken
    assert woken is True
    assert time.monotonic() - t0 < 1.0         # returned promptly
    assert not bg._wake_event.is_set()          # flag cleared after wait


def test_wait_next_times_out_when_not_woken():
    bg._wake_event.clear()
    woken = bg._wait_next(0.05)
    assert woken is False


def test_note_activity_wakes_a_blocked_waiter():
    bg._wake_event.clear()
    result = {}
    def waiter():
        result["woken"] = bg._wait_next(30)     # long wait
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.05)
    bg.note_activity()                          # wake it on demand
    th.join(timeout=2)
    assert result.get("woken") is True


# ── status surfaces idle state ──────────────────────────────────────

def test_status_reports_idle_fields():
    bg.note_activity()
    st = bg.status()
    assert "idle" in st and st["idle"] is False
    assert "poll_interval_seconds" in st
    assert st["poll_interval_seconds"] == bg.ACTIVE_POLL_S
    assert "seconds_since_activity" in st
