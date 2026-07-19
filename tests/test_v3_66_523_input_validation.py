"""v3.66.523 -- input-validation / error-contract fixes (VR-P08/P09/P12).

VR-P11 (numeric backstop type laxity) is intentionally NOT in this cut: as
literally specified ("reject '8'/3.7") it conflicts with the deliberately-pinned
test_put_numeric_range_backstop::test_helper_numeric_string_is_range_checked
(numeric strings like "1080" MUST be accepted and range-checked, because a
string-preflight upstream can coerce a value to str), and "int field" cannot be
derived from NUMERIC_RANGES (e.g. disk_threshold_gb legitimately takes floats).
Deferred for an explicit ruling rather than guessed.

RED on pristine v3.66.522:
  * VR-P08  validate_numeric_updates({"wait": NaN}) -> {} -- NaN slips the range
            backstop (NaN < lo and NaN > hi are BOTH False), so it persists into
            live config via the real PUT. (inf is already rejected -- NaN is the
            gap.) Fix = math.isfinite gate.
  * VR-P12  POST /api/settings/site/<sid>/validate {"updates": {"max_concurrent":
            inf}} -> 500 -- int(inf) raises OverflowError (uncaught by the int
            branch's TypeError/ValueError) and a range-less num field would echo a
            raw inf that jsonify cannot serialize. Fix = finite gate in the int +
            num branches of _validate_updates.
  * VR-P09  Non-object JSON body ([1,2,3]) -> 500 on /api handlers whose OWN
            try/except swallows the body.get AttributeError before the global
            handler can return 400 (the validate endpoint, macros save/replay),
            AND -> unhandled 500 on /cockpit/api/* because _on_attribute_error
            only rescued paths starting "/api/". Fix = extend the global prefix to
            "/cockpit/api/" + dict-guard the own-except handlers (return 400).
"""
from __future__ import annotations

import json
import os


# --------------------------------------------------------------------------
# VR-P08 -- NaN must not slip the numeric backstop (write-path corruption)
# --------------------------------------------------------------------------
def test_vr_p08_nan_rejected_by_numeric_backstop():
    from bulk_downloader import site_editor as se
    errs = se.validate_numeric_updates({"wait": float("nan")})
    assert "wait" in errs, f"VR-P08: NaN slipped the numeric backstop (would persist): {errs}"
    # inf was already caught -- keep that behavior.
    assert "wait" in se.validate_numeric_updates({"wait": float("inf")})
    # finite in-range still accepted.
    assert se.validate_numeric_updates({"wait": 5}) == {}


def test_vr_p08_nan_not_persisted_via_real_put():
    """E2E: the audited PUT path must reject a NaN and leave the on-disk config
    unchanged (the confirmed corruption was nan persisting to sites config)."""
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(
        json.dumps({"demo": {"name": "Demo", "max_concurrent": 4, "wait": 5}}),
        encoding="utf-8")
    a._load_sites_config()
    if "demo" in a.runners:
        a.runners["demo"].update_config = lambda *_a, **_k: None
    c = a.app.test_client()
    r = c.put("/api/sites/demo", json={"wait": float("nan")})
    assert r.status_code == 400, f"VR-P08: PUT accepted NaN (status {r.status_code})"
    on_disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]
    assert on_disk["wait"] == 5, f"VR-P08: NaN persisted to config: {on_disk.get('wait')!r}"


# --------------------------------------------------------------------------
# VR-P12 -- inf must not 500 the validate endpoint
# --------------------------------------------------------------------------
def test_vr_p12_inf_does_not_500_validate_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    # max_concurrent is integer-typed -> the pristine bug is int(inf) OverflowError.
    r = c.post("/api/settings/site/x/validate",
               json={"updates": {"max_concurrent": float("inf")}})
    body = r.get_data(as_text=True)[:200]
    assert r.status_code != 500, f"VR-P12: inf 500s the validate endpoint -> {body}"
    assert r.status_code == 200, f"VR-P12: expected 200 (rejected echo), got {r.status_code} {body}"
    j = r.get_json()
    assert "max_concurrent" in (j.get("rejected") or {}), \
        f"VR-P12: inf not reported as rejected: {j}"


# --------------------------------------------------------------------------
# VR-P09 part-1 -- global AttributeError handler covers /cockpit/api/
# --------------------------------------------------------------------------
def test_vr_p09_attribute_error_handler_covers_cockpit_api():
    from bulk_downloader import app as a
    with a.app.test_request_context("/cockpit/api/anything", method="POST"):
        try:
            resp = a._on_attribute_error(AttributeError("body type mismatch"))
        except AttributeError:
            resp = None
        assert resp is not None, "VR-P09: /cockpit/api/ AttributeError re-raised (HTML 500)"
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        assert status == 400, f"VR-P09: expected 400 for /cockpit/api/, got {status}"
    # /api/ still works; a non-API path still re-raises (unchanged).
    with a.app.test_request_context("/api/x", method="POST"):
        resp = a._on_attribute_error(AttributeError("x"))
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        assert status == 400


# --------------------------------------------------------------------------
# VR-P09 part-2 -- own-except /api handlers return 400 (not 500) on array body
# --------------------------------------------------------------------------
def test_vr_p09_array_body_is_400_not_500_on_own_except_handlers():
    from bulk_downloader import app as a
    c = a.app.test_client()
    samples = [
        ("/api/settings/site/x/validate", "POST"),
        ("/api/macros/save", "POST"),
        ("/api/macros/replay/x/y", "POST"),
    ]
    for path, method in samples:
        r = c.open(path, method=method, json=[1, 2, 3])
        assert r.status_code != 500, \
            f"VR-P09: {path} 500s on array body (own except swallowed it) -> {r.get_data(as_text=True)[:160]}"
        assert r.status_code == 400, \
            f"VR-P09: {path} expected 400 on array body, got {r.status_code}"
