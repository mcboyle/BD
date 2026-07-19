"""D3 follow-up U12 contract — Queue tab badge live-% toggle.

Covers:
  - useQueueBadgeMode hook exists, exports the mode + setter, uses
    the localStorage key bd-queue-badge-mode, and listens to the
    storage event for cross-tab sync.
  - aggregateRunningPct helper exists, clamps to [0,100], returns
    0 for an empty list, and handles non-finite progress values
    (NaN/undefined) without exploding.
  - BottomTabBar accepts the new props (queueBadgeMode,
    queueRunningPct, queueHasRunning) and falls back to the count
    dot when percent mode is selected but nothing is running.
  - AppShell wires the hook into BottomTabBar, sourcing the running
    progress array from the same queue-v2-badge query (no duplicate
    poll).
  - Settings.tsx renders a Count/Percent radiogroup under the
    Downloads section.
  - The pref is per-device — NOT routed through /api/global_config.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


# ── useQueueBadgeMode hook ──────────────────────────────────────────


def test_u12_queue_badge_mode_hook_exists():
    f = _frontend_file("hooks", "useQueueBadgeMode.ts")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    assert "export function useQueueBadgeMode" in src
    assert "export function aggregateRunningPct" in src
    assert "export type QueueBadgeMode" in src


def test_u12_queue_badge_mode_uses_localstorage_key():
    """Pin the storage key so a future rename in the hook is also
    surfaced in the Settings page that has to use the same key."""
    src = _frontend_file("hooks", "useQueueBadgeMode.ts").read_text(encoding="utf-8")
    assert '"bd-queue-badge-mode"' in src, (
        "useQueueBadgeMode must use the bd-queue-badge-mode key"
    )


def test_u12_queue_badge_mode_listens_to_storage_event():
    """Cross-tab sync: when Settings toggles the pref in one tab,
    other tabs' BottomTabBar should update without reload."""
    src = _frontend_file("hooks", "useQueueBadgeMode.ts").read_text(encoding="utf-8")
    assert 'addEventListener("storage"' in src, (
        "useQueueBadgeMode must subscribe to the storage event"
    )
    assert 'removeEventListener("storage"' in src, (
        "useQueueBadgeMode must clean up its storage listener"
    )


def test_u12_aggregate_running_pct_handles_empty_and_nan():
    """The helper is a pure function — invoke it through Node-style
    eval would require a JS runtime; instead, pin the defensive
    shape in source so refactors don't drop the empty/NaN guards."""
    src = _frontend_file("hooks", "useQueueBadgeMode.ts").read_text(encoding="utf-8")
    # Empty list → 0
    assert "progressList.length === 0" in src and "return 0" in src, (
        "aggregateRunningPct must return 0 for empty list"
    )
    # Non-finite guard
    assert "Number.isFinite(p)" in src, (
        "aggregateRunningPct must guard against non-finite progress values"
    )
    # Clamp
    assert "Math.max(0, Math.min(100" in src, (
        "aggregateRunningPct must clamp to [0,100]"
    )


# ── BottomTabBar ────────────────────────────────────────────────────


def test_u12_bottom_tab_bar_accepts_new_props():
    src = _frontend_file("components", "BottomTabBar.tsx").read_text(encoding="utf-8")
    for prop in (
        "queueBadgeMode",
        "queueRunningPct",
        "queueHasRunning",
    ):
        assert prop in src, f"BottomTabBar must accept {prop} prop"


def test_u12_bottom_tab_bar_percent_falls_back_when_no_running():
    """Percent mode with nothing running must show the count dot,
    not a stale "0%" badge. Pin the fallback condition."""
    src = _frontend_file("components", "BottomTabBar.tsx").read_text(encoding="utf-8")
    # The `showPercent` derived flag must require all three:
    # mode=percent, has-running, badge>0.
    show = re.search(
        r"const showPercent\s*=\s*([^;]+);",
        src,
    )
    assert show, "BottomTabBar must derive a showPercent flag"
    body = show.group(1)
    assert "percent" in body
    assert "queueHasRunning" in body
    assert "queueBadge > 0" in body


def test_u12_bottom_tab_bar_percent_aria_label_includes_pct():
    src = _frontend_file("components", "BottomTabBar.tsx").read_text(encoding="utf-8")
    assert "${queueRunningPct}% downloading" in src, (
        "percent badge must have an aria-label that announces the percentage"
    )


# ── AppShell wiring ─────────────────────────────────────────────────


def test_u12_app_shell_uses_badge_mode_hook():
    src = _frontend_file("components", "AppShell.tsx").read_text(encoding="utf-8")
    assert "useQueueBadgeMode" in src, "AppShell must call useQueueBadgeMode"
    assert "aggregateRunningPct" in src, "AppShell must derive the aggregate %"


def test_u12_app_shell_reuses_badge_cache_key():
    """AppShell must not open a second poll for percent-mode data —
    the running[] progress comes from the SAME queue-v2-badge cache
    entry, retyped to QueueV2Full so .running has typed progress."""
    src = _frontend_file("components", "AppShell.tsx").read_text(encoding="utf-8")
    assert '"queue-v2-badge"' in src, (
        "AppShell must reuse the existing queue-v2-badge cache key"
    )
    # And the typing is QueueV2Full (so the running[] array carries the
    # progress field needed by aggregateRunningPct).
    assert "useQuery<QueueV2Full>" in src, (
        "AppShell must type the badge query as QueueV2Full"
    )


# ── Settings UI ─────────────────────────────────────────────────────


def test_u12_settings_renders_badge_mode_radiogroup():
    src = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    assert "useQueueBadgeMode" in src, "Settings.tsx must import the hook"
    assert "Queue tab badge" in src, "Settings.tsx must label the row"
    assert 'role="radiogroup"' in src, (
        "Settings.tsx must use role=radiogroup for the mode choice"
    )
    # Both options present (multi-line JSX, so match by surrounding context).
    assert re.search(r">\s*Count\s*<", src) and re.search(r">\s*Percent\s*<", src), (
        "Settings.tsx must render both Count and Percent buttons"
    )


def test_u12_settings_uses_setter_from_hook():
    src = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    assert "setQueueBadgeMode(" in src, (
        "Settings must wire the hook's setter to the buttons"
    )


# ── Per-device discipline ───────────────────────────────────────────


def test_u12_badge_mode_is_per_device_not_global_config():
    """The preference is per-device by design — same reasoning as
    bd-sound-on-complete. Pin that the key is NEVER passed through
    /api/global_config: a future "let's sync everything" refactor
    would be wrong by the same logic that kept sound per-device."""
    settings = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    # The badge mode must not appear inside the saveMut payload —
    # saveMut posts GlobalConfigSubset fields only.
    # Heuristic: find lines that POST to /api/global_config and check
    # they don't carry the badge-mode key.
    api_types = _frontend_file("lib", "api-types.ts").read_text(encoding="utf-8")
    assert "queue_badge_mode" not in api_types, (
        "GlobalConfigSubset must not include queue_badge_mode — "
        "the preference is per-device, not per-instance"
    )
    assert "queue_badge_mode" not in settings, (
        "Settings.tsx must not POST queue_badge_mode through global_config"
    )
