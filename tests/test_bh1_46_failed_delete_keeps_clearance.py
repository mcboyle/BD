"""BH1-46 (sac-fu1 ADVISORY): pin "a failed site delete does not purge the clearance".

DELETE /api/sites/<sid> purges every cached captcha clearance of the site
(sac-fu1 C3c) -- but only after the delete transaction succeeded. When the
transaction refuses (503: a keeper generation did not stop) or raises, the
site still exists and must keep its clearance; purging it would make a live
site re-solve. Behaviour was proven by probe only (fu1 mutant M2 survived);
this pins it. Positive control: a successful delete does purge.

Direct-call delete (no CSRF/before_request) via app_state + a request
context, as test_sac_fu1_site_delete_purges_clearance.py does.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib

import pytest

pytestmark = pytest.mark.bd_module_wipe

DOMAIN = "shared.test"
URL_1 = f"https://{DOMAIN}/gallery/1"
CLR = [{"name": "cf_clearance", "value": "CLR-KEEP-BH46"}]


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
    cr.register_takeover_starter(lambda site_id, target_url: {"session_id": f"s-{site_id}", "url": target_url})
    relay = Flask("bh1_46_delete")
    relay_api.register_routes(relay)
    st.s_cfg["siteA"] = {"url": f"https://{DOMAIN}/", "name": "siteA"}
    st.s_meta["siteA"] = {"status": "idle"}
    st.runners.pop("siteA", None)
    cr.mark_captcha_needed("siteA", URL_1, "turnstile")
    r = relay.test_client().post("/api/captcha/resolved", json={"url": URL_1, "site_id": "siteA", "clearance": CLR})
    assert r.status_code == 200, r.get_json()
    scope = ei.clearance_scope_for_site("siteA")
    assert tm.get_default_cache().get_clearance(scope, DOMAIN) == CLR      # probe can see the clearance
    yield {"st": st, "app": app, "core": site_core, "cache": tm.get_default_cache(), "scope": scope,
           "monkeypatch": monkeypatch}
    tm.reset_cache_for_tests()
    cr._reset_for_tests()


def _delete(e):
    with e["app"].test_request_context("/api/sites/siteA", method="DELETE"):
        resp = e["core"].api_delete("siteA")
    return resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)


def test_refused_delete_503_keeps_clearance(env):
    e = env
    e["monkeypatch"].setattr(e["core"], "_stop_site_keeper_generations", lambda sid: False)
    status = _delete(e)
    kept = e["cache"].get_clearance(e["scope"], DOMAIN)
    assert (status, "siteA" in e["st"].s_cfg, kept) == (503, True, CLR), (
        f"BH1-46-FAILED-DELETE-PURGED: a refused delete (rc {status}) dropped the live site's clearance: {kept}")


def test_raising_delete_transaction_keeps_clearance(env):
    e = env

    def _boom(sid, keep_vault_refs_of=None):
        raise RuntimeError("bh1-46 synthetic delete failure")

    e["monkeypatch"].setattr(e["core"], "_api_delete_transaction", _boom)
    with pytest.raises(RuntimeError, match="bh1-46 synthetic"):
        _delete(e)
    kept = e["cache"].get_clearance(e["scope"], DOMAIN)
    assert ("siteA" in e["st"].s_cfg, kept) == (True, CLR), (
        f"BH1-46-FAILED-DELETE-PURGED: a raising delete dropped the live site's clearance: {kept}")


def test_successful_delete_purges_positive_control(env):
    e = env
    assert _delete(e) == 200
    assert e["cache"].get_clearance(e["scope"], DOMAIN) is None, "BH1-46-CONTROL: successful delete did not purge"
