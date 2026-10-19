"""dscs-f08 (deep-spa-cockpit-settings F08): numeric settings stored as strings.

Twenty GLOBAL_CONFIG_SCHEMA keys are typed `str` but hold a number (13 whose
safe_default parses as one, 7 with a "" default; the read sites cast). POST /api/global_config only
checked the type, so 'not-a-number' and '-999' answered 200 and were stored,
and takeover_vnc.resolve_ws_port handed a stored '-999' port straight through.
A value that is not a number in the key's range is now a 400 and nothing is
stored; "" (unset -> env/default at the read site) is still accepted.
pc_* tests are positive controls on the same route.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

DIAG = "BD-DSCS-F08-NUMERIC-STR"

_BAD = ("not-a-number", "-999", "1e999999", "nan")


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return A, c, csrf


def _numeric_str_keys():
    """The denominator, derived from the schema: str-typed keys whose safe default is a number."""
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA
    out = set()
    for k, spec in GLOBAL_CONFIG_SCHEMA.items():
        d = spec.get("safe_default")
        if spec.get("type") is str and isinstance(d, str) and d.strip():
            try:
                float(d)
            except ValueError:
                continue
            out.add(k)
    return out | {  # "" default, cast at the read site (the finding's 20-key list)
        "honeypot_score_threshold", "auth_throttle_free", "auth_throttle_base", "auth_throttle_max",
        "secrets_audit_max_bytes", "held_out_stale_days", "lib_reconcile_missing_days"}


@pytest.fixture
def cfg_client():
    A, c, csrf = _client()
    before = dict(A._app_cfg)
    try:
        yield A, c, csrf
    finally:
        A._app_cfg.clear()
        A._app_cfg.update(before)


def _post(c, csrf, body):
    return c.post("/api/global_config", json=body, headers={"X-CSRF-Token": csrf})


def test_denominator_is_every_numeric_string_key():
    from bulk_downloader import global_config as gc
    keys = _numeric_str_keys()
    assert len(keys) == 20 and "captcha_vnc_websocket_port" in keys, sorted(keys)
    assert set(getattr(gc, "NUMERIC_STR_RANGES", {})) == keys, (
        f"{DIAG} range table does not cover the numeric string keys")


def test_non_numeric_and_out_of_range_values_are_400_and_not_stored(cfg_client):
    A, c, csrf = cfg_client
    got = []
    for k in sorted(_numeric_str_keys()):
        for bad in _BAD:
            before = A._app_cfg.get(k)
            r = _post(c, csrf, {k: bad})
            if r.status_code != 400 or A._app_cfg.get(k) != before:
                got.append((k, bad, r.status_code, A._app_cfg.get(k)))
    assert not got, f"{DIAG} accepted: {got}"


def test_port_range(cfg_client):
    A, c, csrf = cfg_client
    got = [(v, _post(c, csrf, {"captcha_vnc_websocket_port": v}).status_code)
           for v in ("0", "65536", "70000", "8444.5")]
    assert all(code == 400 for _, code in got), f"{DIAG} port: {got}"


def test_resolve_ws_port_refuses_an_invalid_stored_value():
    from bulk_downloader.takeover_vnc import resolve_ws_port
    got = {v: resolve_ws_port({"captcha_vnc_websocket_port": v})
           for v in ("-999", "0", "70000", -1, "not-a-number")}
    assert set(got.values()) == {8444}, f"{DIAG} resolve_ws_port: {got}"


def test_pc_valid_values_still_save(cfg_client):
    A, c, csrf = cfg_client
    r = _post(c, csrf, {"captcha_vnc_websocket_port": "8445", "hls_progress_poll_s": "0.5",
                        "challenge_wait_s": "0", "captcha_push_dedupe_s": " 120 ",
                        "live_poll_interval_s": "", "auth_throttle_base": "1.5",
                        "held_out_stale_days": "180"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert A._app_cfg["captcha_vnc_websocket_port"] == "8445"
    assert A._app_cfg["hls_progress_poll_s"] == "0.5"
    assert A._app_cfg["live_poll_interval_s"] == ""
    from bulk_downloader.takeover_vnc import resolve_ws_port
    assert resolve_ws_port({"captcha_vnc_websocket_port": "8445"}) == 8445
    assert resolve_ws_port({"captcha_vnc_websocket_port": 65535}) == 65535
    assert resolve_ws_port({}) == 8444
    # the control from the finding: a choices key with a bogus value was already 400
    assert _post(c, csrf, {"photo_sets": "bogus"}).status_code == 400

