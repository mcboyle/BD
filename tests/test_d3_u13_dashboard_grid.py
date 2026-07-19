"""D3 follow-up U13 contract — react-grid-layout configurable dashboard.

Covers:
  - react-grid-layout + react-resizable + @types/react-grid-layout
    declared in frontend/package.json.
  - useDashboardLayout hook exists, defines WIDGET_IDS, exports
    layouts + setLayouts + reset + isCustom.
  - Uses localStorage key bd-dashboard-layout.
  - Validates the persisted shape (drops corrupt JSON / wrong shape).
  - Cross-tab sync via the storage event.
  - Reconciles a stored layout against the canonical WIDGET_IDS list
    (drops unknown, appends missing).
  - Home.tsx uses Responsive grid with WidthProvider, gates edit
    via isDraggable/isResizable, uses a draggableHandle (so taps on
    tile bodies don't initiate drags).
  - Home.tsx imports the react-grid-layout CSS.
  - Layout only persists while editMode is true (so transient
    react-grid-layout positioning computations on mount don't
    overwrite the stored layout).
  - index.css has the U4 grid overrides.
"""
from __future__ import annotations

import json
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


def _read_pkg_json() -> dict:
    return json.loads(
        (_REPO / "frontend" / "package.json").read_text(encoding="utf-8")
    )


# ── Dependencies ────────────────────────────────────────────────────


def test_u13_grid_layout_deps_declared():
    pkg = _read_pkg_json()
    deps = pkg.get("dependencies") or {}
    for name in ("react-grid-layout", "react-resizable"):
        assert name in deps, f"{name} missing from frontend/package.json deps"
    dev = pkg.get("devDependencies") or {}
    assert "@types/react-grid-layout" in dev, (
        "@types/react-grid-layout missing from devDependencies — "
        "TS build will warn without it"
    )


# ── useDashboardLayout hook ─────────────────────────────────────────


def test_u13_dashboard_layout_hook_exists():
    f = _frontend_file("hooks", "useDashboardLayout.ts")
    assert f.is_file(), f"missing {f}"
    src = f.read_text(encoding="utf-8")
    assert "export function useDashboardLayout" in src
    assert "export const WIDGET_IDS" in src
    assert "export function defaultLayouts" in src


def test_u13_dashboard_layout_widget_ids_complete():
    """The five widgets that exist in the dashboard today must all be
    in the canonical list — otherwise a missing widget gets stuck
    without a layout slot."""
    src = _frontend_file("hooks", "useDashboardLayout.ts").read_text(encoding="utf-8")
    m = re.search(
        r"export const WIDGET_IDS\s*=\s*\[(.*?)\]\s*as const",
        src,
        re.DOTALL,
    )
    assert m, "WIDGET_IDS constant not found"
    ids = {x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()}
    expected = {"attention", "today", "throughput", "now-running", "by-site"}
    assert ids == expected, f"WIDGET_IDS mismatch: {ids} vs {expected}"


def test_u13_dashboard_layout_uses_localstorage_key():
    src = _frontend_file("hooks", "useDashboardLayout.ts").read_text(encoding="utf-8")
    assert '"bd-dashboard-layout"' in src, (
        "useDashboardLayout must use the bd-dashboard-layout key"
    )


def test_u13_dashboard_layout_validates_persisted_shape():
    """A corrupt or wrong-shape JSON blob in localStorage must NOT
    crash the dashboard — it should fall back to defaults."""
    src = _frontend_file("hooks", "useDashboardLayout.ts").read_text(encoding="utf-8")
    # Must have a try/catch around JSON.parse and a shape validator.
    assert "JSON.parse" in src
    assert "validate" in src.lower() or "Array.isArray" in src, (
        "useDashboardLayout must validate the persisted shape"
    )
    # The catch must produce null, not let the exception escape.
    assert re.search(r"catch\s*\{[^}]*return null", src, re.DOTALL), (
        "useDashboardLayout JSON parse must catch and return null"
    )


def test_u13_dashboard_layout_cross_tab_sync():
    src = _frontend_file("hooks", "useDashboardLayout.ts").read_text(encoding="utf-8")
    assert 'addEventListener("storage"' in src
    assert 'removeEventListener("storage"' in src


def test_u13_dashboard_layout_reconciles_unknown_widgets():
    """If localStorage has a stored layout from a future version
    (or a deleted widget), the hook must reconcile against
    WIDGET_IDS — drop unknown entries, append missing ones."""
    src = _frontend_file("hooks", "useDashboardLayout.ts").read_text(encoding="utf-8")
    assert "function reconcile" in src or "const reconcile" in src, (
        "useDashboardLayout must reconcile stored layout against WIDGET_IDS"
    )


# ── Home/Dashboard route ────────────────────────────────────────────


def test_u13_home_uses_responsive_grid_layout():
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    assert "Responsive" in src and "WidthProvider" in src, (
        "Home.tsx must use Responsive + WidthProvider from react-grid-layout"
    )
    assert "ResponsiveGridLayout" in src or "WidthProvider(Responsive)" in src


def test_u13_home_imports_react_grid_layout_css():
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    assert 'import "react-grid-layout/css/styles.css"' in src
    assert 'import "react-resizable/css/styles.css"' in src


def test_u13_home_uses_dashboard_layout_hook():
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    assert "useDashboardLayout" in src
    assert "WIDGET_IDS" in src


def test_u13_home_edit_mode_gates_drag_and_resize():
    """Drag and resize must require explicit edit mode — outside
    edit mode the dashboard is fixed. This is the explicit U4
    discipline (no accidental drags while scrolling on touch)."""
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    # The props on ResponsiveGridLayout bind to editMode.
    assert "isDraggable={editMode}" in src, (
        "isDraggable must be gated by editMode"
    )
    assert "isResizable={editMode}" in src, (
        "isResizable must be gated by editMode"
    )


def test_u13_home_uses_draggable_handle():
    """draggableHandle must be set so tile bodies don't initiate
    drags — only the explicit grip does. Matches the U2 dnd-kit
    pattern."""
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    assert 'draggableHandle=".dashboard-tile-handle"' in src
    # And the handle div must carry that class.
    assert "dashboard-tile-handle" in src


def test_u13_home_layout_only_persists_in_edit_mode():
    """onLayoutChange fires constantly (including on the initial
    mount with computed positions). Persisting unconditionally
    would overwrite the stored layout with a transient state."""
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    cb = re.search(
        r"const onLayoutChange\s*=\s*\([^)]*\)\s*=>\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert cb, "onLayoutChange handler not found"
    body = cb.group(1)
    assert "if (!editMode) return" in body, (
        "onLayoutChange must short-circuit when not in edit mode"
    )


def test_u13_home_renders_reset_button_in_edit_mode():
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    # Reset button rendered conditionally on editMode.
    assert "editMode &&" in src and "reset" in src
    assert "Reset" in src, "Reset Layout button must be labelled"


def test_u13_home_edit_button_aria_pressed():
    src = _frontend_file("routes", "Home.tsx").read_text(encoding="utf-8")
    assert "aria-pressed={editMode}" in src, (
        "Edit toggle must expose aria-pressed for screen readers"
    )


# ── CSS overrides ──────────────────────────────────────────────────


def test_u13_index_css_has_grid_overrides():
    src = _frontend_file("index.css").read_text(encoding="utf-8")
    assert ".dashboard-grid" in src, (
        "index.css must contain U4 dashboard-grid overrides"
    )
    # The placeholder must use a design token, not the default
    # vanilla react-grid-layout red/blue.
    assert "var(--primary" in src and "react-grid-placeholder" in src, (
        "react-grid-placeholder override must use design tokens"
    )
