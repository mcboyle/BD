"""pair-admission-proxy (O1789 raw-editors-dev-auth, PM A-pm-B-pass-1112Z item 2): a local proxy is not the box.

pair-admission let GET /api/pair mint a pairing code (a login waiting to happen) for a loopback peer, judged by
request.remote_addr against the literals 127.0.0.1 / ::1 / localhost. The shipped conf/Caddyfile fronts the app with
`reverse_proxy localhost:5555`, so every LAN/WAN request through it arrives from 127.0.0.1 with the real client in
X-Forwarded-For: behind Caddy anyone could mint. And ::ffff:127.0.0.1 or 127.0.0.2, which are the box, were refused.

A1: a loopback peer carrying Forwarded / X-Forwarded-For / X-Real-IP is remote unless the peer is in trusted_proxies
(empty by default); a trusted proxy's right-most untrusted hop must itself be loopback.
A2: 127.0.0.0/8 and IPv4-mapped loopback are loopback.

Controls: plain loopback and ::1 still mint; a proxied caller with a login session or a valid token still mints; a
trusted proxy forwarding a loopback client mints.

PM ANSWER-pm-B-dev-gate-1141Z folded in the /api/dev gate (app._dev_request_authorized, same literal set, same hole)
and every other request.remote_addr loopback comparison: all go through request_peer.client_is_loopback, and a grep
guard refuses a raw remote_addr loopback comparison anywhere else in bulk_downloader/.
"""

import ast
from pathlib import Path

import pytest

from bulk_downloader import app as bd_app
from bulk_downloader import dev_tools, global_config

BD_GATE_SCOPE = "module"
DIAG = "PAIR_ADMISSION_PROXY_LOOPBACK"
TOKEN = "fixture-pair-admission-proxy-token"  # a test fixture value, not a credential
CLIENT = "203.0.113.7"
# What Caddy's reverse_proxy adds to a request from CLIENT; the peer is 127.0.0.1 (the test client default).
CADDY = {"X-Forwarded-For": CLIENT, "X-Forwarded-Proto": "http", "X-Forwarded-Host": "bd.example:8080"}


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setattr(dev_tools, "is_dev_mode", lambda: True)
    assert bd_app._accepted_tokens() == [], "COULD NOT LOOK: a token is configured in this environment"


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setattr(dev_tools, "is_dev_mode", lambda: True)
    assert bd_app._accepted_tokens() == [TOKEN], "COULD NOT LOOK: the token is not what the app reads"


def _trust(monkeypatch, proxies):
    real = global_config.get
    monkeypatch.setattr(global_config, "get",
                        lambda key, default=None: list(proxies) if key == "trusted_proxies" else real(key, default))


def _mint(client=None, **kw):
    r = (client or bd_app.app.test_client()).get("/api/pair", **kw)
    return r, ((r.get_json(silent=True) or {}).get("token") or "")


def _refused(r, code, how):
    assert r.status_code == 403 and not code, (
        f"{DIAG}: {how} GET /api/pair -> {r.status_code}, pairing code issued={bool(code)}")


def _allowed(r, code, how):
    assert r.status_code == 200 and code, f"{DIAG}: {how} GET /api/pair -> {r.status_code}, the box was refused"


@pytest.mark.parametrize("headers", [
    CADDY,
    {"Forwarded": f"for={CLIENT};proto=http"},
    {"X-Real-IP": CLIENT},
    {"X-Forwarded-For": "127.0.0.1"},
    {"Forwarded": 'for="[::1]"'},
], ids=["caddy-xff", "forwarded", "x-real-ip", "xff-says-loopback", "forwarded-says-loopback"])
def test_a_proxied_request_from_a_loopback_peer_does_not_mint(no_token, headers):
    """No trusted_proxies (the default): any forwarding header makes a loopback peer remote, whatever it names."""
    r, code = _mint(headers=headers)
    _refused(r, code, f"loopback peer with {sorted(headers)}")


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "127.0.0.2"])
def test_control_the_box_itself_mints(no_token, peer):
    r, code = _mint(environ_base={"REMOTE_ADDR": peer})
    _allowed(r, code, f"peer {peer}, no forwarding header,")


def test_control_a_proxied_caller_with_a_login_session_mints(no_token):
    """The operator mints on the box; a phone that redeemed it through Caddy is signed in and may mint again."""
    _, code = _mint()
    phone = bd_app.app.test_client()
    assert phone.post("/api/pair/redeem", json={"token": code}, headers=CADDY).status_code == 200
    r, again = _mint(phone, headers=CADDY)
    _allowed(r, again, "proxied caller holding a pair_redeem session")


def test_control_a_proxied_token_holder_mints(with_token):
    r, code = _mint(headers={**CADDY, "Authorization": f"Bearer {TOKEN}"})
    _allowed(r, code, "proxied Bearer holder")
    r, code = _mint(headers=CADDY)
    assert r.status_code == 401 and not code


@pytest.mark.parametrize("xff", [CLIENT, f"127.0.0.1, {CLIENT}", "127.0.0.2, garbage", "127.0.0.1"],
                         ids=["client-remote", "spoofed-left-hop", "unparseable-hop", "all-hops-trusted"])
def test_a_trusted_proxy_forwarding_a_remote_or_unnamed_client_does_not_mint(no_token, monkeypatch, xff):
    """Peer 127.0.0.1 is trusted: the right-most untrusted hop decides; a client-prepended 127.0.0.1 is ignored,
    an unparseable hop or a header naming no untrusted hop fails closed."""
    _trust(monkeypatch, ["127.0.0.1"])
    r, code = _mint(headers={"X-Forwarded-For": xff})
    _refused(r, code, f"trusted proxy forwarding X-Forwarded-For {xff!r}")


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": "127.0.0.2"},
    {"X-Forwarded-For": f"{CLIENT}, 127.0.0.2"},
    {"Forwarded": 'for="[::1]:4711"'},
], ids=["xff-loopback", "xff-loopback-right-most", "forwarded-v6-loopback"])
def test_control_a_trusted_proxy_forwarding_a_loopback_client_mints(no_token, monkeypatch, headers):
    _trust(monkeypatch, ["127.0.0.1/32"])
    r, code = _mint(headers=headers)
    _allowed(r, code, f"trusted proxy forwarding {headers}")


def test_a_trusted_proxy_does_not_vouch_for_other_loopback_peers(no_token, monkeypatch):
    _trust(monkeypatch, ["127.0.0.5"])
    r, code = _mint(headers={"X-Forwarded-For": "127.0.0.2"})
    _refused(r, code, "untrusted loopback peer 127.0.0.1 (trusted is 127.0.0.5) with X-Forwarded-For 127.0.0.2")


@pytest.mark.parametrize("value,expected", [
    ("127.0.0.1", True), ("127.255.255.254", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("::ffff:7f00:1", True), ("[::1]:5555", True), ("127.0.0.1:5555", True), ("localhost", True),
    ("0.0.0.0", False), ("::", False), ("128.0.0.1", False), ("::ffff:10.0.0.1", False), ("10.0.0.99", False),
    ("", False), (None, False), ("garbage", False), ("127.0.0.1.evil", False),
])
def test_is_loopback_addr(value, expected):
    from bulk_downloader import request_peer

    assert request_peer.is_loopback_addr(value) is expected, f"{DIAG}: is_loopback_addr({value!r})"


DEV = "/api/dev/runs"


@pytest.mark.parametrize("headers", [CADDY, {"Forwarded": f"for={CLIENT}"}, {"X-Real-IP": "127.0.0.1"}],
                         ids=["caddy-xff", "forwarded", "x-real-ip-says-loopback"])
def test_a_proxied_request_from_a_loopback_peer_does_not_reach_dev(no_token, headers):
    r = bd_app.app.test_client().get(DEV, headers=headers)
    assert r.status_code == 403, f"{DIAG}: loopback peer with {sorted(headers)} GET {DEV} -> {r.status_code}"


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "127.0.0.2"])
def test_control_the_box_itself_reaches_dev(no_token, peer):
    r = bd_app.app.test_client().get(DEV, environ_base={"REMOTE_ADDR": peer})
    assert r.status_code == 200, f"{DIAG}: peer {peer}, no forwarding header, GET {DEV} -> {r.status_code}"


def test_control_a_trusted_proxy_decides_dev_by_the_forwarded_client(no_token, monkeypatch):
    _trust(monkeypatch, ["127.0.0.1"])
    client = bd_app.app.test_client()
    assert client.get(DEV, headers={"X-Forwarded-For": "127.0.0.2"}).status_code == 200
    r = client.get(DEV, headers={"X-Forwarded-For": CLIENT})
    assert r.status_code == 403, f"{DIAG}: trusted proxy forwarding {CLIENT} GET {DEV} -> {r.status_code}"


def test_control_a_proxied_caller_with_a_paired_session_is_seen_as_paired(no_token):
    """dev-paired-session-scope (main 4ccdf4ca) keeps a pair_redeem session off
    /api/dev/*; the proof the phone is signed in is the paired-specific refusal,
    not the generic one a stranger gets."""
    _, code = _mint()
    phone = bd_app.app.test_client()
    before = phone.get(DEV, headers=CADDY)
    assert before.status_code == 403 and bd_app._DEV_PAIRED_REFUSAL not in before.get_json()["error"]
    assert phone.post("/api/pair/redeem", json={"token": code}, headers=CADDY).status_code == 200
    after = phone.get(DEV, headers=CADDY)
    assert (after.status_code, after.get_json()["error"]) == (403, bd_app._DEV_PAIRED_REFUSAL), (
        f"{DIAG}: paired phone behind the proxy GET {DEV} -> {after.status_code} {after.get_json()!r}")


# -- grep guard: no raw remote_addr loopback comparison outside request_peer ------------------------------------------

PKG = Path(__file__).resolve().parents[1] / "bulk_downloader"
HELPER = PKG / "request_peer.py"
LOOPBACK_LITERALS = {"127.0.0.1", "::1", "localhost"}


def _raw_loopback_admissions(source, name):
    """Functions that read request.remote_addr AND test loopback themselves (a loopback literal or .is_loopback)."""
    hits = []
    for fn in ast.walk(ast.parse(source, name)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(ast.walk(fn))
        reads_peer = any(isinstance(n, ast.Attribute) and n.attr == "remote_addr" for n in nodes)
        tests_loopback = any(
            (isinstance(n, ast.Constant) and n.value in LOOPBACK_LITERALS)
            or (isinstance(n, ast.Attribute) and n.attr == "is_loopback") for n in nodes)
        if reads_peer and tests_loopback:
            hits.append(f"{name}:{fn.lineno} {fn.name}")
    return hits


def test_guard_detector_flags_the_raw_shapes_and_passes_the_helper():
    """Positive control: the pre-fix dev gate and mint shapes are flagged; the helper call is not."""
    pre_dev = 'def g():\n    ra = (request.remote_addr or "")\n    if ra in ("127.0.0.1", "::1", "localhost"):\n        return 1\n'
    pre_ip = "def g():\n    return ipaddress.ip_address(request.remote_addr).is_loopback\n"
    fixed = "def g():\n    return _rp.client_is_loopback(request, _rp.trusted_proxies())\n"
    label = "def g():\n    return request.headers.get('X-Actor') or request.remote_addr or 'unknown'\n"
    assert _raw_loopback_admissions(pre_dev, "x.py") == ["x.py:1 g"]
    assert _raw_loopback_admissions(pre_ip, "x.py") == ["x.py:1 g"]
    assert _raw_loopback_admissions(fixed, "x.py") == []
    assert _raw_loopback_admissions(label, "x.py") == []


def test_no_raw_remote_addr_loopback_comparison_outside_request_peer():
    files = sorted(p for p in PKG.rglob("*.py") if p != HELPER)
    reads = [p for p in files if "remote_addr" in p.read_text(encoding="utf-8", errors="replace")]
    assert len(files) > 100 and len(reads) >= 5, (
        f"COULD NOT LOOK: scanned {len(files)} files, {len(reads)} mention remote_addr under {PKG}")
    hits = [h for p in reads for h in _raw_loopback_admissions(p.read_text(encoding="utf-8"), p.relative_to(PKG.parent).as_posix())]
    assert hits == [], (
        f"{DIAG}: raw request.remote_addr loopback comparison outside request_peer (a proxy on the box is not the "
        f"box; use request_peer.client_is_loopback): {hits}")
