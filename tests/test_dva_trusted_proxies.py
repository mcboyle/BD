"""dva-trusted-proxies (dva-auth r3 A1 split): the /api/auth/login throttle keyed every
client of a reverse proxy on the proxy's address.

The shipped conf/Caddyfile fronts the app with `reverse_proxy localhost:5555`, so every
login reaches app_auth from 127.0.0.1 and the per-(username, address) label was one label
for all of them: one client's failed guesses refused every other client's login to that
account. Now, when the peer is in request_peer.trusted_proxies() (pair-admission-proxy's
setting), the login is keyed on the forwarded client that request_peer's parser reads;
a forwarding header from an untrusted peer, and a header naming no usable client, still
key on the peer, so a client cannot pick its own label.
"""
from __future__ import annotations

import types

import pytest
from flask import Flask

from bulk_downloader import app_auth as M
from bulk_downloader import auth_throttle as AT
from bulk_downloader import global_config

BD_GATE_SCOPE = "module"
DIAG = "DVA_TRUSTED_PROXIES"

PW = "pw123456"
A = "198.51.100.1"   # the client guessing at admin1's password
B = "198.51.100.2"   # the real admin1, behind the same proxy
PROXY = "127.0.0.1"  # the Caddyfile's reverse_proxy peer (the test client default)
OK = {"username": "admin1", "password": PW}
BAD = {"username": "admin1", "password": "wrong"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the auth store is cwd-relative
    AT.reset()
    a = Flask(__name__)
    a.testing = True
    M.register_routes(a)
    r = a.test_client().post("/api/auth/users", json={**OK, "role": "admin"})
    assert r.status_code == 200, r.get_json()
    yield a
    AT.reset()


def _trust(monkeypatch, proxies):
    real = global_config.get
    monkeypatch.setattr(global_config, "get",
                        lambda key, default=None: list(proxies) if key == "trusted_proxies" else real(key, default))


def _throttle(monkeypatch):
    monkeypatch.setattr(AT, "_store_get", lambda key: None)  # env decides, not a host config
    monkeypatch.setenv("BD_AUTH_THROTTLE", "1")
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", "2")
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", "60")
    now = [1000.0]
    monkeypatch.setattr(AT, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    return now


def _login(app, body, headers, peer=PROXY):
    return app.test_client().post("/api/auth/login", json=body, headers=headers,
                                  environ_base={"REMOTE_ADDR": peer}).status_code


def _key(app, headers, peer=PROXY):
    with app.test_request_context("/api/auth/login", method="POST", headers=headers,
                                  environ_base={"REMOTE_ADDR": peer}):
        return M._login_labels("admin1")[0][-1]


def _flood(app, now, guesser):
    """A's pair escalates past BASE (4th failure: 120 s) while the soft per-user label,
    capped at BASE, is free again: the state in which only the pair key decides."""
    codes = [_login(app, BAD, guesser()) for _ in range(3)]
    now[0] += 61
    codes.append(_login(app, BAD, guesser()))
    now[0] += 61
    codes.append(_login(app, BAD, guesser()))
    assert codes == [401, 401, 401, 401, 429], f"{DIAG}: COULD NOT LOOK, the flood did not escalate: {codes}"


def test_two_forwarded_clients_of_a_trusted_proxy_get_separate_keys(app, monkeypatch):
    _trust(monkeypatch, [PROXY, "::1"])
    ka, kb = _key(app, {"X-Forwarded-For": A}), _key(app, {"X-Forwarded-For": B})
    assert (ka, kb) == (A, B), f"{DIAG}: trusted proxy keyed {A} as {ka!r} and {B} as {kb!r}"


def test_a_guessing_client_behind_a_trusted_proxy_does_not_refuse_the_real_user(app, monkeypatch):
    _trust(monkeypatch, [PROXY])
    now = _throttle(monkeypatch)
    _flood(app, now, lambda: {"X-Forwarded-For": A})
    assert _login(app, OK, {"X-Forwarded-For": B}) == 200, f"{DIAG}: {B}'s login refused by {A}'s back-off"
    assert _login(app, OK, {"X-Forwarded-For": A}) == 429, f"{DIAG}: control, {A} itself stays refused"


def test_control_without_trusted_proxies_forwarded_clients_share_the_proxy_key(app):
    ka, kb = _key(app, {"X-Forwarded-For": A}), _key(app, {"X-Forwarded-For": B})
    assert (ka, kb) == (PROXY, PROXY), f"{DIAG}: with no trusted_proxies a forwarded client got its own key: {ka!r}, {kb!r}"


@pytest.mark.parametrize("peer,headers", [
    ("10.9.9.9", {"X-Forwarded-For": A}),             # untrusted peer: its header is ignored
    ("10.9.9.9", {"Forwarded": f"for={A}"}),
    ("10.9.9.9", {"X-Real-IP": A}),
    (PROXY, {"X-Forwarded-For": f"{A}, {PROXY}, x"}),   # unparseable right-most hop
    (PROXY, {"X-Forwarded-For": PROXY}),               # every hop trusted: no client named
    (PROXY, {"X-Forwarded-For": B, "X-Real-IP": A}),   # headers disagree
], ids=["untrusted-xff", "untrusted-forwarded", "untrusted-real-ip", "bad-hop", "no-client", "disagree"])
def test_an_unusable_forwarding_header_keys_on_the_peer(app, monkeypatch, peer, headers):
    _trust(monkeypatch, [PROXY])
    got = _key(app, headers, peer)
    assert got == peer, f"{DIAG}: peer {peer} with {headers} keyed on {got!r}, not the peer"


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": f"203.0.113.9, {A}"},                       # client-written left hop
    {"X-Forwarded-For": f"{A}, ::1"},                                # trusted hop skipped
    {"X-Forwarded-For": A, "Forwarded": f'for="{A}:4711"'},         # headers agree
    {"Forwarded": f"for={A};proto=http"},
], ids=["spoofed-left-hop", "trusted-hop", "agree", "forwarded"])
def test_a_trusted_proxy_keys_on_the_right_most_untrusted_hop(app, monkeypatch, headers):
    _trust(monkeypatch, [PROXY, "::1"])
    got = _key(app, headers)
    assert got == A, f"{DIAG}: {headers} keyed on {got!r}, not {A}"


@pytest.mark.parametrize("vary", [
    lambda i: ("10.9.9.9", {"X-Forwarded-For": f"203.0.113.{i}"}),
    lambda i: (PROXY, {"X-Forwarded-For": A, "X-Real-IP": f"203.0.113.{i}"}),
    lambda i: (PROXY, {"X-Forwarded-For": f"203.0.113.{i}, {A}"}),
], ids=["untrusted-peer", "real-ip-rotation", "left-hop-rotation"])
def test_a_client_rotating_its_headers_stays_refused(app, monkeypatch, vary):
    """Each request names a fresh address; none of them may become the key."""
    _trust(monkeypatch, [PROXY])
    now = _throttle(monkeypatch)
    codes = []
    for i, (step, body) in enumerate([(0, BAD), (0, BAD), (0, BAD), (61, BAD), (61, BAD), (0, OK)], 1):
        now[0] += step
        peer, headers = vary(i)
        codes.append(_login(app, body, headers, peer))
    assert codes == [401, 401, 401, 401, 429, 429], f"{DIAG}: rotating headers escaped the back-off: {codes}"
