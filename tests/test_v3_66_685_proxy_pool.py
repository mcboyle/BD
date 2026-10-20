"""v3.66.685 (F4) — rotating proxy pool with health checks.

Egress-resilience infra (charter: same class as the shipped per-site
egress / VPN kill-switch; NOT anti-bot evasion). Per-site egress ships a
single static `proxy`; this adds an opt-in rotating POOL: round-robin over
healthy members, with health tracked by cooldown after failures and by an
active probe (injected, so unit tests never touch the network). Wired into
runner_transport._download_proxy_url behind the `proxy_pool` site-cfg key
(default off -> zero behavior change); an explicit single `proxy` still
wins, and the VPN fail-closed posture is untouched (the pool pick simply
becomes the `explicit_proxy` fed to the unchanged effective_download_proxy).
"""
import pytest

from bulk_downloader import proxy_pool as pp


# BH1-34 / RULING-pm-B B.1: a proxy host must resolve (fail closed otherwise). These
# fixture proxy names resolve to a LAN literal through outbound_guard's declared seam.
_F14_PROXY_HOSTS = frozenset({'a', 'b', 'explicit'})


@pytest.fixture(autouse=True)
def _f14_fixture_proxy_hosts_resolve(monkeypatch):
    import socket
    from bulk_downloader import outbound_guard as og
    real = og._resolve

    def resolve(host, port):
        if host in _F14_PROXY_HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", port))]
        return real(host, port)

    monkeypatch.setattr(og, "_resolve", resolve)


# ── select_proxy: round-robin over healthy members ──────────────────

def test_select_round_robins_all_healthy():
    pool = ["http://a", "http://b", "http://c"]
    st = {}
    got = [pp.select_proxy(pool, st, now=0) for _ in range(4)]
    assert got == ["http://a", "http://b", "http://c", "http://a"]


def test_select_empty_pool_returns_none():
    assert pp.select_proxy([], {}, now=0) is None


def test_select_skips_member_in_cooldown():
    pool = ["http://a", "http://b", "http://c"]
    st = {}
    # b fails to threshold -> cooldown
    for _ in range(3):
        pp.record_result(st, "http://b", ok=False, now=0,
                         max_fails=3, cooldown_s=300)
    got = [pp.select_proxy(pool, st, now=10) for _ in range(4)]
    assert got == ["http://a", "http://c", "http://a", "http://c"]


def test_select_all_in_cooldown_returns_none():
    pool = ["http://a", "http://b"]
    st = {}
    for u in pool:
        for _ in range(3):
            pp.record_result(st, u, ok=False, now=0, max_fails=3, cooldown_s=300)
    assert pp.select_proxy(pool, st, now=10) is None


# ── record_result: fail-count + cooldown health machine ─────────────

def test_record_result_marks_down_at_threshold():
    st = {}
    for _ in range(2):
        pp.record_result(st, "http://a", ok=False, now=0, max_fails=3, cooldown_s=300)
    assert pp.healthy_urls(["http://a"], st, now=1) == ["http://a"]   # 2 < 3
    pp.record_result(st, "http://a", ok=False, now=0, max_fails=3, cooldown_s=300)
    assert pp.healthy_urls(["http://a"], st, now=1) == []             # 3 -> down


def test_record_result_ok_clears_a_downed_proxy():
    st = {}
    for _ in range(3):
        pp.record_result(st, "http://a", ok=False, now=0, max_fails=3, cooldown_s=300)
    assert pp.healthy_urls(["http://a"], st, now=1) == []
    pp.record_result(st, "http://a", ok=True, now=1)
    assert pp.healthy_urls(["http://a"], st, now=2) == ["http://a"]


def test_cooldown_expires():
    st = {}
    for _ in range(3):
        pp.record_result(st, "http://a", ok=False, now=0, max_fails=3, cooldown_s=300)
    assert pp.healthy_urls(["http://a"], st, now=100) == []      # still cooling
    assert pp.healthy_urls(["http://a"], st, now=301) == ["http://a"]  # expired


def test_healthy_urls_preserves_pool_order():
    pool = ["http://c", "http://a", "http://b"]
    assert pp.healthy_urls(pool, {}, now=0) == pool


# ── probe_pool: active health sweep via injected probe ──────────────

def test_probe_pool_marks_failing_member_down():
    pool = ["http://a", "http://b", "http://c"]
    st = {}
    def probe(u):
        return u != "http://b"     # b is unreachable
    pp.probe_pool(pool, st, probe, now=0, cooldown_s=300)
    assert pp.select_proxy(pool, st, now=1) == "http://a"
    assert pp.select_proxy(pool, st, now=1) == "http://c"   # b skipped


def test_probe_pool_treats_raising_probe_as_down():
    pool = ["http://a", "http://b"]
    st = {}
    def probe(u):
        if u == "http://a":
            raise OSError("boom")
        return True
    pp.probe_pool(pool, st, probe, now=0, cooldown_s=300)
    assert pp.healthy_urls(pool, st, now=1) == ["http://b"]


def test_probe_pool_recovers_member():
    pool = ["http://a"]
    st = {}
    pp.probe_pool(pool, st, lambda u: False, now=0, cooldown_s=300)
    assert pp.healthy_urls(pool, st, now=1) == []
    pp.probe_pool(pool, st, lambda u: True, now=1, cooldown_s=300)
    assert pp.healthy_urls(pool, st, now=2) == ["http://a"]


# ── wiring: _download_proxy_url uses the pool opt-in ────────────────

def _stub(config):
    from bulk_downloader.runner_transport import TransportMixin
    s = TransportMixin.__new__(TransportMixin)
    s.config = config
    s.site_id = "s-pool"
    return s


def test_no_pool_no_explicit_is_unchanged():
    # a non-vpn site with neither explicit proxy nor pool -> None (degrade open)
    assert _stub({})._download_proxy_url() is None


def test_pool_selected_when_no_explicit_proxy():
    s = _stub({"proxy_pool": ["http://a:3128", "http://b:3128"]})
    got = [s._download_proxy_url() for _ in range(3)]
    assert got == ["http://a:3128", "http://b:3128", "http://a:3128"]   # rotates + persists state


def test_explicit_proxy_wins_over_pool():
    s = _stub({"proxy": "http://explicit:3128", "proxy_pool": ["http://a:3128", "http://b:3128"]})
    assert s._download_proxy_url() == "http://explicit:3128"
    assert s._download_proxy_url() == "http://explicit:3128"   # pool never consulted
