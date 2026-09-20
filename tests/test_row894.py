"""RED/GREEN test for row894: authorization token lifecycle cache.

ACCEPTANCE (ROW 894 / O883): (1) token reuse across successive client sessions,
(2) automatic renewal on expiration, (3) cache invalidation on HTTP 401.
"""

from bulk_downloader.login_impl.token_manager import AuthTokenCache

BD_GATE_SCOPE = "module"


def _counting_fetch(tokens):
    calls = {"n": 0}

    def fetch():
        token = tokens[calls["n"]]
        calls["n"] += 1
        return token, 60.0

    return fetch, calls


def test_token_reused_across_successive_sessions():
    fetch, calls = _counting_fetch(["tok-a", "tok-b"])
    cache = AuthTokenCache()

    first = cache.get("203.0.113.5", "example.test", fetch)
    second = cache.get("203.0.113.5", "example.test", fetch)

    assert first == "tok-a"
    assert second == "tok-a"
    assert calls["n"] == 1


def test_token_auto_renews_on_expiration():
    fetch, calls = _counting_fetch(["tok-a", "tok-b"])
    now = {"t": 1000.0}
    cache = AuthTokenCache(clock=lambda: now["t"])

    first = cache.get("203.0.113.5", "example.test", fetch)
    now["t"] += 61.0  # past the 60s ttl
    second = cache.get("203.0.113.5", "example.test", fetch)

    assert first == "tok-a"
    assert second == "tok-b"
    assert calls["n"] == 2


def test_cache_invalidated_on_401():
    fetch, calls = _counting_fetch(["tok-a", "tok-b"])
    cache = AuthTokenCache()

    cache.get("203.0.113.5", "example.test", fetch)
    cache.on_response("203.0.113.5", "example.test", 401)
    second = cache.get("203.0.113.5", "example.test", fetch)

    assert second == "tok-b"
    assert calls["n"] == 2


def test_distinct_keys_do_not_share_tokens():
    fetch, calls = _counting_fetch(["tok-a", "tok-b"])
    cache = AuthTokenCache()

    a = cache.get("203.0.113.5", "example.test", fetch)
    b = cache.get("203.0.113.5", "other.test", fetch)

    assert a == "tok-a"
    assert b == "tok-b"
    assert calls["n"] == 2


# ── FIXER (row894 REFUTE E2/E3) ──────────────────────────────────────────


def test_401_during_in_flight_fetch_is_not_republished():
    """E2: an invalidation that arrives while a fetch for the same key is
    running must survive that fetch -- the next get() re-fetches instead of
    reusing the token the site already rejected."""
    import threading

    cache = AuthTokenCache(clock=lambda: 1000.0)
    entered, resume = threading.Event(), threading.Event()
    returned = []

    def slow_fetch():
        entered.set()
        assert resume.wait(5)
        return "rejected-token", 60.0

    worker = threading.Thread(
        target=lambda: returned.append(cache.get("203.0.113.1", "fixture.invalid", slow_fetch)))
    worker.start()
    try:
        assert entered.wait(5)
        cache.on_response("203.0.113.1", "fixture.invalid", 401)
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert returned == ["rejected-token"]  # the in-flight caller still gets its token

    fresh, calls = _counting_fetch(["new-token"])
    assert cache.get("203.0.113.1", "fixture.invalid", fresh) == "new-token"
    assert calls["n"] == 1


def test_ttl_is_elapsed_time_not_wall_clock(monkeypatch):
    """E3: the default clock is monotonic -- a wall-clock step backwards
    must not resurrect an expired token."""
    import time as _time
    from bulk_downloader.login_impl import token_manager

    elapsed = [0.0]
    wall = [1000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(_time, "time", lambda: wall[0])
    cache = token_manager.AuthTokenCache()
    assert cache._clock() == elapsed[0]

    fetch, calls = _counting_fetch(["t1", "t2"])
    assert cache.get("203.0.113.1", "fixture.invalid", fetch) == "t1"
    elapsed[0] = 61.0     # TTL 60s elapsed
    wall[0] = 971.0       # wall clock stepped back 90s meanwhile
    assert cache.get("203.0.113.1", "fixture.invalid", fetch) == "t2"
    assert calls["n"] == 2
