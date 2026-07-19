"""v3.66.319 — CLI->GUI parity Phase 4.3c: scanner false-positive exclusion (non-guard).

The four "env vars" BD_ENV_VARS / BD_TEMP_PREFIXES / BD_VPN_DIRNAMES /
BD_TO_APPRISE_EVENT were never environment variables — they are Python MODULE
CONSTANTS (`_BD_ENV_VARS = (...)`, `_BD_TO_APPRISE_EVENT = {...}`) that the
inventory's coverage-pass regex `BD_[A-Z0-9_]+` matched on the `BD_...` substring
INSIDE the leading-underscore identifier. None has an os.environ read site; none
is a configuration knob (the real notification config is the apprise dispatcher
surface, already GUI-exposed). The honest fix is a negative-lookbehind so the
coverage regex only matches a `BD_*` token at a boundary (a real literal), never
inside `_BD_*`. The four leave the inventory entirely -> closed in the ratchet.

RED-first: on pristine v3.66.318 these four ARE present (kind=env_var) -> the
absence assertions fail. Zero-arg.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_FALSE_POSITIVES = (
    "BD_ENV_VARS", "BD_TEMP_PREFIXES", "BD_VPN_DIRNAMES", "BD_TO_APPRISE_EVENT",
)


def test_false_positives_not_in_inventory():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    keys = {it["key"] for it in d["items"]}
    for k in _FALSE_POSITIVES:
        assert k not in keys, f"{k} is a module constant, not an env var — should be excluded"


def test_false_positives_not_open():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    openset = set(csi._open_settings(d["items"]))
    for k in _FALSE_POSITIVES:
        assert k not in openset, f"{k} should be closed (excluded)"


def test_real_env_vars_still_detected():
    """The lookbehind must NOT drop genuine BD_* env vars referenced as literals."""
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    keys = {it["key"] for it in d["items"]}
    # a sample of real env vars that must survive the fix
    for k in ("BD_HOME", "BD_DEV_MODE", "BD_AUTONOMY_ENABLED", "BD_CROSS_SITE_SELECTORS",
              "BD_CAPTURE_BODIES"):
        assert k in keys, f"real env var {k} was wrongly dropped"
