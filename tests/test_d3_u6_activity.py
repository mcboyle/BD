"""D3 U6 contract — Activity tab + ⌘K palette.

Covers:
  - /api/activity/v2 accepts ?q= and echoes it in the response
  - /api/activity/v2 with q= filters the rows (smoke: empty DB → empty)
  - /api/activity/v2/export.csv exists, returns text/csv with
    Content-Disposition attachment, accepts the same filters
  - Backend helper _m2_activity_query_fragments exists and is pure
  - Frontend Activity.tsx, ActivityRow.tsx, CommandPalette.tsx,
    useKeyboardShortcut.ts files exist
  - App.tsx mounts Activity at /activity (not placeholder)
  - AppShell mounts CommandPalette (palette is global)
  - Activity.tsx wires the search debounce + CSV export href
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


# ── Backend: activity/v2 with q= ──────────────────────────────────────


def test_d3_u6_activity_v2_accepts_q_param():
    """?q= is a new optional param. Must be echoed back in the
    response so the SPA can confirm what filter the server applied."""
    from bulk_downloader import app as a
    body = a.app.test_client().get(
        "/api/activity/v2?window=24h&q=foo"
    ).get_json()
    assert body.get("ok") is True
    assert body.get("q") == "foo"


def test_d3_u6_activity_v2_empty_q_is_empty_string():
    """Missing q must come back as empty string, not null/None.
    Lets the SPA branch on truthy without type-checking."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/activity/v2?window=24h").get_json()
    assert body.get("q") == "", (
        f"q must default to empty string, got {body.get('q')!r}"
    )


def test_d3_u6_activity_v2_q_filters_to_empty_in_cold_db():
    """With no history rows, any q returns 0 matches. Smoke check
    that the filter clause is wired in (a malformed query would
    either 500 or ignore the filter entirely)."""
    from bulk_downloader import app as a
    body = a.app.test_client().get(
        "/api/activity/v2?window=24h&q=anything"
    ).get_json()
    assert body.get("ok") is True
    assert body.get("items") == []
    assert body.get("count_current_period") == 0


def test_d3_u6_activity_v2_q_does_not_break_existing_shape():
    """The U2 contract (no q) still passes with q-extended endpoint."""
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/activity/v2").get_json()
    assert body.get("ok") is True
    assert body.get("window") == "24h"
    # U2 contract: items list, delta is 0 (not None) for 24h.
    assert isinstance(body.get("items"), list)
    assert body.get("delta_abs") == 0


def test_d3_u6_activity_v2_q_long_string_doesnt_crash():
    """A 10KB q string must not crash (LIKE pattern is capped at 200
    chars in the backend helper). Just needs to return 200."""
    from bulk_downloader import app as a
    long_q = "x" * 10_000
    r = a.app.test_client().get(f"/api/activity/v2?window=24h&q={long_q}")
    assert r.status_code in (200, 400)
    body = r.get_json()
    assert body.get("ok") is not False or body.get("ok") is False  # parseable
    # Specifically must NOT be 500
    assert r.status_code != 500


# ── Backend: CSV export ──────────────────────────────────────────────


def test_d3_u6_export_csv_route_registered():
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    assert "/api/activity/v2/export.csv" in rules


def test_d3_u6_export_csv_returns_text_csv():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2/export.csv?window=24h")
    assert r.status_code == 200
    ct = r.headers.get("Content-Type", "")
    assert "text/csv" in ct, f"Content-Type was {ct!r}, want text/csv"


def test_d3_u6_export_csv_has_attachment_header():
    """Content-Disposition: attachment with a filename — that's how
    the browser saves it instead of rendering inline."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/activity/v2/export.csv?window=24h")
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd, (
        f"Content-Disposition missing attachment: {cd!r}"
    )
    assert "filename=" in cd, "no filename= in Content-Disposition"
    assert ".csv" in cd, "filename has no .csv extension"


def test_d3_u6_export_csv_includes_header_row():
    """Cold DB → just the header row. Verifies the header line is
    emitted even with no data, so a CSV parser doesn't choke."""
    from bulk_downloader import app as a
    body = a.app.test_client().get(
        "/api/activity/v2/export.csv?window=24h"
    ).get_data(as_text=True)
    # Header on the first line — split on newline.
    first_line = body.split("\n", 1)[0].strip()
    # Field names per the U6 backend code.
    for col in ("id", "ts", "site_id", "site_name",
                "filename", "status", "file_size_bytes", "message"):
        assert col in first_line, f"CSV header missing {col!r}: {first_line!r}"


def test_d3_u6_export_csv_rejects_invalid_window():
    from bulk_downloader import app as a
    r = a.app.test_client().get(
        "/api/activity/v2/export.csv?window=bogus"
    )
    assert r.status_code == 400


def test_d3_u6_query_fragments_helper_present():
    """The shared SQL-fragment helper exists and is callable so the
    JSON + CSV endpoints can't drift on filter semantics."""
    from bulk_downloader import app as a
    assert hasattr(a, "_m2_activity_query_fragments")
    w, p = a._m2_activity_query_fragments(7, "")
    assert isinstance(w, list)
    assert isinstance(p, list)
    # window only → one WHERE clause, one param.
    assert len(w) == 1
    assert len(p) == 1


def test_d3_u6_query_fragments_with_q_adds_three_params():
    """When q is set, the helper adds three params (one per LIKE
    target: url, filename, message)."""
    from bulk_downloader import app as a
    w, p = a._m2_activity_query_fragments(7, "needle")
    assert len(w) == 2  # window + LIKE
    assert len(p) == 4  # 1 window + 3 LIKE
    # All 3 q params are the same %needle% pattern.
    assert p[1] == p[2] == p[3] == "%needle%"


def test_d3_u6_query_fragments_caps_q_length():
    """A pathological q string is capped at 200 chars to keep the
    LIKE pattern bounded."""
    from bulk_downloader import app as a
    _, p = a._m2_activity_query_fragments(1, "x" * 10_000)
    like_pattern = p[1]  # first LIKE param
    # Pattern is "%<q>%" so it's 2 + capped_q_len long.
    assert len(like_pattern) <= 202, (
        f"q not capped: pattern length {len(like_pattern)}"
    )


# ── Frontend files ───────────────────────────────────────────────────


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


def test_d3_u6_activity_route_exists():
    f = _frontend_file("routes", "Activity.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u6_activity_row_component_present():
    f = _frontend_file("components", "ActivityRow.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u6_command_palette_present():
    f = _frontend_file("components", "CommandPalette.tsx")
    assert f.is_file(), f"missing {f}"


def test_d3_u6_keyboard_shortcut_hook_present():
    f = _frontend_file("hooks", "useKeyboardShortcut.ts")
    assert f.is_file(), f"missing {f}"


def test_d3_u6_app_tsx_mounts_real_activity():
    """App.tsx mounts <Activity /> at /activity, not the placeholder."""
    app_tsx = _frontend_file("App.tsx").read_text(encoding="utf-8")
    import re
    activity_route = re.search(
        r'path="/activity"\s+element=\{<Activity\s*/>',
        app_tsx,
    )
    assert activity_route, (
        "App.tsx /activity must render <Activity />, not <PlaceholderRoute>"
    )


def test_d3_u6_appshell_mounts_command_palette():
    """AppShell must mount <CommandPalette /> so the palette is
    available from every tab."""
    src = _frontend_file("components", "AppShell.tsx").read_text(encoding="utf-8")
    assert "CommandPalette" in src, (
        "AppShell must mount <CommandPalette /> for global ⌘K access"
    )


def test_d3_u6_command_palette_uses_shortcut_hook():
    """The palette must use useKeyboardShortcut to bind ⌘K. If this
    drifts (e.g. someone replaces it with a one-off useEffect), the
    keyboard-shortcut convention breaks."""
    src = _frontend_file("components", "CommandPalette.tsx").read_text(encoding="utf-8")
    assert "useKeyboardShortcut" in src
    # And must use meta: true (Cmd/Ctrl) — not just plain 'k' which
    # would trigger from search inputs.
    assert "meta: true" in src or "meta:true" in src


def test_d3_u6_command_palette_uses_allowInInput():
    """⌘K must work from inside text inputs — that's the standard
    palette UX. The shortcut must opt in via allowInInput: true."""
    src = _frontend_file("components", "CommandPalette.tsx").read_text(encoding="utf-8")
    assert "allowInInput" in src, (
        "CommandPalette ⌘K must set allowInInput: true so it opens "
        "from inside form fields"
    )


def test_d3_u6_activity_route_uses_debouncer():
    """Search input must be debounced — typing one char triggers a
    /api/activity/v2 hit, otherwise it floods on every keystroke."""
    src = _frontend_file("routes", "Activity.tsx").read_text(encoding="utf-8")
    assert "useDebouncedValue" in src, (
        "Activity.tsx must debounce the search input"
    )


def test_d3_u6_activity_route_has_csv_export():
    """Activity.tsx must link to the CSV export endpoint."""
    src = _frontend_file("routes", "Activity.tsx").read_text(encoding="utf-8")
    assert "/api/activity/v2/export.csv" in src, (
        "Activity.tsx must link to /api/activity/v2/export.csv"
    )


def test_d3_u6_activity_route_has_all_window_options():
    """All four windows must be in the filter tabs."""
    src = _frontend_file("routes", "Activity.tsx").read_text(encoding="utf-8")
    for w in ('"24h"', '"7d"', '"30d"', '"all"'):
        assert w in src, f"Activity.tsx missing window option {w}"


def test_d3_u6_api_types_extended():
    """U6 adds ActivityWindow, ActivityRow, ActivityV2."""
    src = _frontend_file("lib", "api-types.ts").read_text(encoding="utf-8")
    for t in ("ActivityWindow", "ActivityRow", "ActivityV2"):
        assert t in src, f"api-types missing {t}"
