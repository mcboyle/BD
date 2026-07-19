"""v3.66.319 — CLI->GUI parity Phase 4.3d: final env_var closures, all DISPLAY-ONLY.

The last four open env_vars are all "set outside the running service" — a GUI write
is meaningless, so parity = a read-only effective-value panel (display-only),
matching the @315 bind-port precedent (BD_COCKPIT_PORT/FLEET_PORT/FRAMEWORK_PORT):

  * BD_HOST / BD_PORT  — Flask BIND host/port, bound before any request handler
    runs (no app.run() hot-swap). Bind-time -> display-only.
  * BD_RELEASE_ARCHIVE — read only by the rollback.py CLI ops tool, not the running
    service. Deploy/ops override -> display-only.
  * BD_URL            — "the URL external clients use to reach this server"; the
    server has no reader and can't self-configure its own external URL (operator
    decision: display-only for now).

This closes the env_var kind entirely (the original 12: 2 full, 4 excluded as
false-positives, 6 display-only). RED-first: on pristine these four are OPEN
(runtime_tunable, not display-only). Zero-arg.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_DISPLAY = ("BD_HOST", "BD_PORT", "BD_RELEASE_ARCHIVE", "BD_URL")


def test_final_four_are_display_only():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in _DISPLAY:
        it = items[k]
        assert it["runtime_tunable"] is False, f"{k} should be non-runtime-tunable (bind/deploy)"
        assert it["parity_target"] == "display-only", k
        assert it["gui_exposure"] == "display-only", k


def test_no_open_env_vars_remain():
    """The whole env_var kind is closed."""
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    openset = set(csi._open_settings(d["items"]))
    ev_open = [k for k in openset if items[k].get("kind") == "env_var"]
    assert ev_open == [], f"open env_vars remain: {ev_open}"


def test_display_open_still_zero():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    assert d["counts"].get("display_open") == 0, d["counts"].get("display_open")
