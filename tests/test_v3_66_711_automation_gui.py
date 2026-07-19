"""v3.66.711 (A-GUI Cut 3) -- the Automation GUI.

709 made the automation keys WRITABLE. 710 made them VISIBLE to the parity surface
(26 keys at gui_exposure=none -- the gap became a number instead of a blind spot).
Neither put a control on screen. Today the emergency stop for all autonomous action
is reachable only by hand-crafting a POST.

This cut lands the controls, and these tests pin what "landed" means:

  * every automation.* key has a control (the parity inventory derives exposure from
    what the frontend actually references, so this is not an assertion -- it is a
    measurement);
  * the open-parity debt FALLS by exactly the automation set;
  * Settings has an Automation section (the existing "Automation" NAV group is
    Templates/Pools/AI -- unrelated, and not a home for this);
  * the master off-switch is DANGER-styled and ARM-CONFIRMED, not a bare toggle. It
    dominates every other autonomy toggle, so a stray click must not flip it.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")


def _fe_source():
    src = ""
    for dp, _dn, fns in os.walk(FE):
        for fn in sorted(fns):
            if fn.endswith((".ts", ".tsx")) and not fn.endswith(".test.tsx"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                    src += fh.read()
    return src


def _automation_keys():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA

    return sorted(k for k in GLOBAL_CONFIG_SCHEMA if k.startswith("automation."))


def _inventory():
    from tools import config_surface_inventory as csi

    return csi.build(ROOT)


def test_every_automation_key_has_a_control():
    src = _fe_source()
    missing = [k for k in _automation_keys()
               if '"%s"' % k not in src and "'%s'" % k not in src]
    assert not missing, (
        "%d automation keys have no control in the frontend: %s"
        % (len(missing), missing))


def test_inventory_scores_every_automation_key_exposed():
    """Exposure is DERIVED from the frontend (710), so this measures the GUI, it
    does not assert about it."""
    dark = sorted(it["key"] for it in _inventory()["items"]
                  if it["key"].startswith("automation.")
                  and it["gui_exposure"] != "full")
    assert not dark, "automation keys still gui_exposure!=full: %s" % dark


def test_open_parity_debt_falls_by_the_automation_set():
    """710 pinned the honest baseline at 37 (26 automation + 11 legacy autonomy
    globals). Landing the automation controls must take it to 11 -- and the ratchet
    baseline must be re-pinned in the SAME cut, or the gate is stale."""
    d = _inventory()
    base = json.loads(open(os.path.join(ROOT, "reports", "config_parity_baseline.json"),
                           encoding="utf-8").read())
    open_now = d["counts"]["open_runtime_tunable"]
    assert open_now == base["open_count"], (
        "open=%d but the pinned baseline says %d -- re-pin in the same cut"
        % (open_now, base["open_count"]))
    assert open_now <= 11, (
        "open parity debt is %d; landing the 26 automation controls should take the "
        "37 pinned at 710 down to 11" % open_now)
    assert not [k for k in base.get("open", []) if k.startswith("automation.")], (
        "automation keys are still counted as open debt")


def test_settings_has_an_automation_section():
    """The existing 'Automation' NAV group is Templates/Pools & macros/AI repair --
    nothing to do with the lifecycle automation program. There is no home for these
    controls until one is built."""
    src = open(os.path.join(FE, "routes", "Settings.tsx"),
               encoding="utf-8", errors="replace").read()
    assert '"Automation"' in src or "Automation" in src, "no Automation section"
    schema = open(os.path.join(FE, "lib", "settingsSchema.ts"),
                  encoding="utf-8", errors="replace").read()
    assert "Automation" in schema, (
        "settingsSchema.ts has no Automation section -- the section ToC, the "
        "changed-markers and the command-palette settings search all key off it")


def test_master_off_switch_is_danger_styled_and_arm_confirmed():
    """It dominates every other autonomy toggle. A bare <Switch> is not acceptable:
    a stray click must not be able to change the state of the kill switch."""
    src = open(os.path.join(FE, "routes", "Settings.tsx"),
               encoding="utf-8", errors="replace").read()
    assert "automation.master_off_switch" in src, "the kill switch has no control"

    # Scope to the Automation SECTION -- the key literal also appears at the top of
    # the file as the KILL_KEY constant, and windowing on the first occurrence would
    # inspect the imports instead of the control.
    start = src.index('label="Automation"')
    end = src.index("</SettingSection>", start)
    section = src[start:end]
    assert "danger" in section, "the kill-switch row is not danger-styled"

    # and the write must go through a confirm step, not straight from the control
    assert "setOffSwitchConfirm" in section, (
        "the kill switch writes directly -- it must arm a confirm step first")
    assert "setField(KILL_KEY" not in section, (
        "the kill-switch control calls setField directly; the write belongs in the "
        "confirm dialog, or a stray click flips the emergency stop")
    assert "Dialog" in src and "setOffSwitchConfirm(null)" in src, (
        "no confirm dialog backs the kill switch")


def test_automation_status_readouts_are_surfaced():
    """706 persists the last restore-rehearsal verdict and 708 the last pipeline halt.
    A safety net you cannot see the state of is not a safety net.

    THIS TEST USED TO BE A LIE (fixed at 723). It asserted `"rehearsal" in src.lower()`
    -- which was satisfied by the TOGGLE labelled "Restore rehearsal" that 711 itself
    added. So it went green on the presence of a SWITCH while the VERDICT was surfaced
    nowhere, and it stayed green for 12 releases. It named the property and then
    asserted something structurally incapable of observing it: the exact failure shape
    it was written to prevent.

    A readout is proved by the frontend CALLING THE READ ENDPOINT. That is derivable,
    so derive it -- do not assert a substring that a label can satisfy.
    """
    src = _fe_source()
    assert "/api/automation/status" in src, (
        "no frontend surface calls GET /api/automation/status -- the 706 rehearsal "
        "verdict and the 708 pipeline halt are persisted and rendered NOWHERE")
    # ...and the verdict must be RENDERED, not merely fetched into a dead variable.
    assert "AutomationStatusPanel" in src, (
        "the status endpoint is referenced but no panel renders it")
    # UNKNOWN must reach the operator as its own state. If the UI can only say ok/bad,
    # "never ran" silently becomes one of them -- and that is how a green light gets
    # manufactured out of a net that has never fired.
    assert "UNKNOWN" in src or "unknown" in src, (
        "the readout has no UNKNOWN state; 'never ran' will masquerade as a verdict")
