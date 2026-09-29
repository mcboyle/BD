"""dl95-app-B6-4: Home dashboard cards must not fly in on top of one another.

Measured live on test2 (build 3.66.1710, 2026-09-29): on a second SPA visit
to Home every react-grid-layout item's first frame sits at the container
origin and then animates out to its slot over 200ms; the frame 141ms in
matches the tester's overlapping screenshots. The cause is the library's own
mount sequence (react-grid-layout 1.5.3): an item is styled with top/left
before ``mounted`` and with ``transform: translate(...)`` plus the
``cssTransforms`` class after it, so once the browser has styled the
pre-mount frame the transform transitions from ``none``.

These tests replay that exact style swap in real Chromium against the real
library stylesheet (tests/fixtures, copied from the locked 1.5.3 package) and
the repo's own ``.dashboard-grid`` rules from frontend/src/index.css.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_RGL_VERSION = "1.5.3"
_RGL_CSS = (Path(__file__).resolve().parent / "fixtures"
            / "react_grid_layout_1_5_3_styles.css")

# The final slot of the "now-running" tile on a 1232px-wide grid (x=0, y=7).
_FINAL_X, _FINAL_Y = 0, 294
_SECOND_COL_X = 622


def _dashboard_grid_rules() -> str:
    src = (_REPO / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    rules = re.findall(r"(?m)^(\.dashboard-grid[^{}]*)\{([^{}]*)\}", src)
    assert rules, "no top-level .dashboard-grid rules found in index.css"
    return "\n".join(f"{sel}{{{body}}}" for sel, body in rules)


def _page(grid_classes: str, x: int) -> str:
    return (
        "<!doctype html><html><head><style>"
        + _RGL_CSS.read_text(encoding="utf-8")
        + _dashboard_grid_rules()
        + "body{margin:0}</style></head><body>"
        f'<div class="{grid_classes}">'
        '<div class="react-grid-layout" id="grid" '
        'style="position:relative;width:1232px;height:800px">'
        # Pre-mount style, exactly GridItem.createStyle with usePercentages.
        f'<div class="react-grid-item" id="item" style="top:{_FINAL_Y}px;'
        f'left:{x / 1232:.4%};width:{610 / 1232:.4%};height:240px;'
        'position:absolute">'
        "</div></div></div></body></html>"
    )


# Swap to the post-mount style (utils.setTransform) after the pre-mount frame
# has been styled, then read where the item is drawn 50ms later.
_MOUNT_AND_MEASURE = """async ([x, y]) => {
  const grid = document.getElementById('grid');
  const item = document.getElementById('item');
  item.getBoundingClientRect();
  item.className = 'react-grid-item cssTransforms';
  item.setAttribute('style',
    `transform:translate(${x}px,${y}px);width:610px;height:240px;position:absolute`);
  await new Promise(r => setTimeout(r, 50));
  const g = grid.getBoundingClientRect(), i = item.getBoundingClientRect();
  return [Math.round(i.x - g.x), Math.round(i.y - g.y)];
}"""


def _drawn_after_mount(grid_classes: str, x: int, y: int):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                    reduced_motion="no-preference")
            page.set_content(_page(grid_classes, x))
            return page.evaluate(_MOUNT_AND_MEASURE, [x, y])
        finally:
            browser.close()


def test_dl95_b6_4_fixture_is_the_locked_library_stylesheet():
    lock = json.loads((_REPO / "frontend" / "package-lock.json")
                      .read_text(encoding="utf-8"))
    locked = lock["packages"]["node_modules/react-grid-layout"]["version"]
    assert locked == _RGL_VERSION, (
        f"react-grid-layout is locked at {locked}; refresh "
        f"{_RGL_CSS.name} from that package and re-run these tests")
    assert ".react-grid-item.cssTransforms" in _RGL_CSS.read_text(
        encoding="utf-8")


@pytest.mark.parametrize("x", [_FINAL_X, _SECOND_COL_X])
def test_dl95_b6_4_view_mode_tile_is_in_its_slot_on_mount(x):
    drawn = _drawn_after_mount("dashboard-grid", x, _FINAL_Y)
    assert drawn == [x, _FINAL_Y], (
        f"DL95-B6-4: view-mode grid tile drawn at {drawn} 50ms after mount, "
        f"not its slot {[x, _FINAL_Y]} -- the mount transition flies cards "
        f"out of the container origin over one another")


def test_dl95_b6_4_edit_mode_keeps_the_drag_animation():
    # Negative control: the same swap under edit mode still animates, so the
    # probe can see a transition and the fix is scoped to view mode.
    drawn = _drawn_after_mount("dashboard-grid dashboard-grid-edit",
                               _SECOND_COL_X, _FINAL_Y)
    assert drawn[1] < _FINAL_Y - 20, (
        f"edit-mode tile already settled at {drawn}; the probe cannot "
        f"observe the transition it guards against")
