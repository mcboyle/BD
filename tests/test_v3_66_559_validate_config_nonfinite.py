"""
F-COREBD06-01 (v3.66.559): site_editor.validate_config must reject non-finite
numerics (NaN / +-inf) in NUMERIC_RANGES fields -- mirroring the guarded sibling
validate_numeric_updates (VR-P08 / I0004, v3.66.523).

validate_config's numeric loop did ``n = float(cfg[field])`` then
``if n < lo or n > hi``. NaN slips: ``nan < lo`` and ``nan > hi`` are BOTH False,
so a config carrying wait=NaN validated as ok=True and could persist into
sites_config.json via a hand-edit or import. (+-inf were already caught by the
range comparison, but are now rejected explicitly with a clearer 'finite number'
message so the two validators cannot diverge.)

RED on 3.66.558: validate_config({"name": "s", "wait": float("nan")}) -> ok=True,
                 and no error mentions 'wait'.
GREEN after:     ok=False, an error mentions 'wait', matching validate_numeric_updates.
"""
from bulk_downloader.site_editor import (
    validate_config,
    validate_numeric_updates,
    NUMERIC_RANGES,
)

# REQUIRED_FIELDS == ("name",); this base validates clean on its own, so the only
# thing under test is the injected non-finite numeric.
BASE = {"name": "testsite"}


def test_validate_config_rejects_nan_in_numeric_range():
    # 'wait' is a NUMERIC_RANGES field (0..120) and NOT int-typed.
    assert "wait" in NUMERIC_RANGES
    res = validate_config(dict(BASE, wait=float("nan")))
    assert res["ok"] is False, "NaN in a numeric field must be rejected (VR-P08)"
    assert any("wait" in e for e in res["errors"]), (
        f"expected a 'wait' error for NaN; got {res['errors']}")


def test_validate_config_rejects_inf_both_signs():
    for bad in (float("inf"), float("-inf")):
        res = validate_config(dict(BASE, wait=bad))
        assert res["ok"] is False, f"{bad!r} must be rejected"
        assert any("wait" in e for e in res["errors"])


def test_validate_config_parity_with_numeric_updates_on_nan():
    # the two validators share NUMERIC_RANGES and must AGREE that NaN is invalid.
    vc = validate_config(dict(BASE, wait=float("nan")))
    vn = validate_numeric_updates({"wait": float("nan")})
    assert vc["ok"] is False, "validate_config must reject NaN"
    assert "wait" in vn, "validate_numeric_updates must reject NaN (already guarded)"


def test_validate_config_accepts_finite_no_over_rejection():
    # over-rejection guard: legitimate finite values (incl. 0, boundary, numeric
    # string) must NOT be flagged on 'wait'.
    for good in (30, 0, 120, "45"):
        res = validate_config(dict(BASE, wait=good))
        assert not any("wait" in e for e in res["errors"]), (
            f"finite wait={good!r} wrongly rejected; errors={res['errors']}")
