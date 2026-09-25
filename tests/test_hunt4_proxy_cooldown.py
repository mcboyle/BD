from bulk_downloader import proxy_pool


def test_expired_cooldown_starts_new_consecutive_failure_streak():
    state = {}
    proxy = "http://proxy.example:8080"
    for _ in range(3):
        proxy_pool.record_result(state, proxy, False, now=0, max_fails=3, cooldown_s=10)

    assert proxy_pool.select_proxy([proxy], state, now=9) is None
    assert proxy_pool.select_proxy([proxy], state, now=10) == proxy

    proxy_pool.record_result(state, proxy, False, now=10, max_fails=3, cooldown_s=10)
    assert state["health"][proxy]["fails"] == 1
    assert proxy_pool.select_proxy([proxy], state, now=10) == proxy

    # lens (B1): the post-expiry streak must still COUNT -- a second and third
    # failure cool the proxy again; a fix that only zeroes "fails" but leaves the
    # stale down_until would reset on every failure and never cool it again.
    proxy_pool.record_result(state, proxy, False, now=11, max_fails=3, cooldown_s=10)
    assert proxy_pool.select_proxy([proxy], state, now=11) == proxy
    proxy_pool.record_result(state, proxy, False, now=12, max_fails=3, cooldown_s=10)
    assert proxy_pool.select_proxy([proxy], state, now=12) is None
    assert state["health"][proxy]["down_until"] == 22
