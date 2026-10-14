"""dev-console-lows r1: dev-console-config FINDINGS F4, F5, F6.

F4  config_snapshot_diff redacted top-level keys only: a secret nested under
    a non-secret key was echoed whole (config_dump uses the recursive _redact).
F5  odd-typed bodies gave 500 (cache_clear), and refusals answered HTTP 200 +
    ok:false (feature_flag_set, config_snapshot/restore/diff, cache_clear), so
    a client reading only the status code saw success.
F6  tls_check dialled every string in `hosts`, serially, up to 30 s each, with
    no bound on the count.

Every assertion message starts with DEV_CONSOLE_LOWS so a failure names the
finding it reopens.
"""
from __future__ import annotations

import json
import secrets
import socket
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

DIAG = "DEV_CONSOLE_LOWS"


def _sentinel(prefix):
    return prefix + secrets.token_hex(12)


@pytest.fixture
def cfg(clean_workdir):
    """The live app config, restored after the test."""
    from bulk_downloader import app as bd_app
    saved = dict(bd_app._app_cfg)
    yield bd_app._app_cfg
    bd_app._app_cfg.clear()
    bd_app._app_cfg.update(saved)


@pytest.fixture
def flags(tmp_path, monkeypatch):
    """feature_flags pointed at a tmp file."""
    from bulk_downloader import feature_flags as ff
    target = Path(tmp_path) / "ff" / "feature_flags.json"
    target.parent.mkdir()
    monkeypatch.setattr(ff, "state_path", lambda: target)
    monkeypatch.setattr(ff, "_CACHE", {})
    monkeypatch.setattr(ff, "_CACHE_MTIME", 0.0)
    return target


# ── F4: config_snapshot_diff redacts nested secrets ──────────────────────

def test_f4_nested_secret_in_an_added_key_is_masked(cfg):
    from bulk_downloader import dev_suite as ds
    pw = _sentinel("pw")
    cfg.pop("probe_nested", None)
    ds.config_snapshot(name="f4_added")
    cfg["probe_nested"] = {"inner_password": pw, "label": "keep-me"}
    r = ds.config_snapshot_diff(name="f4_added")
    blob = json.dumps(r)
    assert pw not in blob, (
        f"{DIAG} F4: secret nested under non-secret key probe_nested -> "
        f"echoed in the diff's added entry")
    added = {a["key"]: a["value"] for a in r["added"]}
    # Structure and non-secret siblings survive the redaction.
    assert added["probe_nested"] == {"inner_password": "***redacted***",
                                     "label": "keep-me"}


def test_f4_nested_secret_in_changed_and_removed_keys_is_masked(cfg):
    from bulk_downloader import dev_suite as ds
    old_pw, new_pw, gone_pw = (_sentinel("pw") for _ in range(3))
    cfg["probe_nested"] = {"inner_password": old_pw, "n": 1}
    cfg["probe_gone"] = [{"api_key": gone_pw}]
    ds.config_snapshot(name="f4_changed")
    cfg["probe_nested"] = {"inner_password": new_pw, "n": 2}
    del cfg["probe_gone"]
    r = ds.config_snapshot_diff(name="f4_changed")
    blob = json.dumps(r)
    leaked = [n for n, v in (("from", old_pw), ("to", new_pw),
                             ("removed", gone_pw)) if v in blob]
    assert not leaked, (
        f"{DIAG} F4: nested secret echoed in the diff's {leaked} value(s)")
    ch = {c["key"]: c for c in r["changed"]}["probe_nested"]
    assert (ch["from"]["n"], ch["to"]["n"]) == (1, 2)
    assert {x["key"]: x["value"] for x in r["removed"]}["probe_gone"] == [
        {"api_key": "***redacted***"}]


def test_f4_control_top_level_secret_still_masked_even_when_empty(cfg):
    from bulk_downloader import dev_suite as ds
    cfg.pop("probe_token", None)
    cfg["ai_api_key"] = _sentinel("ak")
    ds.config_snapshot(name="f4_top")
    cfg["ai_api_key"] = ak = _sentinel("ak")
    cfg["probe_token"] = ""
    r = ds.config_snapshot_diff(name="f4_top")
    assert ak not in json.dumps(r)
    assert {c["key"]: c for c in r["changed"]}["ai_api_key"]["secret"] is True
    assert {a["key"]: a["value"] for a in r["added"]}["probe_token"] == \
        "***redacted***"


def test_f4_route_diff_has_no_nested_secret(fresh_app):
    from bulk_downloader import app as bd_app
    pw = _sentinel("pw")
    assert fresh_app.post("/api/dev/config_snapshot",
                          json={"name": "f4_route"}).status_code == 200
    bd_app._app_cfg["probe_nested"] = {"inner_password": pw}
    try:
        r = fresh_app.get("/api/dev/config_snapshot_diff?name=f4_route")
        assert r.status_code == 200
        hits = r.get_data(as_text=True).count(pw)
        assert hits == 0, (
            f"{DIAG} F4: GET /api/dev/config_snapshot_diff -> nested secret "
            f"sentinel hits={hits}, expected 0")
    finally:
        bd_app._app_cfg.pop("probe_nested", None)


# ── F5: non-object bodies -> 400; refusals are not 200 ───────────────────

BAD_BODIES = [
    ("list", {"json": [1, 2]}),
    ("string", {"json": "x"}),
    ("number", {"json": 7}),
    ("null", {"data": "null", "content_type": "application/json"}),
    ("form", {"data": "name=x",
              "content_type": "application/x-www-form-urlencoded"}),
    ("bad-json", {"data": "{", "content_type": "application/json"}),
]

POST_ROUTES = ["cache_clear", "config_snapshot", "config_restore",
               "feature_flag_set", "feature_flag_delete", "tls_check"]


@pytest.mark.parametrize("route", POST_ROUTES)
def test_f5_non_object_bodies_are_400(fresh_app, flags, route):
    wrong = []
    for label, kw in BAD_BODIES:
        r = fresh_app.post(f"/api/dev/{route}", **kw)
        if r.status_code != 400:
            wrong.append(f"{label}->{r.status_code}")
    assert not wrong, (
        f"{DIAG} F5: POST /api/dev/{route} with a non-object body -> "
        f"{wrong}, expected 400 for each")


def test_f5_empty_body_keeps_the_route_defaults(fresh_app):
    r = fresh_app.post("/api/dev/cache_clear")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert len(r.get_json()["cleared"]) >= 1  # default: every target
    r = fresh_app.post("/api/dev/config_snapshot")
    assert r.status_code == 200 and r.get_json()["ok"] is True, (
        f"{DIAG} F5: POST /api/dev/config_snapshot with no body -> "
        f"{r.status_code}, expected 200 with the default snapshot name")


def test_f5_cache_clear_target_types(fresh_app):
    got = {label: fresh_app.post("/api/dev/cache_clear",
                                 json={"targets": t}).status_code
           for label, t in (("int", 7), ("dict", {"a": 1}),
                            ("list-of-int", [1]), ("unknown", "bogus"))}
    assert got == {"int": 400, "dict": 400, "list-of-int": 400,
                   "unknown": 400}, (
        f"{DIAG} F5: cache_clear odd targets -> {got}, expected 400 each")
    ok = fresh_app.post("/api/dev/cache_clear", json={"targets": ["ffmpeg"]})
    assert ok.status_code == 200 and ok.get_json()["cleared"] == ["ffmpeg"]


def test_f5_refusals_carry_a_failure_status(fresh_app, flags):
    calls = {
        "flag_set bad name": ("post", "/api/dev/feature_flag_set",
                              {"json": {"name": "Bad", "value": True}}),
        "flag_set str value": ("post", "/api/dev/feature_flag_set",
                               {"json": {"name": "probe_b", "value": "true"}}),
        "flag_delete bad name": ("post", "/api/dev/feature_flag_delete",
                                 {"json": {"name": ""}}),
        "snapshot traversal": ("post", "/api/dev/config_snapshot",
                               {"json": {"name": "../x"}}),
        "restore invalid": ("post", "/api/dev/config_restore",
                            {"json": {"name": "a/../b"}}),
        "restore missing": ("post", "/api/dev/config_restore",
                            {"json": {"name": "missing"}}),
        "diff missing": ("get",
                         "/api/dev/config_snapshot_diff?name=missing", {}),
    }
    want = {k: 400 for k in calls}
    want["restore missing"] = want["diff missing"] = 404
    got = {}
    for label, (meth, path, kw) in calls.items():
        r = getattr(fresh_app, meth)(path, **kw)
        assert r.get_json()["ok"] is False, f"{label}: not refused"
        got[label] = r.status_code
    assert got == want, (
        f"{DIAG} F5: refusals -> {got}, expected {want} (200 + ok:false "
        f"reads as success)")


def test_f5_corrupt_snapshot_is_400_and_write_failure_is_500(
        fresh_app, flags, monkeypatch):
    from bulk_downloader import feature_flags as ff
    from bulk_downloader.dev_suite import config_tools
    sd = Path(config_tools._snapshot_dir())
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "notjson.json").write_text("{")
    r = fresh_app.get("/api/dev/config_snapshot_diff?name=notjson")
    assert (r.status_code, r.get_json()["ok"]) == (400, False), (
        f"{DIAG} F5: diff of a non-JSON snapshot -> {r.status_code}")
    monkeypatch.setattr(ff, "_write_locked",
                        lambda p, data: "atomic write failed: probe")
    r = fresh_app.post("/api/dev/feature_flag_set",
                       json={"name": "probe_w", "value": True})
    assert (r.status_code, r.get_json()["ok"]) == (500, False), (
        f"{DIAG} F5: feature_flag_set whose write failed -> "
        f"{r.status_code}, expected 500")


def test_f5_control_successes_stay_200(fresh_app, flags):
    r = fresh_app.post("/api/dev/feature_flag_set",
                       json={"name": "probe_ok", "value": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r = fresh_app.post("/api/dev/feature_flag_delete",
                       json={"name": "probe_ok"})
    assert r.status_code == 200 and r.get_json()["removed"] is True
    r = fresh_app.post("/api/dev/config_snapshot", json={"name": "ok_snap"})
    assert r.status_code == 200
    r = fresh_app.get("/api/dev/config_snapshot_diff?name=ok_snap")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r = fresh_app.post("/api/dev/config_restore", json={"name": "ok_snap"})
    assert r.status_code == 200 and r.get_json()["ok"] is True


# ── F6: tls_check host-count cap ─────────────────────────────────────────

@pytest.fixture
def dials(monkeypatch):
    """Every create_connection is recorded and refused: no network."""
    seen = []

    def _refuse(addr, timeout=None, *a, **k):
        seen.append(addr[0])
        raise OSError("test: no network")
    monkeypatch.setattr(socket, "create_connection", _refuse)
    return seen


def _cap():
    from bulk_downloader.dev_suite import audit_security
    return getattr(audit_security, "TLS_CHECK_HOSTS_MAX", 32)


def test_f6_route_refuses_a_host_list_over_the_cap(fresh_app, dials):
    cap = _cap()
    hosts = [f"h{i}.invalid" for i in range(cap + 1)]
    r = fresh_app.post("/api/dev/tls_check", json={"hosts": hosts})
    assert r.status_code == 400 and not dials, (
        f"{DIAG} F6: tls_check with {cap + 1} hosts -> {r.status_code}, "
        f"{len(dials)} dialled; expected 400 and 0 dialled")
    assert r.get_json()["ok"] is False
    # S30: the count is of the list as sent, before empty names drop out.
    r = fresh_app.post("/api/dev/tls_check", json={"hosts": [""] * 10000})
    assert r.status_code == 400, (
        f"{DIAG} F6: tls_check with 10000 empty hosts -> {r.status_code}")


def test_f6_control_a_list_at_the_cap_is_dialled_once_per_host(
        fresh_app, dials):
    cap = _cap()
    hosts = [f"h{i}.invalid" for i in range(cap - 1)] + ["h0.invalid"]
    r = fresh_app.post("/api/dev/tls_check", json={"hosts": hosts})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert dials == [f"h{i}.invalid" for i in range(cap - 1)], (
        f"{DIAG} F6: a duplicate host was dialled twice")


def test_f5_tls_check_bad_hosts_type_is_400(fresh_app, dials):
    got = {label: fresh_app.post("/api/dev/tls_check",
                                 json={"hosts": h}).status_code
           for label, h in (("str", "x"), ("dict", {"a": 1}))}
    assert got == {"str": 400, "dict": 400}, (
        f"{DIAG} F5: tls_check hosts not a list -> {got}, expected 400")


def test_f6_derived_hosts_are_cut_at_the_cap(dials):
    from bulk_downloader import dev_suite as ds
    cap = _cap()
    sites = {f"s{i}": {"login_url": f"https://h{i:03d}.invalid/login"}
             for i in range(cap + 5)}
    r = ds.tls_cert_check(site_configs=sites, timeout=1)
    assert len(r["hosts_checked"]) == cap and len(dials) == cap, (
        f"{DIAG} F6: {cap + 5} derived hosts -> {len(dials)} dialled, "
        f"expected {cap}")
    assert len(r["hosts_skipped"]) == 5
    assert set(r["hosts_checked"]).isdisjoint(r["hosts_skipped"])


# ── F5: login_flow_save (ANSWER-dispatch-C-builder-Qs-1344Z item 1 (a)) ──

def test_f5_login_flow_save_odd_types_are_400_and_write_nothing(
        fresh_app, clean_workdir):
    md = Path(clean_workdir) / "macros"
    before = set(md.glob("*.json")) if md.exists() else set()
    calls = {
        "site_id=int": {"site_id": 5, "name": "m3", "actions": []},
        "name=list": {"site_id": "s1", "name": ["a"], "actions": []},
        "desc=dict": {"site_id": "s1", "name": "m5", "actions": [],
                      "description": {"a": 1}},
        "bad kind": {"site_id": "s1", "name": "m2",
                     "actions": [{"kind": "rm"}]},
    }
    got = {k: fresh_app.post("/api/dev/login_flow_save",
                             json=v).status_code for k, v in calls.items()}
    reasons = {}
    for label, kw in BAD_BODIES:
        r = fresh_app.post("/api/dev/login_flow_save", **kw)
        got[label] = r.status_code
        reasons[label] = (r.get_json(silent=True) or {}).get("error")
    want = {k: 400 for k in got}
    assert got == want, (
        f"{DIAG} F5: login_flow_save odd bodies -> {got}, expected 400 each")
    # Refused as a non-object body, not by the recorder after reading {}.
    off = {k: v for k, v in reasons.items()
           if v != "request body must be a JSON object"}
    assert not off, f"{DIAG} F5: login_flow_save non-object body reasons {off}"
    after = set(md.glob("*.json")) if md.exists() else set()
    assert after == before, f"{DIAG} F5: refused saves wrote {after - before}"


def test_f5_control_login_flow_save_valid_is_200(fresh_app, clean_workdir):
    r = fresh_app.post("/api/dev/login_flow_save", json={
        "site_id": "s1", "name": "ok_flow", "description": "d",
        "actions": [{"kind": "click", "selector": "#go"}]})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert (Path(clean_workdir) / "macros" / "s1_ok_flow.json").exists()
