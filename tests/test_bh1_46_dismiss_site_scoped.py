"""BH1-46 (sac-fu1 RESIDUAL): a bare-url /api/captcha/dismiss evicted another site's entry.

There is no per-request site principal, so a POST /api/captcha/dismiss with
{url} only names no requesting site. At BASE it still dismissed the pending
entry AND invalidated the owning site's cached clearance for that domain, so
a caller acting for siteB could make siteA re-solve (availability). The
dismiss is now scoped to the owning site: a bare {url} for a site-owned
pending is refused (409 "site_id required"), the entry stays pending and
the clearance stays cached; {url, site_id == owner} still dismisses and
invalidates (control). A URL with no owning site keeps the bare-url path.

sac-f9 (3): cloak._apply_clearance_to_context / save_clearance_from_context
carried a dead ``egress_ip`` argument left from the (egress|UNKNOWN, domain)
key sac-fu1 removed; the argument is gone (scope derives from the site).
"""
from __future__ import annotations

import inspect

import pytest

BD_GATE_SCOPE = "module"

SECRET = "CLR-SECRET-BH46"
DOMAIN = "shared.test"
URL_A = f"https://{DOMAIN}/gallery/a"
URL_A2 = f"https://{DOMAIN}/gallery/a2"
URL_B = f"https://{DOMAIN}/gallery/b"
URL_NOSITE = f"https://{DOMAIN}/gallery/nosite"
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
    app = Flask("bh1_46")
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


def _solve_a(client):
    r = client.post("/api/captcha/resolved", json={"url": URL_A, "site_id": "siteA", "clearance": CLEARANCE})
    assert r.status_code == 200 and _cached("siteA") == CLEARANCE      # probe can see the clearance


def test_bare_url_dismiss_from_another_site_keeps_owners_entry_and_clearance(client):
    from bulk_downloader import captcha_relay
    _solve_a(client)
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    r = client.post("/api/captcha/dismiss", json={"url": URL_A2})      # siteB's caller: no site_id
    got = (r.status_code, (r.get_json() or {}).get("error", ""), _status(URL_A2), _cached("siteA"))
    assert got[0] == 409 and "site_id required" in got[1] and got[2] == "pending" and got[3] == CLEARANCE, (
        f"BH1-46-CROSS-SITE-BARE-DISMISS: a bare-url dismiss dropped siteA's entry/clearance: {got}")
    assert SECRET not in r.get_data(as_text=True)
    assert _status(URL_B) == "pending"                                  # siteB's own entry untouched


def test_bare_url_dismiss_never_touches_the_other_sites_clearance(client):
    from bulk_downloader import captcha_relay
    _solve_a(client)
    r = client.post("/api/captcha/resolved", json={"url": URL_B, "site_id": "siteB", "clearance": CLEARANCE})
    assert r.status_code == 200 and _cached("siteB") == CLEARANCE
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    assert client.post("/api/captcha/dismiss", json={"url": URL_A2}).status_code == 409
    assert (_cached("siteA"), _cached("siteB")) == (CLEARANCE, CLEARANCE)


def test_same_site_dismiss_still_dismisses_and_invalidates_control(client):
    from bulk_downloader import captcha_relay
    _solve_a(client)
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    r = client.post("/api/captcha/dismiss", json={"url": URL_A2, "site_id": "siteA"})
    got = (r.status_code, _status(URL_A2), _cached("siteA"))
    assert got == (200, "dismissed", None), f"BH1-46-CONTROL: owner's dismiss no longer works: {got}"


def test_other_site_named_dismiss_still_refused_control(client):
    from bulk_downloader import captcha_relay
    _solve_a(client)
    captcha_relay.mark_captcha_needed("siteA", URL_A2, "turnstile")
    r = client.post("/api/captcha/dismiss", json={"url": URL_A2, "site_id": "siteB"})
    assert r.status_code == 409 and "site mismatch" in (r.get_json() or {}).get("error", "")
    assert (_status(URL_A2), _cached("siteA")) == ("pending", CLEARANCE)


def test_unknown_url_bare_dismiss_is_404_not_409_control(client):
    r = client.post("/api/captcha/dismiss", json={"url": "https://nowhere.test/x"})
    assert r.status_code == 404, r.get_json()


def test_siteless_pending_keeps_bare_url_dismiss(client):
    from bulk_downloader import captcha_relay
    captcha_relay.mark_captcha_needed("", URL_NOSITE, "turnstile")
    assert _status(URL_NOSITE) == "pending"                             # probe: the entry exists
    r = client.post("/api/captcha/dismiss", json={"url": URL_NOSITE})
    assert (r.status_code, _status(URL_NOSITE)) == (200, "dismissed"), r.get_json()


# ─── (3) cloak: the dead egress_ip argument is gone ────────────────────

class _Ctx:
    def __init__(self, cookies=()):
        self._cookies = list(cookies)
        self.added = []

    def cookies(self):
        return list(self._cookies)

    def add_cookies(self, cookies):
        self.added.extend(cookies)


@pytest.mark.parametrize("fn", ["_apply_clearance_to_context", "save_clearance_from_context"])
def test_cloak_clearance_helpers_take_no_egress_ip(fn):
    from bulk_downloader import cloak
    f = getattr(cloak, fn)
    assert "egress_ip" not in inspect.signature(f).parameters, (
        f"BH1-46-CLOAK-DEAD-KEY: {fn} still accepts the dormant egress_ip key argument")
    with pytest.raises(TypeError):
        f(_Ctx(), DOMAIN, egress_ip="198.51.100.9", config={"site_id": "siteA"})


def test_cloak_save_and_apply_still_scoped_by_site_control(client):
    from bulk_downloader import cloak
    ctx = _Ctx([{"name": "cf_clearance", "value": SECRET}])
    assert cloak.save_clearance_from_context(ctx, DOMAIN, config={"site_id": "siteA"})
    assert _cached("siteA") == CLEARANCE and _cached("siteB") is None
    own, other = _Ctx(), _Ctx()
    assert cloak._apply_clearance_to_context(own, DOMAIN, config={"site_id": "siteA"}) is True
    assert cloak._apply_clearance_to_context(other, DOMAIN, config={"site_id": "siteB"}) is False
    assert (own.added, other.added) == (CLEARANCE, [])
