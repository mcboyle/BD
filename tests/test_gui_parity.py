"""Tests for the GUI Parity inventory generators (Phase 1 + 2). Inventory-only;
verifies required fields, coverage, and classification invariants."""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO))

import gui_parity_inventory as P1  # noqa: E402
import config_surface_inventory as P2  # noqa: E402

_ROOT = str(_REPO)
_P1_FIELDS = {"name", "source_file", "command_or_endpoint", "purpose", "category",
              "gui_support", "dependencies", "difficulty", "runtime_risk",
              "recommended_gui_location", "recommendation", "gui_class", "kind"}
_GUI_CLASSES = {"gui-safe", "gui-gated", "read-only", "cli-only"}
_P2_FIELDS = {"key", "source_file", "category", "default", "gui_exposure",
              "read_write", "validation_rules", "description", "risk",
              "recommended_gui_section", "related", "kind"}


def test_p1_items_have_required_fields():
    d = P1.build(_ROOT)
    assert d["counts"]["total"] > 100
    for it in d["items"]:
        assert _P1_FIELDS <= set(it), _P1_FIELDS - set(it)
        assert it["gui_support"] in ("full", "partial", "none")
        assert it["runtime_risk"] in ("none", "low", "medium", "high")
        assert it["gui_class"] in _GUI_CLASSES
    json.dumps(d)  # serializable


def test_p1_includes_tools_and_routes():
    d = P1.build(_ROOT)
    k = d["counts"]["by_kind"]
    assert k.get("cli_tool", 0) >= 50
    assert k.get("cockpit_page", 0) + k.get("cockpit_api", 0) >= 10
    assert k.get("workflow", 0) >= 5


def test_p1_high_risk_tools_are_cli_only():
    d = P1.build(_ROOT)
    for it in d["items"]:
        if it["kind"] == "cli_tool" and it["runtime_risk"] == "high":
            assert it["gui_class"] == "cli-only", it["name"]

def test_p1_sensitive_apis_are_gui_gated_not_safe():
    d = P1.build(_ROOT)
    SENS = ("secret", "login", "rotate", "bulk_delete", "rebalance", "circuit")
    for it in d["items"]:
        if it["kind"] in ("cockpit_api", "gui_api") and any(s in it["name"].lower() for s in SENS):
            if it["runtime_risk"] in ("medium", "high"):  # mutating sensitive
                assert it["gui_class"] in ("gui-gated", "read-only"), it["name"]

def test_p1_new_surfaces_present():
    d = P1.build(_ROOT)
    k = d["counts"]["by_kind"]
    assert k.get("gui_page", 0) >= 1 and k.get("shell_entrypoint", 0) >= 1
    assert k.get("blueprint_module", 0) == 3  # cockpit_console + framework_dashboard/fleet


def test_p2_items_have_required_fields():
    d = P2.build(_ROOT)
    assert d["counts"]["total"] > 50
    for it in d["items"]:
        assert _P2_FIELDS <= set(it), _P2_FIELDS - set(it)
        assert it["gui_exposure"] in ("full", "partial", "none", "display-only")
        assert it["risk"] in ("none", "low", "medium", "high")
    json.dumps(d)


def test_p2_env_vars_present_and_exposure_is_ledgered():
    """Env vars are unexposed by DEFAULT; the CLI->GUI parity program promotes
    them cut-by-cut (the config-parity ratchet burns 159->0). This pins the
    EVOLVING invariant -- the set of exposed env vars equals exactly the BD_ keys
    recorded in reports/config_gui_manifest.json -- NOT the now-false "every env
    var is none" (4 queue-HK keys are gui_exposure=full as of v3.66.306). It stays
    green as promotions land and catches any env var exposed off-ledger (or a
    manifest key with no live exposure)."""
    d = P2.build(_ROOT)
    envs = [i for i in d["items"] if i["kind"] == "env_var"]
    assert len(envs) >= 50
    # v3.66.713: this used to assert EVERY env var starts with the project prefix.
    # That assertion did not describe reality, it CREATED the blind spot: the scan
    # matched the prefix, so a var without it could not be seen, and the test then
    # certified that none existed. Five do -- and CLOAKBROWSER_BINARY_PATH selects the
    # browser BINARY that gets executed. (The NETNS_* vars are deliberately unprefixed
    # to dodge FG-ENV-TRANCHE-BD-LITERAL, so the gate's own pressure keeps producing
    # them.) Prefixed vars must still be ledgered; unprefixed ones are tracked as
    # bootstrap/deploy display-only.
    unprefixed = [i for i in envs if not i["key"].startswith("BD_")]
    assert all(i["gui_exposure"] == "display-only" for i in unprefixed), (
        "an unprefixed env var claims a GUI control: %s"
        % [i["key"] for i in unprefixed if i["gui_exposure"] != "display-only"])
    assert all(i["gui_exposure"] in ("none", "full", "display-only") for i in envs)
    # The ledger invariant: an env var with any exposure must be RECORDED as such.
    # This used to filter the manifest to prefixed keys, which is the same blind spot
    # one line down -- an unprefixed var would be "promoted but unledgered" forever.
    env_keys = {i["key"] for i in envs}
    promoted = {i["key"] for i in envs if i["gui_exposure"] != "none"}
    manifest_envs = {k for k in P2._load_manifest(_ROOT) if k in env_keys}
    assert promoted == manifest_envs, (sorted(promoted), sorted(manifest_envs))


def test_p2_secrets_are_high_risk():
    d = P2.build(_ROOT)
    secrets = [i for i in d["items"] if i["category"] == "per-site secret"]
    assert secrets, "expected per-site secret fields"
    assert all(i["risk"] == "high" for i in secrets)

def test_p2_cfg_fields_added():
    d = P2.build(_ROOT)
    sk = {i["key"] for i in d["items"] if i["kind"] == "site_key"}
    for f in ("login_url", "user_field", "pass_field", "submit_btn", "success_url",
              "dl_selector", "trigger_selector", "dismiss_selectors", "cookie_file",
              "download_dir", "filename_template", "username", "sched_time", "sched_repeat"):
        assert f in sk, f

def test_p2_other_stores_and_related():
    d = P2.build(_ROOT)
    k = d["counts"]["by_kind"]
    assert k.get("vpn_config", 0) >= 1 and k.get("widgets_config", 0) >= 1
    assert any(i.get("related") for i in d["items"])


# ── Phase 3 (GUI-parity final-audit closure) ─────────────────────
# Finding #2: config/maintenance/lifecycle state mutations must be gui-gated
# (they were landing gui-safe). Pin the audit's named items so the sensitivity
# keyword set can't silently regress them back to gui-safe.
_PHASE3_MUST_GATE = {
    "api_dev_config_reload", "api_dev_config_restore", "api_dev_config_snapshot",
    "api_dev_feature_flag_set", "api_dev_maintenance_enable", "api_dev_maintenance_disable",
    "api_backup_restore", "api_global_config", "api_supervisor_configure",
    "api_plugins_reload", "api_prune", "api_prune_selectors",
    "api_start", "api_start_all", "api_stop", "api_library_scan_start",
}


def test_phase3_config_maintenance_mutations_are_gated():
    d = P1.build(_ROOT)
    by_name = {it.get("name"): it for it in d["items"]}
    for name in _PHASE3_MUST_GATE:
        it = by_name.get(name)
        if it is None:
            continue  # route not present in this tree slice — don't fail on absence
        assert it["gui_class"] == "gui-gated", f"{name} must be gui-gated, got {it['gui_class']}"


# Finding #1: the previously un-inventoried operator workflows are now present.
_PHASE3_WORKFLOW_HINTS = ("VPN", "rotation", "Backup", "scan", "Schedule",
                          "Secrets", "Import", "Live recording", "Manual-login")


def test_phase3_operator_workflows_inventoried():
    d = P1.build(_ROOT)
    wf_names = [it.get("name", "") for it in d["items"] if it.get("kind") == "workflow"]
    # the audit named ~10 missing journeys; we should now carry well more than the old 9
    assert len(wf_names) >= 15, f"expected >=15 workflows after Phase 3, got {len(wf_names)}"
    blob = " | ".join(wf_names)
    missing = [h for h in _PHASE3_WORKFLOW_HINTS if h.lower() not in blob.lower()]
    assert not missing, f"Phase-3 workflows not inventoried: {missing}"
