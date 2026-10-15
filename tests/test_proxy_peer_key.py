"""proxy-peer-key r1 (O1789 raw-editors-dev-auth, PM ANSWER-pm-B-sse-A1-1446Z A1): a proxy is not one client.

app._rate_check keyed its buckets on request.remote_addr. The shipped conf/Caddyfile fronts the app with
`reverse_proxy localhost:5555`, so behind Caddy every client arrives from 127.0.0.1: one shared bucket, and one
client's burst rate-limits all the others. Trusting X-Forwarded-For from anyone would let any client pick its own
bucket instead.

Fix: _rate_check keys on request_peer.client_addr(request, trusted_proxies()) -- the forwarded client (right-most hop
not in trusted_proxies) only when the peer is a configured trusted proxy, else remote_addr. Same hop walk as
client_is_loopback (pair-admission-proxy); no second parser, no new settings key.

r2 (stream part): the /api/stream per-client cap (app_stream._stream_slot_acquire -> sse_broker.acquire_stream) keyed
its slots on request.remote_addr too; it now keys on the same client_addr. Stream tests below use the fresh_app client.
"""

import pytest
from flask import request

from bulk_downloader import app as bd_app
from bulk_downloader import global_config, request_peer

BD_GATE_SCOPE = "module"
DIAG = "PROXY_PEER_KEY"
ACTION = "proxy_peer_key_probe"
A, B = "203.0.113.7", "198.51.100.9"
LAN_PEER = "192.0.2.50"


@pytest.fixture
def buckets():
    bd_app._rate_buckets.clear()
    yield bd_app._rate_buckets
    bd_app._rate_buckets.clear()


def _trust(monkeypatch, proxies):
    real = global_config.get
    monkeypatch.setattr(global_config, "get",
                        lambda key, default=None: list(proxies) if key == "trusted_proxies" else real(key, default))


def _hit(peer, headers=None, n=1):
    """n _rate_check calls from `peer` carrying `headers`; their results."""
    out = []
    for _ in range(n):
        with bd_app.app.test_request_context("/", environ_base={"REMOTE_ADDR": peer}, headers=headers or {}):
            out.append(bd_app._rate_check(ACTION))
    return out


def _keys(buckets):
    return sorted(ip for ip, action in buckets if action == ACTION)


def _burst(peer, headers):
    """Exhaust the bucket `peer`+`headers` lands in; the burst must be admitted and the next call refused."""
    first = _hit(peer, headers, bd_app.RATE_LIMIT_MAX)
    assert all(first), f"COULD NOT LOOK: a fresh bucket refused within RATE_LIMIT_MAX: {first}"
    assert _hit(peer, headers) == [False], "COULD NOT LOOK: the bucket did not fill at RATE_LIMIT_MAX"


@pytest.mark.parametrize("trusted", [["127.0.0.1"], ["127.0.0.0/8"], ["127.0.0.1", "10.0.0.0/8"]],
                         ids=["ip", "cidr", "list"])
def test_two_clients_behind_a_trusted_caddy_get_two_buckets(buckets, monkeypatch, trusted):
    _trust(monkeypatch, trusted)
    _burst("127.0.0.1", {"X-Forwarded-For": A})
    other = _hit("127.0.0.1", {"X-Forwarded-For": B})
    assert other == [True] and _keys(buckets) == sorted([A, B]), (
        f"{DIAG}_SHARED_BUCKET: behind trusted proxy {trusted}, client {B} was refused after client {A}'s burst "
        f"(result {other}); bucket keys {_keys(buckets)}")


def test_a_client_written_hop_before_the_proxys_does_not_pick_the_bucket(buckets, monkeypatch):
    """Caddy appends the real client to a client-sent X-Forwarded-For: the right-most untrusted hop decides."""
    _trust(monkeypatch, ["127.0.0.1"])
    _burst("127.0.0.1", {"X-Forwarded-For": A})
    assert _keys(buckets) == [A], (
        f"{DIAG}_SHARED_BUCKET: client {A} behind trusted proxy 127.0.0.1 was keyed {_keys(buckets)}")
    spoof = _hit("127.0.0.1", {"X-Forwarded-For": f"{B}, {A}"})
    assert spoof == [False] and _keys(buckets) == [A], (
        f"{DIAG}_SPOOFED_BUCKET: client {A} escaped its full bucket by prepending {B} (result {spoof}); "
        f"bucket keys {_keys(buckets)}")


@pytest.mark.parametrize("peer,trusted", [
    ("127.0.0.1", []),
    (LAN_PEER, []),
    (LAN_PEER, ["127.0.0.1"]),
    ("127.0.0.2", ["127.0.0.1"]),
], ids=["loopback-no-trust", "lan-no-trust", "lan-not-the-proxy", "other-loopback-not-the-proxy"])
def test_an_untrusted_peer_sending_xff_is_keyed_on_remote_addr(buckets, monkeypatch, peer, trusted):
    _trust(monkeypatch, trusted)
    _burst(peer, {"X-Forwarded-For": A})
    spoof = _hit(peer, {"X-Forwarded-For": B, "X-Real-IP": B, "Forwarded": f"for={B}"})
    assert spoof == [False] and _keys(buckets) == [peer], (
        f"{DIAG}_SPOOFED_BUCKET: untrusted peer {peer} (trusted_proxies={trusted}) picked a new bucket with a "
        f"forwarding header (result {spoof}); bucket keys {_keys(buckets)}")


def test_control_a_direct_client_is_keyed_on_remote_addr(buckets, monkeypatch):
    _trust(monkeypatch, ["127.0.0.1"])
    assert _hit(LAN_PEER) == [True] and _keys(buckets) == [LAN_PEER], (
        f"{DIAG}: a direct client's bucket moved off remote_addr: {_keys(buckets)}")


@pytest.mark.parametrize("peer,trusted,headers,expected", [
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": A}, A),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": f"{B}, {A}"}, A),
    ("127.0.0.1", ["127.0.0.1", "10.0.0.0/8"], {"X-Forwarded-For": f"{A}, 10.1.2.3"}, A),
    ("127.0.0.1", ["127.0.0.1"], {"Forwarded": 'for="[2001:db8::1]:4711";proto=http'}, "2001:db8::1"),
    ("127.0.0.1", ["127.0.0.1"], {"X-Real-IP": f"::ffff:{A}"}, A),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": A, "X-Real-IP": A}, A),
    ("10.0.0.5", ["10.0.0.5"], {"X-Forwarded-For": A}, A),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": A, "X-Real-IP": B}, "127.0.0.1"),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": "not-an-ip"}, "127.0.0.1"),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": f"{A}, garbage"}, "127.0.0.1"),
    ("127.0.0.1", ["127.0.0.1"], {"X-Forwarded-For": "127.0.0.1"}, "127.0.0.1"),
    ("127.0.0.1", ["127.0.0.1"], {}, "127.0.0.1"),
    ("127.0.0.1", [], {"X-Forwarded-For": A}, "127.0.0.1"),
    (LAN_PEER, ["127.0.0.1"], {"X-Forwarded-For": A}, LAN_PEER),
], ids=["xff", "xff-rightmost", "xff-trusted-chain", "forwarded-v6-port", "x-real-ip-mapped", "headers-agree",
        "remote-proxy", "headers-disagree", "unparseable", "unparseable-rightmost", "no-client-named", "no-header",
        "no-trust", "untrusted-peer"])
def test_client_addr(peer, trusted, headers, expected):
    with bd_app.app.test_request_context("/", environ_base={"REMOTE_ADDR": peer}, headers=headers):
        got = request_peer.client_addr(request, trusted)
    assert got == expected, (
        f"{DIAG}: client_addr(peer={peer}, trusted={trusted}, {headers}) -> {got!r}, want {expected!r}")


# -- r2: the /api/stream per-client cap (app_stream._stream_slot_acquire) ------------------------------------------

def _open_stream(client, peer, headers=None):
    return client.get("/api/stream", buffered=False, environ_base={"REMOTE_ADDR": peer}, headers=headers or {})


def _stream_keys(*addrs):
    from bulk_downloader import sse_broker
    streams = getattr(sse_broker.get_broker(), "_streams", {})
    return sorted(a for a in addrs if a in streams)


def _fill_streams(client, peer, headers, opened):
    """Open the cap for `peer`+`headers` (each 200) and prove the cap bites: one more is 429."""
    from bulk_downloader import sse_broker
    cap = sse_broker.STREAMS_PER_CLIENT_MAX
    for _ in range(cap):
        r = _open_stream(client, peer, headers)
        opened.append(r)
        assert r.status_code == 200, f"COULD NOT LOOK: /api/stream under the cap answered {r.status_code}"
    extra = _open_stream(client, peer, headers)
    extra.close()
    assert extra.status_code == 429, f"COULD NOT LOOK: the stream cap did not bite at {cap}: {extra.status_code}"


def test_two_clients_behind_a_trusted_caddy_get_two_stream_buckets(fresh_app, monkeypatch):
    _trust(monkeypatch, ["127.0.0.1"])
    opened = []
    try:
        _fill_streams(fresh_app, "127.0.0.1", {"X-Forwarded-For": A}, opened)
        other = _open_stream(fresh_app, "127.0.0.1", {"X-Forwarded-For": B})
        opened.append(other)
        assert other.status_code == 200 and _stream_keys(A, B, "127.0.0.1") == sorted([A, B]), (
            f"{DIAG}_SHARED_STREAM_BUCKET: behind trusted proxy 127.0.0.1, client {B} got {other.status_code} on "
            f"/api/stream after client {A} filled the cap; stream keys {_stream_keys(A, B, '127.0.0.1')}")
    finally:
        while opened:
            opened.pop().close()
    assert _stream_keys(A, B, "127.0.0.1") == [], f"{DIAG}: closed streams left slots {_stream_keys(A, B, '127.0.0.1')}"


@pytest.mark.parametrize("peer,trusted", [(LAN_PEER, []), (LAN_PEER, ["127.0.0.1"])],
                         ids=["lan-no-trust", "lan-not-the-proxy"])
def test_an_untrusted_peer_sending_xff_is_keyed_on_remote_addr_for_streams(fresh_app, monkeypatch, peer, trusted):
    _trust(monkeypatch, trusted)
    opened = []
    try:
        _fill_streams(fresh_app, peer, {"X-Forwarded-For": A}, opened)
        spoof = _open_stream(fresh_app, peer, {"X-Forwarded-For": B, "X-Real-IP": B, "Forwarded": f"for={B}"})
        spoof.close()
        assert spoof.status_code == 429 and _stream_keys(A, B, peer) == [peer], (
            f"{DIAG}_SPOOFED_STREAM_BUCKET: untrusted peer {peer} (trusted_proxies={trusted}) opened a stream past "
            f"its cap with a forwarding header ({spoof.status_code}); stream keys {_stream_keys(A, B, peer)}")
    finally:
        while opened:
            opened.pop().close()
