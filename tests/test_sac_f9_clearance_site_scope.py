"""session-auth-capture F9 residual: a cached clearance was shared across sites.

The captcha relay cached a solved clearance under (egress identity, domain).
Every site with no bound egress resolves to UNKNOWN_EGRESS, and two sites
bound to one tunnel share an IP, so siteB's start_solve on the same domain
was answered from siteA's clearance. The cache scope is now
egress_identity.clearance_scope_for_site(site): site AND egress, and no
site means no cache read or write at all.
"""
from __future__ import annotations

import logging

import pytest

BD_GATE_SCOPE = "module"

SECRET = "CLR-SECRET-1"
URL_A = "https://shared.test/gallery/a"
URL_B = "https://shared.test/gallery/b"


@pytest.fixture
def client(monkeypatch):
    from flask import Flask

    from bulk_downloader import app_captcha_relay, captcha_relay, egress_identity
    from bulk_downloader.login_impl.token_manager import reset_cache_for_tests

    reset_cache_for_tests()
    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *_: None)
    monkeypatch.setattr(egress_identity, "_observed", {})
    monkeypatch.setattr(egress_identity, "_carrier_of_site", {})
    started = []
    captcha_relay.register_takeover_starter(
        lambda site_id, target_url: started.append(site_id) or {"session_id": f"s-{site_id}", "url": target_url})
    app = Flask("sac_f9")
    app_captcha_relay.register_routes(app)
    yield app.test_client(), started
    reset_cache_for_tests()
    captcha_relay._reset_for_tests()


def _bind(site_id, carrier, ip):
    from bulk_downloader import egress_identity
    egress_identity.bind_site_carrier(site_id, carrier)
    egress_identity.observe_egress_ip(carrier, ip)


def _solve_a_then_start_b(c):
    from bulk_downloader import captcha_relay
    captcha_relay.mark_captcha_needed("siteA", URL_A, "turnstile")
    captcha_relay.mark_captcha_needed("siteB", URL_B, "turnstile")
    r = c.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA",
                                              "clearance": [{"name": "cf_clearance", "value": SECRET}]})
    assert r.status_code == 200
    return c.post("/api/captcha/start_solve", json={"url": URL_B})


@pytest.mark.parametrize("egress", ["unbound", "shared-tunnel"])
def test_f9_site_b_never_gets_site_a_clearance(client, egress, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    c, started = client
    if egress == "shared-tunnel":
        _bind("siteA", "tun-x", "198.51.100.7")
        _bind("siteB", "tun-x", "198.51.100.7")
    res = _solve_a_then_start_b(c)
    body = res.get_json()
    assert res.status_code == 200
    assert body.get("cached") is not True and started == ["siteB"], (
        f"SAC-F9-SHARED-CLEARANCE ({egress}): siteB start_solve answered from siteA's clearance: {body}")
    out = capsys.readouterr()
    assert SECRET not in res.get_data(as_text=True)
    assert SECRET not in caplog.text and SECRET not in out.out and SECRET not in out.err


def test_f9_same_site_same_bound_egress_still_hits_cache_control(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    c, started = client
    from bulk_downloader import captcha_relay
    _bind("siteA", "tun-a", "198.51.100.5")
    captcha_relay.mark_captcha_needed("siteA", URL_A, "turnstile")
    c.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA",
                                          "clearance": [{"name": "cf_clearance", "value": SECRET}]})
    captcha_relay.mark_captcha_needed("siteA", URL_B, "turnstile")
    res = c.post("/api/captcha/start_solve", json={"url": URL_B})
    body = res.get_json()
    assert body.get("cached") is True and body["session"]["status"] == "resolved" and started == [], (
        f"SAC-F9-CONTROL: same site + same egress lost its cached clearance: {body}")
    out = capsys.readouterr()
    assert SECRET not in res.get_data(as_text=True)
    assert SECRET not in caplog.text and SECRET not in out.out and SECRET not in out.err


def test_f9_scope_is_site_and_egress_and_no_site_has_none():
    from bulk_downloader import egress_identity
    scope = egress_identity.clearance_scope_for_site
    assert scope("siteA") != scope("siteB")
    assert scope("siteA") == "site=siteA|egress=UNKNOWN"
    assert scope(None) is None and scope("") is None
