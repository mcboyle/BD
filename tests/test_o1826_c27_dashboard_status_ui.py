"""O1826 BRIEF-27: dashboard / status UI (TECH_DEBT M215, M225, M218, M200).

M215  Home's Reset button was disabled when the widget selection held 4 ids,
      but DEFAULT_WIDGET_IDS holds 5: Reset stayed live at the defaults and
      went dead for a custom 4-widget selection.
M225  SiteDetail carried a near-verbatim copy of Home's DashboardTile and the
      KPI tile builder; it now imports Home's.
M218  Maintenance's Status grab() turned every failed GET into null, so the
      panel could not tell an unreachable source from an empty one.
M200  Every SiteRow mounted ReadinessBadge, one readiness GET per row; rows
      now mount it once they are near the viewport.

M215/M225/M218/M200 run render-level in the tracked vitest spec below.
"""
from pathlib import Path

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
_SPEC = "src/routes/o1826_c27_dashboard_status_ui.test.tsx"


def test_dashboard_status_ui_vitest():
    receipt = run_vitest(_SPEC, expected_tests=9)
    expected = {
        "spec": _SPEC,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 9,
        "tests_collected": 9,
    }
    assert receipt == expected, (
        f"O1826-C27 vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )


def test_site_detail_reuses_home_tiles():
    site_detail = (_SRC / "routes" / "SiteDetail.tsx").read_text(encoding="utf-8")
    home = (_SRC / "routes" / "Home.tsx").read_text(encoding="utf-8")
    assert "export function DashboardTile(" in home, "M225: Home no longer exports DashboardTile"
    assert "export function buildKpiTiles(" in home, "M225: Home no longer exports buildKpiTiles"
    assert 'import { buildKpiTiles, DashboardTile } from "@/routes/Home";' in site_detail, (
        "M225: SiteDetail does not import Home's tile helpers"
    )
    for copy in ("function DashboardTile(", "<KPICard ", "Drag to reorder"):
        assert copy not in site_detail, f"M225: SiteDetail still carries its own copy ({copy!r})"
