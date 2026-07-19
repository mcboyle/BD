"""GUI-parity ratchet gate (Phase 4 of the CLI->GUI parity program).

`config_surface_inventory.py --check` is the durable win: it fails if the count of
runtime-tunable settings NOT yet gui_exposure=full exceeds a pinned baseline — the
same ratchet mechanism as legacy_parity. As each Phase-4 cut lands a GUI control it
adds the key to reports/config_gui_manifest.json and the open count shrinks.

RED-first: the pristine tool has no _check / _open_settings / _apply_manifest and
build() emits no 'open_runtime_tunable' count -> these tests fail. After the gate
lands -> GREEN.

Sandbox: tools-only (no Flask); zero-arg fns; root from __file__; tempfile not tmp_path.
"""
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config_surface_inventory as csi  # noqa: E402


def test_build_emits_parity_counts_and_runtime_flag():
    """build() classifies every setting runtime_tunable + emits the open count."""
    d = csi.build(str(ROOT))
    assert "open_runtime_tunable" in d["counts"]
    assert "display_open" in d["counts"]
    for it in d["items"]:
        assert "runtime_tunable" in it and "parity_target" in it
        assert it["parity_target"] in ("full", "display-only")


def test_check_passes_against_pinned_baseline():
    """The committed baseline matches the current open set -> --check is green.
    Stands down while the ratchet is parked (v3.66.468 operator directive)."""
    if not csi._RATCHET_ACTIVE:
        return  # ratchet parked: open-count not enforced
    d = csi.build(str(ROOT))
    assert csi._check(str(ROOT), d) == 0
    base = json.loads((ROOT / "reports/config_parity_baseline.json").read_text())
    assert d["counts"]["open_runtime_tunable"] == base["open_count"]


def test_deploy_only_targets_display_only_never_open():
    """Deploy/path/bootstrap env vars are NOT runtime-tunable: parity_target is
    display-only and they never appear in the open (must-reach-full) set."""
    d = csi.build(str(ROOT))
    items = d["items"]
    opens = set(csi._open_settings(items))
    deploy_seen = 0
    for it in items:
        if it.get("kind") == "env_var" and it["key"] in csi._DEPLOY_ONLY:
            deploy_seen += 1
            assert it["runtime_tunable"] is False
            assert it["parity_target"] == "display-only"
            assert it["key"] not in opens
    assert deploy_seen >= 5, "expected several deploy-only env vars"


def test_manifest_full_shrinks_the_open_set():
    """Marking an open runtime-tunable key 'full' in the manifest removes it from
    the open set — i.e. landing a GUI control shrinks the ratchet. Uses a SYNTHETIC
    open item so the mechanism is verified independently of the live ratchet count
    (which is 0 once parity is complete — v3.66.319)."""
    items = [
        {"key": "_synthetic_open", "kind": "env_var", "gui_exposure": "none",
         "runtime_tunable": True},
        {"key": "_synthetic_full", "kind": "env_var", "gui_exposure": "full",
         "runtime_tunable": True},
    ]
    opens = csi._open_settings(items)
    assert "_synthetic_open" in opens and "_synthetic_full" not in opens
    shrunk = csi._apply_manifest(items, {"_synthetic_open": "full"})
    assert "_synthetic_open" not in csi._open_settings(shrunk)
    assert len(csi._open_settings(shrunk)) == len(opens) - 1


def test_check_fails_when_open_exceeds_baseline():
    """A new un-exposed runtime-tunable setting pushes open above a pinned
    baseline -> --check returns 1 (the regression catch). Stands down while the
    ratchet is parked (v3.66.468 operator directive) -- _check is inert then."""
    if not csi._RATCHET_ACTIVE:
        return  # ratchet parked: _check no longer enforces
    d = csi.build(str(ROOT))
    # pin a temp baseline one BELOW the current open count, simulating that a new
    # setting was added without a GUI control after the baseline was last pinned.
    tmp = Path(tempfile.mkdtemp())
    (tmp / "reports").mkdir()
    (tmp / "reports/config_parity_baseline.json").write_text(json.dumps({
        "open_count": d["counts"]["open_runtime_tunable"] - 1,
        "open": sorted(csi._open_settings(d["items"]))[1:],  # drop one -> "regressed"
    }))
    assert csi._check(str(tmp), d) == 1
