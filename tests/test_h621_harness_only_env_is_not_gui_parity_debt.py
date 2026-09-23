"""H621 / register row 963: a variable with NO product reader is not GUI-parity debt.

tools/config_surface_inventory.py::_is_runtime_tunable defaults to True for any env_var that is
not on _DEPLOY_ONLY / _IMPORT_TIME / _ALIAS_OF_FULL. Its own comment warns that recomputing the
verdict "MANUFACTURES debt that no control could ever close" -- and that is exactly what happened
to BD_GATE_SCOPE and BD_WHEELHOUSE_DIR, which arrived on trains 25 and 23 and were counted as open
GUI-parity debt on the shrink-only ratchet in reports/config_parity_baseline.json.

MEASURED at origin/main 8fc45b25f, `grep -rl <key>` per tree (product / tools / tests):
    BD_GATE_SCOPE        0 / 1 / 678   -- a pytest scope marker
    BD_WHEELHOUSE_DIR    0 / 1 /   0   -- a fleet CI wheelhouse knob
    BD_AUTH_TOKEN        3 / 4 /  14   -- the positive control: a real setting reads non-zero
A GUI control for either of the first two would wire to nothing in the product, so they can never
leave the open set and the ratchet can never reach zero.

H622 / O1330: later GUI controls closed the remaining debt. The live ratchet now
pins zero, while preserving runtime-tunable/full assertions for the two former open keys.
"""
import pathlib
import sys

import pytest

# MODULE scope: this gate pins the classification of NAMED KEYS by one module,
# tools/config_surface_inventory.py. It is not a tree sweep -- the repo-wide sweep of the same
# surface is tests/test_config_parity_ratchet.py, which is already declared repo-wide and already
# in a shard. Marking this file repo-wide instead puts it in the DERIVED gate population, and the
# 939 gate then (correctly) fails it for sitting in no CI shard: measured on tree d6b48f44,
# 8 of the 939 assertions went red for exactly that reason. Wiring it into a ci.yml shard is
# H622's row and is deferred by O1186 until this train is green, so the honest marker is the one
# that describes what the file actually gates.
# (The marker is itself BD_GATE_SCOPE, one of the two variables this cut declassifies: read by
# the shard gate and by no product file, which is the whole point of the cut.)
BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.config_surface_inventory as csi  # noqa: E402

HARNESS_ONLY = ("BD_GATE_SCOPE", "BD_WHEELHOUSE_DIR")

# O1186 (operator): deployment topology and secrets, excluded as deploy-only. These DO have
# product readers -- the reader census is in the source comment beside the set -- so they are
# pinned separately from HARNESS_ONLY, whose members have none. Conflating the two reasons is
# how a real setting gets parked in the wrong set later.
DEPLOY_ONLY_O1186 = ("BD_VAULT_KEY", "BD_REDIS_HOST", "BD_REDIS_PORT", "BD_CLUSTER_RATE_MODE")


def _item(items, key):
    return next((i for i in items if i.get("key") == key), None)


def _inventory():
    return csi.build(str(ROOT))


def test_harness_only_env_vars_are_not_runtime_tunable():
    items = _inventory()["items"]
    for key in HARNESS_ONLY:
        it = _item(items, key)
        assert it is not None, f"{key} vanished from the inventory; this test pins a real key"
        assert it["runtime_tunable"] is False, (
            f"{key} is counted as runtime-tunable, so it sits in the open GUI-parity set, "
            "but it has no product reader and no control could ever close it"
        )
        assert it["parity_target"] == "display-only"


def test_the_o1186_deploy_only_keys_are_not_runtime_tunable():
    """O1186: deployment endpoints and the vault secret are not user settings."""
    items = _inventory()["items"]
    for key in DEPLOY_ONLY_O1186:
        it = _item(items, key)
        assert it is not None, f"{key} vanished from the inventory; this test pins a real key"
        assert it["runtime_tunable"] is False, f"{key} is still counted as GUI-parity debt"
        assert it["parity_target"] == "display-only"


def test_a_setting_with_product_readers_is_still_runtime_tunable():
    """POSITIVE CONTROL: the fix must not blanket-exclude env vars."""
    items = _inventory()["items"]
    for key in ("BD_HTTP_PROXY",):
        it = _item(items, key)
        assert it is not None and it["runtime_tunable"] is True, (
            f"{key} has product readers and is real GUI-parity debt; excluding it would hide "
            "the very debt this ratchet exists to track"
        )


def test_the_open_count_remains_zero_without_excluding_real_settings():
    """The former open settings have controls and must remain runtime tunable."""
    d = _inventory()
    open_keys = sorted(
        i["key"] for i in d["items"] if i.get("runtime_tunable") and i.get("gui_exposure") != "full"
    )
    assert open_keys == [], f"H622: unexpected GUI-parity debt: {open_keys}"
    for key in HARNESS_ONLY:
        assert key not in open_keys
    for key in DEPLOY_ONLY_O1186:
        assert key not in open_keys
    for key in ("BD_HTTP_PROXY", "turnstile_one_click_enabled"):
        item = _item(d["items"], key)
        assert item is not None and item["runtime_tunable"] is True
        assert item["gui_exposure"] == "full", f"{key}: the GUI control disappeared"
    assert d["counts"]["open_runtime_tunable"] == 0


def test_the_exclusion_is_scoped_to_env_vars_only():
    """A same-named setting of a DIFFERENT kind must stay runtime-tunable.

    Without the `kind == "env_var"` half of the guard, a future site_key or global_config key
    that happened to be called BD_GATE_SCOPE would be silently excluded too. Dropping that half
    is invisible on today's tree -- no such item exists -- so the mutant is driven directly here.
    """
    for kind in ("site_key", "global_config", "db_config"):
        fabricated = {"key": "BD_GATE_SCOPE", "kind": kind}
        assert csi._is_runtime_tunable(fabricated) is True, (
            f"a {kind} named BD_GATE_SCOPE was excluded; the exclusion must be env_var-only"
        )
    assert csi._is_runtime_tunable({"key": "BD_GATE_SCOPE", "kind": "env_var"}) is False


def test_one_more_open_setting_would_red_the_gate_again(monkeypatch):
    """An extra uncontrolled setting must increase debt and fail the live zero pin."""
    real = csi._is_runtime_tunable

    def seventh(it):
        if it.get("key") == "BD_GATE_SCOPE" and it.get("kind") == "env_var":
            return True   # pretend one more uncontrolled setting exists
        return real(it)

    monkeypatch.setattr(csi, "_is_runtime_tunable", seventh)
    d = csi.build(str(ROOT))
    assert d["counts"]["open_runtime_tunable"] == 1, (
        "an extra uncontrolled runtime-tunable setting did not raise the open count"
    )
    with pytest.raises(AssertionError, match="H622: unexpected GUI-parity debt"):
        test_the_open_count_remains_zero_without_excluding_real_settings()


# ── E1 (BOUNCE 2026-09-21T20:46Z): a changed SHARED constant owes a consumer census. ──────────
# _DEPLOY_ONLY is read by tools/config_surface_inventory.py AND, through an ast parse, by
# tests/test_v3_66_504_envfile_editor.py, which asserts the GUI env editor's key set is exactly
# (_DEPLOY_ONLY - _HOST_MANAGED) | {BD_DISABLE_VPN_RUNTIME}. Adding four keys to _DEPLOY_ONLY
# therefore DEMANDED four more GUI controls -- the exact opposite of O1186's intent. The two
# assertions below pin the reconciliation so the next editor of this set cannot undo it silently.
def test_the_o1186_keys_are_not_exposed_by_the_gui_env_editor():
    """A vault key and the broker topology must never become .env fields in the GUI."""
    from bulk_downloader import app_envfile_editor as EE

    editable = {r["name"] for r in EE._keys()}
    leaked = sorted(csi._O1186_DEPLOY_ONLY & editable)
    assert not leaked, (
        f"O1186 keys are GUI-writable through the envfile editor: {leaked}. They were excluded "
        "from the parity surface as deploy-managed; exposing them contradicts that and hands an "
        "operator a field that moves one reader and nothing else."
    )
    # POSITIVE CONTROL: the probe can find a key that IS editable, so the empty set above is a
    # measurement and not a broken lookup.
    assert "BD_PORT" in editable, "the editor key probe found nothing; the zero above is vacuous"


def test_the_o1186_keys_get_their_own_danger_note_not_the_path_one():
    """The generic _DEPLOY_ONLY note says 'Path / storage root'. That is wrong for a vault key."""
    for key in sorted(csi._O1186_DEPLOY_ONLY):
        danger, note = csi._danger_for({"key": key, "kind": "env_var", "source_file": ""})
        assert danger is True, f"{key} is deploy-managed infrastructure and must read as dangerous"
        assert "Path / storage root" not in note, (
            f"{key} got the generic path/storage wording; an operator reading it would think the "
            "risk is orphaned files, not a cluster-wide broker or the secret itself"
        )
        assert "deploy time" in note
    # NEGATIVE CONTROL: a real path key still gets the path wording, so the assertion above
    # discriminates rather than passing for every key.
    _, path_note = csi._danger_for({"key": "BD_ROOT", "kind": "env_var", "source_file": ""})
    assert "Path / storage root" in path_note
