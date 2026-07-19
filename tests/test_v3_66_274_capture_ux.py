"""Capture-UX follow-ups (staged with probe mode):

Item A — collapsed sidebar is too wide. The 269 collapse rail is a fixed 64px; the
operator wants it to be basically just the icons. A named SIDEBAR_COLLAPSED_WIDTH
(48px) replaces the magic 64 and tightens the rail to an icon strip.

Item B — capture canvas bezels. The /capture page wrapped the canvas in generous
padding; tighten the outer padding + give the live canvas more height so the
embedded browser uses the space (the in-iframe black below the browser window is
the stash framebuffer/maximize, i.e. Lever 1 — not SPA, called out to the operator).

Item C — multi-row selector collection. bdPickSelector built a selector unique to
ONE element: for a video grid tile it lands on
`div.elastic-content-tile...[data-id="6011"]` — pinned to one video, useless as a
ROW selector that must match every tile. The pick now ALSO returns a GROUP
(repeating) selector (tag + stable classes, no id/data/nth) + its total and
VISIBLE match counts (the visible count exposes the 3x responsive-duplicate
inflation: an UltraFilms grid renders lg+md+mob copies). The row_selectors field
prefers the group selector and shows the match count, so the operator can see it
generalizes instead of guessing.

SPA-only + element_pick.py (a non-guard). Runtime pick is stash-only; tsc/vite +
source-scans are the in-sandbox ceiling.
"""
from __future__ import annotations

from pathlib import Path


def _shell() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "components"
            / "DesktopShell.tsx").read_text(encoding="utf-8")


def _layout() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "hooks"
            / "useUiLayout.ts").read_text(encoding="utf-8")


def _capture() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "routes"
            / "CaptureWorkflow.tsx").read_text(encoding="utf-8")


def _pick() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "bulk_downloader" / "element_pick.py").read_text(encoding="utf-8")


# ─── Item A: collapsed sidebar is an icon strip ──────────────────────────
def test_collapsed_sidebar_width_constant():
    # A named collapsed width, not a magic 64 in the JSX.
    lay = _layout()
    assert "SIDEBAR_COLLAPSED_WIDTH" in lay
    assert "SIDEBAR_COLLAPSED_WIDTH = 48" in lay


def test_shell_uses_collapsed_width_constant():
    sh = _shell()
    assert "SIDEBAR_COLLAPSED_WIDTH" in sh
    # the old magic 64 collapse width is gone.
    assert "collapsed ? 64" not in sh


# ─── Item B: canvas bezels tightened ─────────────────────────────────────
def test_capture_canvas_height_bumped():
    # The live canvas gets more height (less dead frame around the browser).
    cap = _capture()
    assert "min-h-[82vh]" in cap


# ─── Item C: row/group selector ──────────────────────────────────────────
def test_pick_emits_group_selector_and_counts():
    src = _pick()
    assert "group_selector" in src
    assert "group_count" in src
    assert "group_visible" in src


def test_pick_group_counts_visible_matches():
    # The visible-match count is what exposes the responsive-duplicate inflation.
    src = _pick()
    assert "countVisible" in src or "group_visible" in src


def test_spa_pickresult_carries_group():
    cap = _capture()
    assert "group_selector" in cap


def test_spa_row_field_prefers_group_selector():
    # For the multi (row) field the collected value is the GROUP selector when
    # present, not the unique-to-one selector.
    cap = _capture()
    assert "group_selector" in cap
    # the toast/label surfaces how many rows it matches.
    assert "group_count" in cap or "group_visible" in cap
