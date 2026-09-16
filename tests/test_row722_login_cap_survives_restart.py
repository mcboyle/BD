"""Row 722 live (test2, 2026-09-15, every restart): PUT /api/sites/<sid>
{"login_attempt_cap_per_day": 8} was accepted, but the key was not in
CFG_FIELDS, so the _load_sites_config rebuild dropped it on restart and the
worker's auto re-login was refused: "daily login attempt cap reached (6/3);
raise login_attempt_cap_per_day for this site to log in again today"
(naughtyamerica, 07:52Z test2). Same class as tests/test_mod1_c11_predictive_relogin_gui.py.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

KEY = "login_attempt_cap_per_day"


def test_the_row_the_cap_survives_the_reload_rebuild():
    from bulk_downloader import app_kernel as k
    assert KEY in set(k.CFG_FIELDS), f"{KEY} not in CFG_FIELDS: every restart resets the cap to 3"
    DEFAULTS = getattr(k, "DEFAULTS", {})
    cfg_in = {KEY: 8}
    rebuilt = {kk: cfg_in.get(kk, DEFAULTS.get(kk, "")) for kk in k.CFG_FIELDS}
    assert rebuilt.get(KEY) == 8, f"{KEY} was DROPPED by the CFG_FIELDS reload rebuild"


def test_default_is_three_and_range_is_bounded():
    from bulk_downloader import app_kernel as k, site_editor as se
    assert k.DEFAULTS.get(KEY) == 3
    lo, hi = se.NUMERIC_RANGES[KEY] if hasattr(se, "NUMERIC_RANGES") else _range(se)
    assert lo >= 1 and hi >= 8


def _range(se):
    for name in dir(se):
        v = getattr(se, name)
        if isinstance(v, dict) and KEY in v and isinstance(v[KEY], tuple):
            return v[KEY]
    raise AssertionError("no numeric range table carries the cap")


def test_negative_control_a_key_outside_cfg_fields_is_still_dropped():
    from bulk_downloader import app_kernel as k
    DEFAULTS = getattr(k, "DEFAULTS", {})
    cfg_in = {"not_a_real_key_722": 1}
    rebuilt = {kk: cfg_in.get(kk, DEFAULTS.get(kk, "")) for kk in k.CFG_FIELDS}
    assert "not_a_real_key_722" not in rebuilt
