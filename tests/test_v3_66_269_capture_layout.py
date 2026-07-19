"""Capture-workflow layout — collapsible/resizable sidebar, resizable rail,
fluid noVNC canvas (v3.66.269).

The operator's `/capture` page was width-capped (DesktopShell `<main>` max-w-7xl
+ CaptureWorkflow `max-w-6xl`) with a fixed 400px rail and a fixed 224px (w-56)
sidebar, so the embedded noVNC canvas was a fixed ~700px regardless of monitor
size. 269 makes the layout adjustable:

  • the DesktopShell sidebar can collapse to an icon rail AND be drag-resized,
  • the CaptureWorkflow "Inspect & refine" rail can be drag-resized,
  • the capture page opts into a `wide` shell (no max-w cap) and the canvas
    column is fluid, so the noVNC iframe auto-fills whatever space is freed.

These are source-structure pins (same idiom as test_capture_iframe_clipboard):
the SPA's full behavior is proven separately by tsc + vite + a Playwright
bounding-box interaction run, but jsdom unit tests can't run in the sandbox, so
we pin the load-bearing structural markers here.

RED on pristine v3.66.268 (proven before implementing): none of the markers
(useSidebarLayout / useCaptureRailWidth wiring, the resize handles, the `wide`
opt-in) exist, and CaptureWorkflow still carries max-w-6xl.
GREEN after the 269 layout lands.

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FE = REPO / "frontend" / "src"
DESKTOP = FE / "components" / "DesktopShell.tsx"
APPSHELL = FE / "components" / "AppShell.tsx"
CAPTURE = FE / "routes" / "CaptureWorkflow.tsx"
HOOK = FE / "hooks" / "useUiLayout.ts"


def _read(p: Path) -> str:
    assert p.exists(), f"missing source file: {p}"
    return p.read_text(encoding="utf-8")


# ── persistence hook ──────────────────────────────────────────────────────────
def test_ui_layout_hook_exists_with_storage_keys():
    src = _read(HOOK)
    for needle in (
        "useSidebarLayout",
        "useCaptureRailWidth",
        '"bd-sidebar-collapsed"',
        '"bd-sidebar-width"',
        '"bd-capture-rail-width"',
    ):
        assert needle in src, f"useUiLayout.ts missing {needle}"
    # clamped on read+write so a stale entry can't push a pane off-screen.
    assert "clamp(" in src, "rail/sidebar widths must be clamped"


# ── DesktopShell: collapsible + resizable sidebar + wide opt-in ────────────────
def test_desktop_shell_wires_sidebar_layout_hook():
    src = _read(DESKTOP)
    assert "useSidebarLayout" in src, "DesktopShell must consume useSidebarLayout"


def test_desktop_shell_has_collapse_toggle():
    src = _read(DESKTOP)
    assert 'data-testid="sidebar-collapse-toggle"' in src, (
        "DesktopShell must expose a collapse/expand toggle "
        '(data-testid="sidebar-collapse-toggle")'
    )


def test_desktop_shell_has_sidebar_resize_handle():
    src = _read(DESKTOP)
    assert 'data-testid="sidebar-resize-handle"' in src, (
        "DesktopShell must expose a sidebar drag-resize handle "
        '(data-testid="sidebar-resize-handle")'
    )
    # the handle drives a drag — pointer wiring, not a static element.
    assert "onPointerMove" in src, "sidebar resize handle needs onPointerMove drag wiring"
    assert "setPointerCapture" in src, "drag should use setPointerCapture for robustness"


def test_desktop_shell_drops_width_cap_when_wide():
    src = _read(DESKTOP)
    assert "wide?: boolean" in src, "DesktopShell must accept a `wide?: boolean` prop"
    # wide drops the max-w cap so an opted-in page (capture) fills the column.
    assert "max-w-none" in src, (
        "DesktopShell `<main>` must use max-w-none when wide is set"
    )
    # the fixed `w-56` sidebar width must no longer be hard-coded (now dynamic).
    assert "w-56" not in src, (
        "sidebar width must be dynamic (collapsed icon-rail vs persisted width), "
        "not the hard-coded w-56"
    )


# ── AppShell: forwards `wide` to DesktopShell ──────────────────────────────────
def test_appshell_forwards_wide():
    src = _read(APPSHELL)
    # specific (not the bare substring "wide", which appears in comments like
    # "wider content"): the prop must be declared AND forwarded to DesktopShell.
    assert "wide?: boolean" in src, "AppShell must declare a `wide?: boolean` prop"
    assert "wide={wide}" in src, "AppShell must forward `wide` to DesktopShell"


# ── CaptureWorkflow: resizable rail + fluid canvas + wide ──────────────────────
def test_capture_uses_rail_width_hook():
    src = _read(CAPTURE)
    assert "useCaptureRailWidth" in src, "CaptureWorkflow must consume useCaptureRailWidth"


def test_capture_has_rail_resize_handle():
    src = _read(CAPTURE)
    assert 'data-testid="rail-resize-handle"' in src, (
        "CaptureWorkflow must expose a rail drag-resize handle "
        '(data-testid="rail-resize-handle")'
    )
    assert "onPointerMove" in src, "rail resize handle needs onPointerMove drag wiring"


def test_capture_rail_track_is_variable_and_canvas_fluid():
    src = _read(CAPTURE)
    # the rail grid track is now a CSS var driven by the hook, not a fixed 400px.
    assert "var(--rail" in src, "rail grid track must be var(--rail), driven by the hook"
    assert "minmax(0,1fr)" in src, "canvas column must stay fluid (minmax(0,1fr))"


def test_capture_opts_into_wide_and_drops_max_w_cap():
    src = _read(CAPTURE)
    assert "wide" in src, "CaptureWorkflow must pass `wide` to AppShell"
    # the old hard page cap is gone so the canvas can use the full column.
    assert "max-w-6xl" not in src, (
        "CaptureWorkflow must drop max-w-6xl so the canvas fills the wide column"
    )
