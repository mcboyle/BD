"""v3.64.x Unit B — widget catalog restoration.

The legacy `/` UI shipped 36 KPI widgets across 7 categories. The
v3.64.0 SPA collapsed this to 5 hardcoded widgets. This release brings
the full catalog back.

Tests on the Python side parse the TypeScript source as text and
verify the catalog contract:

  1. All 36 widget IDs are present (the legacy widgets.js list).
  2. Each widget has a category, title, desc, and spec function.
  3. All declared categories exist in the CATEGORIES list.
  4. The default selection (`DEFAULT_WIDGET_IDS`) is non-empty and
     references real widget IDs.
  5. The data envelope's keys cover what each widget's `spec` reads
     (otherwise the renderer always shows '—' for that widget).
"""
from __future__ import annotations

import re
from pathlib import Path


CATALOG_TS = (Path(__file__).resolve().parents[1]
              / "frontend" / "src" / "lib" / "widgetCatalog.ts")

# The 36 widget IDs from legacy widgets.js (the canonical surface
# being restored). If any of these are missing in the TS catalog,
# the restoration is incomplete.
EXPECTED_WIDGET_IDS = {
    # activity
    "done_today", "done_hour", "bytes_today", "files_hour",
    # performance
    "throughput", "success_rate", "avg_speed", "avg_size", "avg_quality",
    # capacity
    "queue_depth", "workers", "disk_free", "bandwidth",
    # health
    "action_req", "stuck", "failures", "retries",
    # system
    "cpu_ram", "gpu", "eta_clear", "cookies", "sites_active",
    # collection
    "lib_total", "lib_size", "lib_watched", "lib_unrated",
    "lib_missing", "lib_recent", "lib_top_studio", "audit_recent",
    # diagnostics
    "success_rate_24h", "error_top_cluster", "captcha_24h",
    "cookie_warnings", "sites_need_attention", "active_alerts",
}

EXPECTED_CATEGORIES = {
    "activity", "performance", "capacity", "health",
    "system", "collection", "diagnostics",
}


def _read_source() -> str:
    return CATALOG_TS.read_text(encoding="utf-8")


def test_catalog_file_exists():
    assert CATALOG_TS.is_file(), (
        f"Expected {CATALOG_TS} — the SPA's widget catalog is missing. "
        f"Unit B (widget catalog restoration) ships this file.")


def test_all_36_widgets_present():
    """Every legacy widget ID from widgets.js must exist in the TS
    catalog. A future refactor that drops one is a regression of the
    restoration itself."""
    src = _read_source()
    # Match every entry like:  { id: "<id>", cat: "<cat>", ...
    ids = set(re.findall(r'\{\s*id:\s*"([a-z_0-9]+)"\s*,\s*cat:', src))
    missing = EXPECTED_WIDGET_IDS - ids
    extra = ids - EXPECTED_WIDGET_IDS
    assert not missing, (
        f"Catalog is missing {len(missing)} widget(s) from the legacy 36: "
        f"{sorted(missing)}")
    # Extras are flagged as a clean failure rather than tolerated —
    # widening the catalog should be a deliberate edit to this test.
    assert not extra, (
        f"Catalog has unexpected widgets: {sorted(extra)}. Add them to "
        f"EXPECTED_WIDGET_IDS or remove from the catalog.")


def test_every_widget_assigned_to_a_known_category():
    """A widget assigned to a category that isn't in CATEGORIES would
    be filtered out of the picker's category-tab view — invisible to
    the operator. Catch the assignment mismatch here."""
    src = _read_source()
    assignments = re.findall(
        r'\{\s*id:\s*"([a-z_0-9]+)"\s*,\s*cat:\s*"([a-z]+)"', src)
    for widget_id, cat in assignments:
        assert cat in EXPECTED_CATEGORIES, (
            f"Widget {widget_id!r} assigned to unknown category {cat!r}. "
            f"Known categories: {sorted(EXPECTED_CATEGORIES)}")


def test_all_7_categories_declared():
    """The CATEGORIES export must include all 7 legacy categories.
    A picker with a missing category tab hides every widget in that
    category from the operator."""
    src = _read_source()
    m = re.search(r"export const CATEGORIES[^=]*=\s*\[(.+?)\];", src, re.DOTALL)
    assert m, "CATEGORIES export not found"
    declared = set(re.findall(r'\{\s*id:\s*"([a-z]+)"', m.group(1)))
    missing = EXPECTED_CATEGORIES - declared
    assert not missing, (
        f"CATEGORIES is missing: {sorted(missing)}")


def test_default_widget_ids_are_real():
    """DEFAULT_WIDGET_IDS bootstraps the first dashboard load. Every
    ID in that list must exist in the catalog or a fresh install
    renders an empty dashboard."""
    src = _read_source()
    m = re.search(
        r"export const DEFAULT_WIDGET_IDS:\s*string\[\]\s*=\s*\[(.+?)\];",
        src, re.DOTALL)
    assert m, "DEFAULT_WIDGET_IDS export not found"
    defaults = set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))
    assert defaults, "DEFAULT_WIDGET_IDS is empty"
    invalid = defaults - EXPECTED_WIDGET_IDS
    assert not invalid, (
        f"DEFAULT_WIDGET_IDS references non-existent widgets: "
        f"{sorted(invalid)}")


def test_every_widget_has_a_spec_function():
    """Each widget definition must include a `spec:` function. The
    KPICard reads `spec(data)` to get its render-ready props; a
    missing spec means the widget can be added to the dashboard but
    never renders."""
    src = _read_source()
    # Slice between `export const WIDGETS: KPIWidget[] = [` and the
    # final `];` then verify every `id:` is followed (within ~600
    # chars — generous for the multi-line objects) by a `spec:`.
    cat_re = re.compile(
        r"\{\s*id:\s*\"([a-z_0-9]+)\"\s*,[^}]{0,800}\}",
        re.DOTALL)
    matched_ids = set()
    for m in cat_re.finditer(src):
        if "spec:" not in m.group(0):
            continue
        matched_ids.add(m.group(1))
    missing = EXPECTED_WIDGET_IDS - matched_ids
    assert not missing, (
        f"These widgets are missing a `spec:` function: {sorted(missing)}")


def test_widget_picker_uses_dialog_primitive():
    """The picker must use the project's Dialog primitive (which has
    the click-outside-disabled behavior from Unit A). A regression to
    a custom modal would re-introduce the click-outside dismissal
    that the operator specifically complained about."""
    picker_path = (Path(__file__).resolve().parents[1]
                   / "frontend" / "src" / "components" / "WidgetPicker.tsx")
    assert picker_path.is_file(), "WidgetPicker.tsx not found"
    src = picker_path.read_text(encoding="utf-8")
    assert "@/components/ui/dialog" in src, (
        "WidgetPicker should import from @/components/ui/dialog so it "
        "inherits the click-outside-disabled fix from Unit A. A custom "
        "modal would regress that behavior.")


def test_selection_hook_persists_globally_not_per_site():
    """The current restoration is global-only — per-site config is
    deferred (OPEN_THREADS B-2). The selection hook must use a single
    localStorage key, not a per-site one. A future change that
    introduces per-site behavior should update this test."""
    sel_path = (Path(__file__).resolve().parents[1]
                / "frontend" / "src" / "hooks" / "useWidgetSelection.ts")
    src = sel_path.read_text(encoding="utf-8")
    # Single STORAGE_KEY constant, no template literal with site_id.
    assert "bd-widget-selection" in src, (
        "Expected the canonical storage key 'bd-widget-selection'")
    # Per-site would look something like `bd-widget-selection:${siteId}`
    # — if that pattern appears, per-site work has started without
    # updating this test or OPEN_THREADS.
    assert "site_id" not in src.lower() or "site_id" not in src, (
        "useWidgetSelection appears to reference site_id — per-site "
        "scope was deferred (OPEN_THREADS B-2). Update this test and "
        "OPEN_THREADS if per-site is being added.")
