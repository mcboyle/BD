"""v3.66.302 — GUI-parity inventory drift reconciliation.

The shipped reports/gui_parity_inventory.json had been deliberately frozen at the
292 baseline (1098 items) to avoid leaking a standing CLI-census drift on
no-route cuts. The standing drift is now reconciled: the 7 real tools that exist
in tools/ but were absent from the pinned inventory, plus the 2 new routes added
@293, must now appear in the SHIPPED inventory.

This pins the RECONCILIATION (the 9 specific previously-phantom items are present)
WITHOUT pinning the total — so a future tool/route addition isn't gated into a
brittle full-census equality (G12 already gates the 3 write-blueprints' counts).
"""
import json
import sys
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parent.parent
# THB-1 (v3.66.528): the tools/ + repo sys.path inserts used to live here at module
# scope and were never restored -> any later test that imported a name colliding with
# a tools/*.py module got silently shadowed. They are now scoped + restored inside the
# one function that needs them (test_shipped_inventory_matches_live_regen_itemset).

# the 7 tools that existed in tools/ but were missing from the frozen baseline
_PHANTOM_TOOLS = {
    "build_session_pack", "legacy_pin_scan", "make_overlay", "precut_check",
    "tasktracker_gen", "tasktracker_sync", "build_recognizer_corpus",
}
# the 2 routes added @293 that were never reconciled into the shipped inventory
_NEW_293_ROUTES = {"sites.api_template_capture_cancel", "cockpit.api_capture_goto"}


@pytest.fixture
def generated_inventory_path(tmp_path):
    """Materialize the ignored parity report in an isolated output directory."""
    saved_path = list(sys.path)
    try:
        import tools.gui_parity_inventory as parity
        outdir = tmp_path / "reports"
        assert parity.main(["--root", str(_REPO), "--outdir", str(outdir)]) == 0
    finally:
        sys.path[:] = saved_path
    path = outdir / "gui_parity_inventory.json"
    assert path.is_file()
    return path


def _shipped_names(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {it["name"] for it in d["items"]}, d


def test_shipped_inventory_includes_reconciled_tools(generated_inventory_path):
    names, _ = _shipped_names(generated_inventory_path)
    missing = sorted(_PHANTOM_TOOLS - names)
    assert not missing, f"shipped inventory still missing reconciled tools: {missing}"


def test_shipped_inventory_includes_new_293_routes(generated_inventory_path):
    names, _ = _shipped_names(generated_inventory_path)
    missing = sorted(_NEW_293_ROUTES - names)
    assert not missing, f"shipped inventory still missing 293 routes: {missing}"


def test_shipped_inventory_matches_live_regen_itemset(generated_inventory_path):
    """No drift: the shipped json's item-set equals a fresh regen's item-set.
    (Item-set identity, not a brittle count pin — proves the artifact is truthful
    at cut time.)"""
    names, _ = _shipped_names(generated_inventory_path)
    _saved_path = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import gui_parity_inventory as P1  # noqa: E402
        regen = {it["name"] for it in P1.build(str(_REPO))["items"]}
    finally:
        sys.path[:] = _saved_path
    only_shipped = sorted(names - regen)
    only_regen = sorted(regen - names)
    assert not only_shipped and not only_regen, (
        f"inventory drift — only-shipped={only_shipped} only-regen={only_regen}")


def test_gated_blueprint_counts_unchanged(generated_inventory_path):
    """G12 invariant: the 3 write-blueprint counts must be unchanged by the
    reconciliation (the 9 new items are NOT on data_layer/report_center/
    actions_center, so G12 stays green)."""
    names, _ = _shipped_names(generated_inventory_path)
    c = {"data_layer.": 0, "report_center.": 0, "actions_center.": 0}
    for n in names:
        for k in c:
            if n.startswith(k):
                c[k] += 1
    assert c == {"data_layer.": 15, "report_center.": 9, "actions_center.": 0}, c
