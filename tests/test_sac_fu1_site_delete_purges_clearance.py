"""sac-fu1 C3c: a deleted site's cached captcha clearance outlived the site.

The relay caches a solved clearance under clearance_scope_for_site(site) =
"site=<id>|egress=<ip>" for up to 7 days. DELETE /api/sites/<sid> never
dropped it, so a site recreated with the same id (same egress, same domain)
was served the deleted site's clearance. The delete now purges every
clearance the site was cached under -- each egress it was ever bound to,
not just the current one -- and leaves other sites' clearances alone.

Direct-call delete (no CSRF/before_request) via app_state + a request
context, as test_bug1_idle_site_delete.py does.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

import importlib

import pytest

pytestmark = pytest.mark.bd_module_wipe

DOMAIN = "shared.test"
URL_1 = f"https://{DOMAIN}/gallery/1"
URL_2 = f"https://{DOMAIN}/gallery/2"
URL_3 = f"https://{DOMAIN}/gallery/3"
URL_OTHER = f"https://{DOMAIN}/gallery/other"


@pytest.fixture
def env(isolated_bd_home, monkeypatch):
    from flask import Flask

    st = importlib.import_module("bulk_downloader.app_state")
    app = importlib.import_module("bulk_downloader.app").app
    site_core = importlib.import_module("bulk_downloader.app_sites_id_core")
    relay_api = importlib.import_module("bulk_downloader.app_captcha_relay")
    cr = importlib.import_module("bulk_downloader.captcha_relay")
    ei = importlib.import_module("bulk_downloader.egress_identity")
    tm = importlib.import_module("bulk_downloader.login_impl.token_manager")

    tm.reset_cache_for_tests()
    cr._reset_for_tests()
    monkeypatch.setattr(cr, "_maybe_push", lambda *_: None)
    monkeypatch.setattr(ei, "_observed", {})
    monkeypatch.setattr(ei, "_carrier_of_site", {})
    started = []
    cr.register_takeover_starter(
        lambda site_id, target_url: started.append(site_id) or {"session_id": f"s-{site_id}", "url": target_url})
    relay = Flask("sac_fu1_c3c")
    relay_api.register_routes(relay)
    yield {"st": st, "app": app, "api_delete": site_core.api_delete, "relay": relay.test_client(),
           "cr": cr, "ei": ei, "cache": tm.get_default_cache(), "started": started}
    tm.reset_cache_for_tests()
    cr._reset_for_tests()


def _solve(e, site_id, url, secret):
    e["cr"].mark_captcha_needed(site_id, url, "turnstile")
    r = e["relay"].post("/api/captcha/resolved", json={
        "url": url, "site_id": site_id, "clearance": [{"name": "cf_clearance", "value": secret}]})
    assert r.status_code == 200, r.get_json()


def _bind(e, site_id, carrier, ip):
    e["ei"].bind_site_carrier(site_id, carrier)
    e["ei"].observe_egress_ip(carrier, ip)


def _add_site(e, sid):
    e["st"].s_cfg[sid] = {"url": f"https://{DOMAIN}/", "name": sid}
    e["st"].s_meta[sid] = {"status": "idle"}
    e["st"].runners.pop(sid, None)


def _delete(e, sid):
    with e["app"].test_request_context(f"/api/sites/{sid}", method="DELETE"):
        resp = e["api_delete"](sid)
    status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
    assert status == 200, resp


def test_c3c_delete_purges_every_scope_and_recreated_site_is_not_served(env):
    e = env
    _add_site(e, "siteA")
    _bind(e, "siteA", "tun-1", "198.51.100.1")
    _solve(e, "siteA", URL_1, "CLR-OLD-EGRESS")
    scope_old = e["ei"].clearance_scope_for_site("siteA")
    _bind(e, "siteA", "tun-2", "198.51.100.2")             # egress changed before the delete
    _solve(e, "siteA", URL_2, "CLR-CUR-EGRESS")
    scope_cur = e["ei"].clearance_scope_for_site("siteA")
    assert scope_old != scope_cur
    assert e["cache"].get_clearance(scope_old, DOMAIN) is not None   # probe can see a cached clearance
    assert e["cache"].get_clearance(scope_cur, DOMAIN) is not None

    _delete(e, "siteA")
    left = {s: e["cache"].get_clearance(s, DOMAIN) for s in (scope_old, scope_cur)}
    assert left == {scope_old: None, scope_cur: None}, (
        f"SAC-FU1-C3C-RECREATED-INHERITS: deleted site's clearance still cached: {left}")

    _add_site(e, "siteA")                                    # recreated, same id + egress + domain
    e["cr"].mark_captcha_needed("siteA", URL_3, "turnstile")
    body = e["relay"].post("/api/captcha/start_solve", json={"url": URL_3}).get_json()
    assert body.get("cached") is not True and e["started"] == ["siteA"], (
        f"SAC-FU1-C3C-RECREATED-INHERITS: recreated site served the deleted site's clearance: {body}")


def test_c3c_delete_leaves_other_sites_clearance_control(env):
    e = env
    _add_site(e, "siteA")
    _add_site(e, "siteB")
    _solve(e, "siteA", URL_1, "CLR-A")
    _solve(e, "siteB", URL_OTHER, "CLR-B")
    _delete(e, "siteA")
    kept = e["cache"].get_clearance(e["ei"].clearance_scope_for_site("siteB"), DOMAIN)
    assert kept == [{"name": "cf_clearance", "value": "CLR-B"}], (
        f"SAC-FU1-C3C-CONTROL: deleting siteA dropped siteB's clearance: {kept}")
