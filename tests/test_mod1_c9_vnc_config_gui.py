"""MOD-1 Cut (v3.66.808): the two remaining Arch-B takeover knobs --
``captcha_vnc_display`` and ``captcha_vnc_websocket_port`` -- become DECLARED,
GUI-configurable global_config keys.

Before this cut they were read via a plain ``(config or {}).get(...)`` with a
code default (takeover_vnc.py), were absent from GLOBAL_CONFIG_SCHEMA, and so
``POST /api/global_config`` rejected them 400 ("unknown config key(s)"). An
operator could not set the display or the websocket port from the UI -- exactly
the gap MOD1_ARCH_B_STATUS.md flagged ("front them with a declared key + FE
control when the operator needs to set them from the UI").

RED-first: on pristine source both keys are undeclared, so every assertion below
fails. The fix is (a) declare them in GLOBAL_CONFIG_SCHEMA, (b) add both to the
frontend settingsSchema.ts so the config-surface tool derives gui_exposure=full,
(c) ledger them in reports/config_gui_manifest.json.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

_KEYS = ("captcha_vnc_display", "captcha_vnc_websocket_port")


def test_keys_declared_in_schema():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA as gcs
    missing = [k for k in _KEYS if k not in gcs]
    assert not missing, f"undeclared global_config keys: {missing}"
    # str type mirrors captcha_takeover_max_concurrent ("2"); takeover_vnc int()-coerces.
    for k in _KEYS:
        assert gcs[k]["type"] is str, f"{k} type should be str, got {gcs[k]['type']}"


def test_keys_are_gui_exposed_full():
    _saved = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import config_surface_inventory as P2  # noqa: E402
        d = P2.build(str(_REPO))
        ex = {i["key"]: i["gui_exposure"]
              for i in d["items"] if i["kind"] == "global_config"}
    finally:
        sys.path[:] = _saved
    for k in _KEYS:
        assert ex.get(k) == "full", f"{k} gui_exposure={ex.get(k)} (want full)"


def test_keys_ledgered_in_manifest():
    _saved = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import config_surface_inventory as P2  # noqa: E402
        man = P2._load_manifest(str(_REPO))
    finally:
        sys.path[:] = _saved
    for k in _KEYS:
        assert man.get(k) == "full", f"{k} not ledgered 'full' in config_gui_manifest"


def test_api_accepts_the_declared_keys():
    """The 400 'unknown config key' rejection keys off GLOBAL_CONFIG_SCHEMA
    membership (app_global_config.py _known). Declaring the keys must make the
    admission set contain them."""
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA as gcs
    from bulk_downloader.app_global_config import _EXPLICIT_BRANCH_KEYS
    known = set(gcs) | set(_EXPLICIT_BRANCH_KEYS)
    for k in _KEYS:
        assert k in known, f"{k} would still be rejected 400 by /api/global_config"
