"""sac-fu1 C4: the captcha relay let any caller write or drop another site's clearance.

/api/captcha/resolved and /api/captcha/dismiss took a bare {url}: the
clearance scope came from the pending record (sac-f9), but nothing tied the
CALLER to that site, so a caller acting for siteB that named siteA's pending
URL wrote its clearance into siteA's scope (cache poison) or invalidated
siteA's cached clearance. A clearance write now requires body.site_id equal
to the site that owns the pending URL (409 "site mismatch" before the URL is
resolved, so it stays pending for its owner); a dismiss naming a different
site is refused the same way. A bare {url} resolve/dismiss (the dashboard)
is unchanged.

sac-fu1 cloak: _apply_clearance_to_context / save_clearance_from_context
keyed the cache by (egress or "UNKNOWN", domain) -- the cross-site shape
sac-f9 removed from the relay. They now use the same site scope.
"""
from __future__ import annotations

import logging

import pytest

BD_GATE_SCOPE = "module"

SECRET = "CLR-SECRET-FU1"
DOMAIN = "shared.test"
URL_A = f"https://{DOMAIN}/gallery/a"
URL_A2 = f"https://{DOMAIN}/gallery/a2"
URL_B = f"https://{DOMAIN}/gallery/b"
CLEARANCE = [{"name": "cf_clearance", "value": SECRET}]


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
    captcha_relay.register_takeover_starter(
        lambda site_id, target_url: {"session_id": f"s-{site_id}", "url": target_url})
    captcha_relay.mark_captcha_needed("siteA", URL_A, "turnstile")
    captcha_relay.mark_captcha_needed("siteB", URL_B, "turnstile")
    app = Flask("sac_fu1")
    app_captcha_relay.register_routes(app)
    yield app.test_client()
    reset_cache_for_tests()
    captcha_relay._reset_for_tests()


def _cached(site_id):
    from bulk_downloader.egress_identity import clearance_scope_for_site
    from bulk_downloader.login_impl.token_manager import get_default_cache
    return get_default_cache().get_clearance(clearance_scope_for_site(site_id), DOMAIN)


def _status(url):
    from bulk_downloader import captcha_relay
    return (captcha_relay.get_pending(url) or {}).get("status")


@pytest.mark.parametrize("claimed", ["siteB", None], ids=["other-site", "no-site"])
def test_c4_resolved_for_another_sites_url_writes_nothing(client, claimed, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    body = {"url": URL_A, "clearance": CLEARANCE}
    if claimed is not None:
        body["site_id"] = claimed
    r = client.post("/api/captcha/resolved", json=body)
    got = (r.status_code, (r.get_json() or {}).get("error", ""), _cached("siteA"), _status(URL_A))
    assert got[0] == 409 and "site mismatch" in got[1] and got[2] is None and got[3] == "pending", (
        f"SAC-FU1-C4-CROSS-SITE-WRITE (site_id={claimed!r}): siteA's pending URL resolved into siteA's scope: {got}")
    out = capsys.readouterr()
    assert SECRET not in r.get_data(as_text=True)
    assert SECRET not in caplog.text and SECRET not in out.out and SECRET not in out.err


def test_c4_owning_site_still_writes_its_clearance_control(client):
    r = client.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA", "clearance": CLEARANCE})
    assert (r.status_code, _cached("siteA"), _status(URL_A)) == (200, CLEARANCE, "resolved"), (
        f"SAC-FU1-C4-CONTROL: the owning site's clearance was not written: {r.get_json()}")
    assert _cached("siteB") is None


def test_c4_bare_url_resolve_and_dismiss_unchanged_control(client):
    # the dashboard (ImportsCenter.tsx) posts {url} only and carries no clearance
    assert client.post("/api/captcha/resolved", json={"url": URL_A}).status_code == 200
    assert _status(URL_A) == "resolved"
    assert client.post("/api/captcha/dismiss", json={"url": URL_B}).status_code == 200
    assert _status(URL_B) == "dismissed"


def test_c4_dismiss_naming_another_site_keeps_owners_clearance(client):
    from bulk_downloader import captcha_relay
    client.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA", "clearance": CLEARANCE})
    assert _cached("siteA") == CLEARANCE                   # probe can see the clearance
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    r = client.post("/api/captcha/dismiss", json={"url": URL_A2, "site_id": "siteB"})
    got = (r.status_code, (r.get_json() or {}).get("error", ""), _cached("siteA"), _status(URL_A2))
    assert got[0] == 409 and "site mismatch" in got[1] and got[2] == CLEARANCE and got[3] == "pending", (
        f"SAC-FU1-C4-CROSS-SITE-DISMISS: siteB dismissed siteA's URL and dropped siteA's clearance: {got}")


def test_c4_owning_site_dismiss_still_invalidates_control(client):
    from bulk_downloader import captcha_relay
    client.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA", "clearance": CLEARANCE})
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    r = client.post("/api/captcha/dismiss", json={"url": URL_A2, "site_id": "siteA"})
    assert (r.status_code, _cached("siteA"), _status(URL_A2)) == (200, None, "dismissed")


# ─── cloak: same site scope ─────────────────────────────────────────

class _Ctx:
    def __init__(self, cookies=()):
        self._cookies = list(cookies)
        self.added = []

    def cookies(self):
        return list(self._cookies)

    def add_cookies(self, cookies):
        self.added.extend(cookies)


@pytest.fixture
def cache(monkeypatch):
    from bulk_downloader import egress_identity
    from bulk_downloader.login_impl.token_manager import (
        get_default_cache,
        reset_cache_for_tests,
    )

    reset_cache_for_tests()
    monkeypatch.setattr(egress_identity, "_observed", {})
    monkeypatch.setattr(egress_identity, "_carrier_of_site", {})
    yield get_default_cache()
    reset_cache_for_tests()


def test_cloak_save_keys_on_site_scope_not_bare_egress(cache):
    from bulk_downloader import cloak
    from bulk_downloader.egress_identity import clearance_scope_for_site
    ctx = _Ctx([{"name": "cf_clearance", "value": SECRET}, {"name": "session", "value": "x"}])
    assert cloak.save_clearance_from_context(ctx, DOMAIN, egress_ip="198.51.100.9", config={"site_id": "siteA"})
    got = (cache.get_clearance(clearance_scope_for_site("siteA"), DOMAIN),
           cache.get_clearance("198.51.100.9", DOMAIN), cache.get_clearance("UNKNOWN", DOMAIN))
    assert got == ([{"name": "cf_clearance", "value": SECRET}], None, None), (
        f"SAC-FU1-CLOAK-UNSCOPED: cloak saved the clearance outside siteA's scope: {got}")


def test_cloak_apply_reads_only_own_site_scope(cache):
    from bulk_downloader import cloak
    from bulk_downloader.egress_identity import clearance_scope_for_site
    cache.set_clearance(clearance_scope_for_site("siteA"), DOMAIN, CLEARANCE, ttl_seconds=600.0)
    cache.set_clearance("UNKNOWN", DOMAIN, [{"name": "cf_clearance", "value": "BARE"}], ttl_seconds=600.0)
    own, other, none = _Ctx(), _Ctx(), _Ctx()
    res = (cloak._apply_clearance_to_context(own, DOMAIN, config={"site_id": "siteA"}),
           cloak._apply_clearance_to_context(other, DOMAIN, config={"site_id": "siteB"}),
           cloak._apply_clearance_to_context(none, DOMAIN, config={}))
    assert (res, own.added, other.added, none.added) == ((True, False, False), CLEARANCE, [], []), (
        f"SAC-FU1-CLOAK-UNSCOPED: cloak injected a clearance across sites: {res} {other.added} {none.added}")
