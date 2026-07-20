"""MOD-1 Cut (v3.66.810): the F1.4 predictive-relogin per-site knobs
(``predictive_relogin_enabled`` / ``predictive_relogin_fraction``) become
DECLARED, GUI-configurable per-site keys.

runner_auth.py reads both from ``self.config`` (the per-site runner config), but
neither was in CFG_FIELDS. Two consequences, both fixed here:

  1. DROP-ON-RELOAD (a real latent bug): _load_sites_config rebuilds each site's
     config as ``{k: cfg_in.get(k, DEFAULTS.get(k, "")) for k in CFG_FIELDS}`` --
     any key NOT in CFG_FIELDS is silently dropped on restart. So an operator who
     PUT predictive_relogin_enabled=true lost it on the next reload; the F1.4
     feature could not actually be turned on per-site and survive.
  2. NO GUI CONTROL: absent from the schema-driven site editor, so no toggle/field.

Fix: add both to CFG_FIELDS + DEFAULTS (survives reload), categorize them with the
other relogin fields (gated -> renders a control), type them in site_editor
(_FIELD_TYPES + NUMERIC_RANGES for the 0..1 fraction), ledger gui_exposure=full.

RED-first: on pristine source both are undeclared -> every assertion fails, and
the drop-on-reload guard fails (the value is dropped by the rebuild).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_KEYS = ("predictive_relogin_enabled", "predictive_relogin_fraction")


def test_keys_in_cfg_fields():
    from bulk_downloader import app_kernel as k
    cf = set(k.CFG_FIELDS)
    missing = [x for x in _KEYS if x not in cf]
    assert not missing, f"per-site keys not in CFG_FIELDS: {missing}"


def test_value_survives_the_reload_rebuild():
    """CLAUDE.md 0 bug-fix guard: the value must SURVIVE the CFG_FIELDS rebuild
    that _load_sites_config performs. On pristine source the key is not in
    CFG_FIELDS, so the rebuild drops it -> this fails RED and proves the bug."""
    from bulk_downloader import app_kernel as k
    DEFAULTS = getattr(k, "DEFAULTS", {})
    cfg_in = {"predictive_relogin_enabled": True,
              "predictive_relogin_fraction": 0.75}
    # EXACT rebuild from app.py _load_sites_config
    rebuilt = {kk: cfg_in.get(kk, DEFAULTS.get(kk, "")) for kk in k.CFG_FIELDS}
    assert rebuilt.get("predictive_relogin_enabled") is True, (
        "predictive_relogin_enabled was DROPPED by the CFG_FIELDS reload rebuild")
    assert rebuilt.get("predictive_relogin_fraction") == 0.75, (
        "predictive_relogin_fraction was DROPPED by the CFG_FIELDS reload rebuild")


def test_fraction_numeric_range_enforced():
    """The fraction is a 0..1 float; a direct PUT must not persist out-of-range."""
    from bulk_downloader import site_editor as se
    assert "predictive_relogin_fraction" in se.NUMERIC_RANGES, \
        "predictive_relogin_fraction missing from NUMERIC_RANGES"
    lo, hi = se.NUMERIC_RANGES["predictive_relogin_fraction"]
    assert (lo, hi) == (0.0, 1.0), f"fraction range should be (0.0, 1.0), got {(lo, hi)}"
    errs = se.validate_numeric_updates({"predictive_relogin_fraction": 1.5})
    assert "predictive_relogin_fraction" in errs, "out-of-range fraction not rejected"
    assert not se.validate_numeric_updates({"predictive_relogin_fraction": 0.8}), \
        "in-range fraction wrongly rejected"


def test_keys_gui_exposed_full():
    _saved = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import config_surface_inventory as P2  # noqa: E402
        d = P2.build(str(_REPO))
        ex = {i["key"]: i["gui_exposure"]
              for i in d["items"] if i["kind"] == "site_key"}
    finally:
        sys.path[:] = _saved
    for x in _KEYS:
        assert ex.get(x) == "full", f"{x} gui_exposure={ex.get(x)} (want full)"


def test_keys_ledgered_in_manifest():
    _saved = list(sys.path)
    sys.path.insert(0, str(_REPO / "tools"))
    sys.path.insert(0, str(_REPO))
    try:
        import config_surface_inventory as P2  # noqa: E402
        man = P2._load_manifest(str(_REPO))
    finally:
        sys.path[:] = _saved
    for x in _KEYS:
        assert man.get(x) == "full", f"{x} not ledgered 'full' in manifest"
