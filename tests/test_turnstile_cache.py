"""Acceptance tests for Row 894: VERIFICATION-TOKEN-CACHING-AND-REUSE-ENGINE.

ACCEPTANCE (Row 894 / O883 / O961):
(1) token reuse across repeated sessions
(2) automatic renewal when upstream verification expires
(3) non-blocking fallback if cache is empty
"""
import time
from unittest.mock import MagicMock

import pytest

from bulk_downloader.login_impl.token_manager import AuthTokenCache, TurnstileCache, get_default_cache, reset_cache_for_tests
from bulk_downloader import cloak
from bulk_downloader import app_captcha_relay
from bulk_downloader import captcha_relay

BD_GATE_SCOPE = "module"


def _bind_egress(site_id, carrier, ip):
    from bulk_downloader import egress_identity
    egress_identity.bind_site_carrier(site_id, carrier)
    egress_identity.observe_egress_ip(carrier, ip)


@pytest.fixture(autouse=True)
def clean_cache():
    reset_cache_for_tests()
    captcha_relay._reset_for_tests()
    yield
    reset_cache_for_tests()
    captcha_relay._reset_for_tests()


def test_token_reuse_across_repeated_sessions_unit():
    """(1) Token reuse across repeated client sessions at cache level."""
    now = {"t": 1000.0}
    cache = TurnstileCache(clock=lambda: now["t"])
    calls = {"n": 0}

    def fetch_clearance():
        calls["n"] += 1
        return [{"name": "cf_clearance", "value": f"val-{calls['n']}", "domain": ".example.test"}], 3600.0

    # Session 1: initial fetch
    tok1 = cache.get_clearance("198.51.100.1", "example.test", fetch=fetch_clearance)
    assert calls["n"] == 1
    assert tok1[0]["value"] == "val-1"

    # Session 2: repeated session uses cached clearance without re-fetching
    tok2 = cache.get_clearance("198.51.100.1", "example.test", fetch=fetch_clearance)
    assert calls["n"] == 1
    assert tok2 == tok1

    # Session 3: third session also reuses cached clearance
    tok3 = cache.get_clearance("198.51.100.1", "example.test", fetch=fetch_clearance)
    assert calls["n"] == 1
    assert tok3 == tok1


def test_token_reuse_across_repeated_sessions_cloak(monkeypatch):
    """(1) Token reuse across repeated sessions wired into cloak context."""
    cache = get_default_cache()
    clearance_cookie = [{"name": "cf_clearance", "value": "test-clearance-123", "domain": ".site.test"}]
    cache.set_clearance("203.0.113.10", "site.test", clearance_cookie, ttl_seconds=3600.0)

    # Mock open_persistent_context underlying launcher
    mock_context = MagicMock()
    monkeypatch.setattr(cloak, "_CLOAK_LPC", lambda **kw: mock_context)
    monkeypatch.setattr(cloak, "resolve_backend", lambda *a, **k: cloak.CLOAKBROWSER)

    ctx, pw, backend = cloak.open_persistent_context(
        user_data_dir="/tmp/test-profile",
        domain="site.test",
        egress_ip="203.0.113.10",
    )
    # Clearance cookies from cache were applied to context
    mock_context.add_cookies.assert_called_once_with(clearance_cookie)


def test_token_reuse_across_repeated_sessions_captcha_relay(monkeypatch):
    """(1) Token reuse across repeated sessions wired into app_captcha_relay."""
    from flask import Flask

    app = Flask("test_app")
    app_captcha_relay.register_routes(app)
    client = app.test_client()

    url = "https://protected.test/gallery"
    captcha_relay.mark_captcha_needed("site1", url, "turnstile")
    # the egress identity is the SERVER's observation for site1's carrier
    _bind_egress("site1", "tun-a", "198.51.100.5")

    cache = get_default_cache()
    cookie_payload = [{"name": "cf_clearance", "value": "solved-turnstile-token"}]
    cache.set_clearance("198.51.100.5", "protected.test", cookie_payload, ttl_seconds=3600.0)

    # Session start with cached clearance available (no egress_ip in the body: it is not the client's to say)
    res = client.post("/api/captcha/start_solve", json={"url": url})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data.get("cached") is True
    assert data["session"]["status"] == "resolved"
    assert data["session"]["clearance"] == cookie_payload


def test_automatic_renewal_when_upstream_verification_expires():
    """(2) Automatic renewal on expiration."""
    now = {"t": 2000.0}
    cache = TurnstileCache(clock=lambda: now["t"])
    calls = {"n": 0}

    def fetch_clearance():
        calls["n"] += 1
        return [{"name": "cf_clearance", "value": f"token-{calls['n']}"}], 100.0

    # Initial verification
    t1 = cache.get_clearance("198.51.100.1", "renew.test", fetch=fetch_clearance)
    assert calls["n"] == 1
    assert t1[0]["value"] == "token-1"

    # Advance clock past TTL (100s) -> 101s later
    now["t"] += 101.0

    # Next session requests clearance -> automatically triggers renewal
    t2 = cache.get_clearance("198.51.100.1", "renew.test", fetch=fetch_clearance)
    assert calls["n"] == 2
    assert t2[0]["value"] == "token-2"

    # Further call within new TTL reuses renewed token
    now["t"] += 50.0
    t3 = cache.get_clearance("198.51.100.1", "renew.test", fetch=fetch_clearance)
    assert calls["n"] == 2
    assert t3[0]["value"] == "token-2"


def test_non_blocking_fallback_if_cache_is_empty_unit():
    """(3) Non-blocking fallback if cache is empty at cache level."""
    cache = TurnstileCache()
    start = time.monotonic()
    # Cache lookup with no fetch should immediately return None without blocking
    result = cache.get_clearance("198.51.100.1", "uncached.test")
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 0.1  # non-blocking


def test_non_blocking_fallback_if_cache_is_empty_cloak(monkeypatch):
    """(3) Non-blocking fallback if cache is empty wired into cloak context."""
    mock_context = MagicMock()
    monkeypatch.setattr(cloak, "_CLOAK_LPC", lambda **kw: mock_context)
    monkeypatch.setattr(cloak, "resolve_backend", lambda *a, **k: cloak.CLOAKBROWSER)

    # Empty cache
    reset_cache_for_tests()
    start = time.monotonic()
    ctx, pw, backend = cloak.open_persistent_context(
        user_data_dir="/tmp/test-profile-cold",
        domain="cold-site.test",
        egress_ip="198.51.100.1",
    )
    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    # No cookies were injected, but context opened cleanly
    mock_context.add_cookies.assert_not_called()


def test_non_blocking_fallback_if_cache_is_empty_captcha_relay(monkeypatch):
    """(3) Non-blocking fallback if cache is empty wired into captcha relay."""
    from flask import Flask

    app = Flask("test_app_cold")
    app_captcha_relay.register_routes(app)
    client = app.test_client()

    url = "https://cold.test/challenge"
    captcha_relay.mark_captcha_needed("site2", url, "turnstile")

    # Register takeover starter
    starter_called = {"n": 0}

    def dummy_starter(site_id, target_url):
        starter_called["n"] += 1
        return {"session_id": "solve-cold-1", "url": target_url}

    captcha_relay.register_takeover_starter(dummy_starter)

    start = time.monotonic()
    res = client.post("/api/captcha/start_solve", json={"url": url})
    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    # Fell back to real takeover starter without blocking
    assert starter_called["n"] == 1
    assert data["session"]["session_id"] == "solve-cold-1"


def test_egress_ip_and_domain_isolation():
    """Clearance tokens are strictly partitioned by (egress_ip, domain)."""
    cache = TurnstileCache()
    c1 = [{"name": "cf_clearance", "value": "ip1-dom1"}]
    c2 = [{"name": "cf_clearance", "value": "ip2-dom1"}]
    c3 = [{"name": "cf_clearance", "value": "ip1-dom2"}]

    cache.set_clearance("1.1.1.1", "a.test", c1)
    cache.set_clearance("2.2.2.2", "a.test", c2)
    cache.set_clearance("1.1.1.1", "b.test", c3)

    assert cache.get_clearance("1.1.1.1", "a.test") == c1
    assert cache.get_clearance("2.2.2.2", "a.test") == c2
    assert cache.get_clearance("1.1.1.1", "b.test") == c3
    assert cache.get_clearance("2.2.2.2", "b.test") is None


def test_invalidation_on_401_and_dismiss():
    """Cache invalidation on HTTP 401 or dismiss."""
    cache = TurnstileCache()
    cache.set_clearance("1.1.1.1", "a.test", [{"name": "cf_clearance", "value": "val"}])
    assert cache.get_clearance("1.1.1.1", "a.test") is not None

    cache.on_response("1.1.1.1", "a.test", 401)
    assert cache.get_clearance("1.1.1.1", "a.test") is None


# ---- fixer (O928): shape REFUTE -- the cache key is the SERVER's egress identity ----

def test_routes_ignore_a_client_supplied_egress_ip_and_scope_by_the_servers_identity():
    """A caller naming another session's egress_ip in the JSON body must not
    read, write or invalidate that session's cached clearance: all three
    routes key on egress_identity.egress_ip_for_site(pending.site_id)."""
    from flask import Flask
    app = Flask("test_app")
    app_captcha_relay.register_routes(app)
    client = app.test_client()
    cache = get_default_cache()
    victim_ip, own_ip = "203.0.113.7", "198.51.100.9"
    victim = [{"name": "cf_clearance", "value": "victim-clearance"}]
    cache.set_clearance(victim_ip, "protected.test", victim, ttl_seconds=3600.0)

    url = "https://protected.test/gallery"
    captcha_relay.mark_captcha_needed("site-own", url, "turnstile")
    _bind_egress("site-own", "tun-own", own_ip)
    started = {"n": 0}
    captcha_relay.register_takeover_starter(lambda site_id, target_url: started.__setitem__("n", started["n"] + 1) or {"session_id": "s1", "url": target_url})

    # READ: naming the victim's IP does not serve the victim's clearance
    res = client.post("/api/captcha/start_solve", json={"url": url, "egress_ip": victim_ip})
    assert res.status_code == 200 and res.get_json().get("cached") is not True and started["n"] == 1

    # WRITE: a resolved clearance lands under the server's identity, not the named one
    mine = [{"name": "cf_clearance", "value": "own-clearance"}]
    res = client.post("/api/captcha/resolved", json={"url": url, "clearance": mine, "egress_ip": victim_ip})
    assert res.status_code == 200
    assert cache.get_clearance(own_ip, "protected.test") == mine
    assert cache.get_clearance(victim_ip, "protected.test") == victim          # untouched

    # INVALIDATE: dismiss with the victim's IP named clears only the server-scoped entry
    captcha_relay.mark_captcha_needed("site-own", url, "turnstile")
    res = client.post("/api/captcha/dismiss", json={"url": url, "egress_ip": victim_ip})
    assert res.status_code == 200
    assert cache.get_clearance(own_ip, "protected.test") is None
    assert cache.get_clearance(victim_ip, "protected.test") == victim          # still there

    # positive control: with the server's identity matching, the cached clearance IS served
    cache.set_clearance(own_ip, "protected.test", mine, ttl_seconds=3600.0)
    captcha_relay.mark_captcha_needed("site-own", url, "turnstile")
    res = client.post("/api/captcha/start_solve", json={"url": url})
    assert res.get_json().get("cached") is True and res.get_json()["session"]["clearance"] == mine
    # a URL with no pending record has no site: UNKNOWN identity, never a body value
    assert app_captcha_relay._server_side_egress_ip("https://nowhere.test/x") == "UNKNOWN"
