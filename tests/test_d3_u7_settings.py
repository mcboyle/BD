"""D3 U7 contract — Settings + Advanced + visible dark-mode toggle.

Covers:
  - Frontend Settings.tsx, Advanced.tsx routes exist
  - SettingSection, ThemeToggle components exist
  - useTheme hook exists and exports applyStoredThemeOnBoot
  - App.tsx mounts Settings at /settings (not placeholder)
  - App.tsx mounts Advanced at /settings/advanced (sub-route)
  - main.tsx calls applyStoredThemeOnBoot before render
  - PlaceholderRoute no longer mounted in App.tsx
  - Settings.tsx wires the global_config endpoint
  - Advanced.tsx reads /api/health/v2
  - index.css has the .dark token override (carried from U1)

No new backend endpoints in U7 — Settings reads/writes the existing
/api/global_config, Advanced reads /api/health/v2. The U7 contract
is purely about frontend wiring and the dark-mode infrastructure
becoming user-visible.
"""
from __future__ import annotations

import os
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


# ── Frontend file shape ──────────────────────────────────────────────


def test_d3_u7_settings_route_exists():
    f = _frontend_file("routes", "Settings.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u7_advanced_route_exists():
    f = _frontend_file("routes", "Advanced.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u7_setting_section_component_present():
    f = _frontend_file("components", "SettingSection.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u7_theme_toggle_component_present():
    f = _frontend_file("components", "ThemeToggle.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u7_use_theme_hook_present():
    f = _frontend_file("hooks", "useTheme.ts")
    assert f.is_file(), f"missing {f}"


def test_d3_u7_use_theme_exports_boot_helper():
    """applyStoredThemeOnBoot is the synchronous-on-import helper
    that main.tsx calls before render to avoid a light-flash on
    initial load. If it disappears the FOUC returns."""
    src = _frontend_file("hooks", "useTheme.ts").read_text(encoding="utf-8")
    assert "export function applyStoredThemeOnBoot" in src or \
           "export const applyStoredThemeOnBoot" in src, (
        "useTheme must export applyStoredThemeOnBoot for FOUC prevention"
    )


# ── App.tsx routing ──────────────────────────────────────────────────


def test_d3_u7_app_tsx_mounts_real_settings():
    """App.tsx mounts <Settings />, not <PlaceholderRoute>."""
    app_tsx = _frontend_file("App.tsx").read_text(encoding="utf-8")
    import re
    settings_route = re.search(
        r'path="/settings"\s+element=\{<Settings\s*/>',
        app_tsx,
    )
    assert settings_route, (
        "App.tsx /settings must render <Settings />, not <PlaceholderRoute>"
    )


def test_d3_u7_app_tsx_mounts_advanced_subroute():
    """App.tsx must mount /settings/advanced as a separate route."""
    app_tsx = _frontend_file("App.tsx").read_text(encoding="utf-8")
    import re
    adv_route = re.search(
        r'path="/settings/advanced"\s+element=\{<Advanced\s*/>',
        app_tsx,
    )
    assert adv_route, (
        "App.tsx must mount <Advanced /> at /settings/advanced"
    )


def test_d3_u7_app_tsx_no_placeholder_mounted():
    """U7 closes out the SPA pages — no route should mount
    PlaceholderRoute anymore. The file can stay in the tree (dead
    code), but it must not appear in App.tsx's route table."""
    app_tsx = _frontend_file("App.tsx").read_text(encoding="utf-8")
    assert "<PlaceholderRoute" not in app_tsx, (
        "App.tsx still mounts <PlaceholderRoute>; all 5 tabs should "
        "now have real implementations"
    )


# ── main.tsx boot wiring ─────────────────────────────────────────────


def test_d3_u7_main_tsx_calls_boot_helper():
    """main.tsx must call applyStoredThemeOnBoot() before
    ReactDOM.render so the first paint matches the stored theme."""
    src = _frontend_file("main.tsx").read_text(encoding="utf-8")
    assert "applyStoredThemeOnBoot" in src, (
        "main.tsx must import + call applyStoredThemeOnBoot to avoid FOUC"
    )
    # And the call must come BEFORE the render, not after.
    boot_idx = src.find("applyStoredThemeOnBoot()")
    render_idx = src.find("ReactDOM.createRoot")
    assert boot_idx > 0 and render_idx > 0, (
        "main.tsx is missing either the boot call or the render call"
    )
    assert boot_idx < render_idx, (
        "applyStoredThemeOnBoot() must be called BEFORE ReactDOM.createRoot"
    )


# ── Settings wiring ──────────────────────────────────────────────────


def test_d3_u7_settings_reads_global_config():
    """Settings.tsx must read /api/global_config to populate fields."""
    src = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    assert "/api/global_config" in src, (
        "Settings.tsx must GET /api/global_config for current state"
    )


def test_d3_u7_settings_uses_theme_toggle():
    """ThemeToggle must be mounted somewhere in Settings (it's the
    only place the user can switch theme outside ⌘K)."""
    src = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    assert "ThemeToggle" in src, "Settings.tsx must mount <ThemeToggle />"


def test_d3_u7_settings_links_to_advanced():
    """Settings.tsx must link to /settings/advanced — otherwise
    users can never reach it (it's not a tab)."""
    src = _frontend_file("routes", "Settings.tsx").read_text(encoding="utf-8")
    assert "/settings/advanced" in src, (
        "Settings.tsx must link to the Advanced sub-page"
    )


# ── Advanced wiring ──────────────────────────────────────────────────


def test_d3_u7_advanced_reads_health_v2():
    """Advanced.tsx is read-only over /api/health/v2."""
    src = _frontend_file("routes", "Advanced.tsx").read_text(encoding="utf-8")
    assert "/api/health/v2" in src, (
        "Advanced.tsx must read /api/health/v2 for diagnostics"
    )


def test_d3_u7_advanced_links_back_to_settings():
    """The back arrow on Advanced must link to /settings, not /."""
    src = _frontend_file("routes", "Advanced.tsx").read_text(encoding="utf-8")
    assert '"/settings"' in src or "'/settings'" in src, (
        "Advanced.tsx must have a back link to /settings"
    )


# ── ThemeToggle wiring ───────────────────────────────────────────────


def test_d3_u7_theme_toggle_offers_three_options():
    """The toggle must offer System / Light / Dark — System default
    matters because users who don't visit Settings still get the
    right palette via the OS preference."""
    src = _frontend_file("components", "ThemeToggle.tsx").read_text(encoding="utf-8")
    for opt in ('"system"', '"light"', '"dark"'):
        assert opt in src, f"ThemeToggle missing option {opt}"


def test_d3_u7_use_theme_persists_to_localstorage():
    """The toggle must persist across reloads via localStorage. If
    this drifts, the user's preference is lost on every refresh."""
    src = _frontend_file("hooks", "useTheme.ts").read_text(encoding="utf-8")
    assert "localStorage" in src, (
        "useTheme must persist mode to localStorage"
    )


def test_d3_u7_use_theme_listens_for_system_change():
    """When the user picks System mode, the SPA must listen for OS
    dark-mode toggles via matchMedia — otherwise toggling the OS
    setting doesn't update the SPA until reload."""
    src = _frontend_file("hooks", "useTheme.ts").read_text(encoding="utf-8")
    assert "matchMedia" in src and "prefers-color-scheme" in src, (
        "useTheme must subscribe to prefers-color-scheme for "
        "System-mode auto-switch"
    )


# ── Dark-mode CSS pairings (carried from U1, must still be present) ──


def test_d3_u7_index_css_has_dark_override():
    """The .dark CSS class with token overrides is what actually
    swaps the palette when the toggle flips. If U1's dark pairings
    are dropped, the toggle becomes a no-op."""
    src = _frontend_file("index.css").read_text(encoding="utf-8")
    assert ".dark" in src, (
        "index.css missing .dark override block — toggle would be no-op"
    )
    # Sanity: the dark block must override at least the base bg/ink.
    # Order is :root then .dark, both setting --bg.
    import re
    # Find ".dark {" block and check it contains --bg.
    m = re.search(r"\.dark\s*\{[^}]*--bg\s*:", src, re.DOTALL)
    assert m, (
        ".dark block must override --bg (the base background token)"
    )


# ── api-types extension ──────────────────────────────────────────────


def test_d3_u7_api_types_extended():
    """U7 adds ThemeMode, GlobalConfigSubset, HealthV2."""
    src = _frontend_file("lib", "api-types.ts").read_text(encoding="utf-8")
    for t in ("ThemeMode", "GlobalConfigSubset", "HealthV2"):
        assert t in src, f"api-types missing {t}"
