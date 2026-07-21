"""MOD-1 Cut (v3.66.809): the C-7 egress-containment switch ``netns_isolation``
becomes a DECLARED, GUI-configurable global_config key.

Before this cut it was read via ``(cfg or {}).get("netns_isolation")`` in
netns_isolation.py, was absent from GLOBAL_CONFIG_SCHEMA, and so
POST /api/global_config rejected it 400 and no FE control existed -- the operator
could not arm/disarm egress confinement from the UI.

The delicate part: ``netns_isolation`` accepts BOTH a bare bool (``true``) and a
dict form (``{enabled, egress:{wg_iface,...}}``). The type is declared as
``(bool, dict)`` so the API accepts either, and validate_config's dict branch
(which skips the scalar type check and only flags sub-keys that shadow flat
schema keys -- netns's ``enabled``/``egress`` do not) leaves the advanced form
valid. safety=False preserves the pre-cut behavior exactly (the key was never
validated/fail-closed before, and enforcement fail-closes at the launch layer,
not here).

RED-first: on pristine source the key is undeclared, so every assertion fails.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_KEY = "netns_isolation"


def test_key_declared_and_accepts_bool_and_dict():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA as gcs
    assert _KEY in gcs, f"{_KEY} not declared in GLOBAL_CONFIG_SCHEMA"
    t = gcs[_KEY]["type"]
    types = t if isinstance(t, tuple) else (t,)
    assert bool in types, f"{_KEY} type must accept bool (the GUI toggle)"
    assert dict in types, (
        f"{_KEY} type must ALSO accept dict, or the advanced egress form "
        f"({{enabled, egress}}) is rejected -- got {t}")


def test_advanced_dict_form_still_validates_clean():
    """Regression guard (CLAUDE.md 0): declaring the key must NOT make the
    advanced dict form fail validation. The subject (a real dict-form config) is
    IN the denominator -- validate_config over it yields zero findings for the
    key."""
    from bulk_downloader import global_config as gc
    dict_form = {"netns_isolation": {"enabled": True,
                                     "egress": {"wg_iface": "wg0",
                                                "wg_conf": "/etc/wg/wg0.conf",
                                                "address": "10.9.0.2/32"}}}
    findings = [f for f in gc.validate_config(dict_form) if f["key"] == _KEY]
    assert not findings, f"advanced dict form regressed to invalid: {findings}"
    # and the bare bool form is clean too
    bool_findings = [f for f in gc.validate_config({"netns_isolation": True})
                     if f["key"] == _KEY]
    assert not bool_findings, f"bool form invalid: {bool_findings}"


def test_key_is_gui_exposed_full():
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
    assert ex.get(_KEY) == "full", f"{_KEY} gui_exposure={ex.get(_KEY)} (want full)"


def test_key_ledgered_in_manifest():
    _saved = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import config_surface_inventory as P2  # noqa: E402
        man = P2._load_manifest(str(_REPO))
    finally:
        sys.path[:] = _saved
    assert man.get(_KEY) == "full", f"{_KEY} not ledgered 'full' in manifest"


def test_api_admits_the_declared_key():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA as gcs
    from bulk_downloader.app_global_config import _EXPLICIT_BRANCH_KEYS
    known = set(gcs) | set(_EXPLICIT_BRANCH_KEYS)
    assert _KEY in known, f"{_KEY} would still be rejected 400 by /api/global_config"
