"""v3.66.319 — CLI->GUI parity Phase 4.4: global_config + templates (classification only).

CLASSIFICATION, not new write paths (operator-approved). Each key is marked FULL
because a real EXISTING GUI control configures it — verified, not asserted:

  * "(global_config store)" — the store is read+written via GET/POST /api/global_config
    (Settings.tsx reads on mount, POSTs the draft on Save). The per-key controls on
    the Settings page ARE the store's GUI.
  * template.* (8) — recognizer OUTPUTS written by the capture->promotion flow and
    reviewed via the template-manager GUI (app_template_manager_ui routes +
    /api/sites/<sid>/template_onboard + TemplateAuthoringSection.tsx + disable). The
    promotion/review workflow IS the parity mechanism (per the 4.4 plan); no direct
    field-editor is added (that would put a live hand on the recognizer's controls).

NO runtime/capture-workflow file is touched — only the manifest + baseline + this
test. config_gui_manifest.json is read solely by the inventory gate.

RED-first: on pristine these 9 are gui_exposure=partial (open). Zero-arg.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

# v3.66.710 removed "(global_config store)" -- ONE manifest row that stood for the
# whole store. This file's own docstring claimed each key was "marked FULL because a
# real EXISTING GUI control configures it -- verified, not asserted". For the store
# that was false: the row asserted `full` over 90 keys, 21 of which were not even
# DECLARED, so a POST to them returned 200 and wrote nothing --
# automation.master_off_switch, the emergency stop, among them. A row is not a key.
# The store is now enumerated per key and each one is scored on its own evidence.
_FULL = (
    "template.api_base", "template.blocked_terms", "template.download_trigger",
    "template.network_patterns", "template.resolutions", "template.row_selectors",
    "template.selector_groups", "template.status",
)



def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_global_and_templates_full():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in _FULL:
        assert items[k]["gui_exposure"] == "full", f"{k} not full"


def test_global_config_store_is_enumerated_per_key():
    """What the catch-all row used to claim, now asserted key by key."""
    import config_surface_inventory as csi

    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA

    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    assert "(global_config store)" not in items, (
        "the catch-all row is back -- it hides every key behind one assertion")
    missing = sorted(k for k in GLOBAL_CONFIG_SCHEMA if k not in items)
    assert not missing, f"declared global_config keys absent from the inventory: {missing}"


def test_every_automation_key_is_full():
    """v3.66.711 landed the controls; exposure is DERIVED from the frontend, so this
    measures the GUI rather than asserting about it."""
    import config_surface_inventory as csi

    d = csi.build(str(_REPO))
    dark = sorted(it["key"] for it in d["items"]
                  if it["key"].startswith("automation.")
                  and it["gui_exposure"] != "full")
    assert not dark, f"automation keys with no control: {dark}"


def test_none_of_the_nine_remain_open():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    openset = set(csi._open_settings(d["items"]))
    for k in _FULL:
        assert k not in openset, f"{k} still open"


def test_backing_controls_exist():
    """The 'full' classification is honest only if a real control backs each."""
    # global store: GET/POST /api/global_config
    app = _APP_SRC()
    assert '"/api/global_config"' in app and "POST" in app
    # template promotion/review GUI
    assert (_REPO / "bulk_downloader" / "app_template_manager_ui.py").exists()
    assert (_REPO / "frontend" / "src" / "components" / "sections"
            / "TemplateAuthoringSection.tsx").exists()
    assert "/api/sites/<sid>/template_onboard" in app
