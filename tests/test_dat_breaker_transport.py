"""dat-breaker: transport breakers that never recover, a proxy that fails open only sometimes, and the F16 leaks.

Rows F4 F5 F6 F16 of the deep-adapters-transport findings. Every defect test names its row in the assertion message
and has a control beside it that passes before and after the fix. Hermetic: a fake clock for the breaker, a fake
opener for the proxy pool, a Flask test client for the reset route; no socket is opened.
"""

from __future__ import annotations

import http.client
import itertools
import urllib.error

import pytest
from flask import Flask

from bulk_downloader import app_circuit
from bulk_downloader import circuit_breaker as cb
from bulk_downloader import download_egress
from bulk_downloader import egress_identity
from bulk_downloader import http_client as hc

BD_GATE_SCOPE = "module"

URL = "https://cdn.example.test/v/1.mp4"
HOST = "cdn.example.test"


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(cb, "time", c)
    cb.reset()
    yield c
    cb.reset()


def _trip(clock):
    for ok in (True,) * 4 + (False,) * 6:
        cb.observe(URL, success=ok)
        clock.now += 1
    assert cb.state(URL) == "open", "control: 6 failures of 10 open the circuit"


# ── F4: "open" must end without anyone calling allow() ──────────────


def test_f4_open_ends_after_the_cool_down_for_a_reader(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    assert cb.state(URL) == "half_open", f"DATB-F4-OPEN-FOREVER: state {cb.state(URL)!r} after the cool-down"
    assert cb.report()[HOST]["state"] == "half_open", f"DATB-F4-OPEN-FOREVER: report {cb.report()[HOST]!r}"


def test_f4_report_alone_shows_the_cool_down_has_passed(clock):
    # the alert, the health check and queue priority read report(), never state()
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    assert cb.report()[HOST]["state"] == "half_open", f"DATB-F4-OPEN-FOREVER: report {cb.report()[HOST]!r}"


def test_f4_successes_after_the_cool_down_close_it_with_observe_alone(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    for _ in range(cb._HALF_OPEN_HEAL_THRESHOLD):
        cb.observe(URL, success=True)
        clock.now += 1
    assert cb.state(URL) == "closed", f"DATB-F4-OPEN-FOREVER: {cb.state(URL)!r} after 3 good downloads"


def test_f4_a_week_idle_host_is_not_still_open(clock):
    _trip(clock)
    clock.now += 7 * 86400
    assert cb.state(URL) != "open", "DATB-F4-OPEN-FOREVER: open 7 days after the last observation"


def test_f4_control_inside_the_cool_down_it_stays_open(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION - 30
    for _ in range(5):
        cb.observe(URL, success=True)
    assert cb.state(URL) == "open" and cb.allow(URL) is False


def test_f4_control_a_failed_probe_reopens_for_a_full_cool_down(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    assert cb.allow(URL) is True
    cb.observe(URL, success=False)
    clock.now += cb._OPEN_DURATION - 1
    assert cb.state(URL) == "open" and cb.allow(URL) is False
    clock.now += 2
    assert cb.allow(URL) is True


# ── F5: half-open admits one probe AT A TIME, not one probe ever ────


def test_f5_each_reported_probe_admits_the_next_until_closed(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    for n in range(cb._HALF_OPEN_HEAL_THRESHOLD):
        assert cb.allow(URL) is True, f"DATB-F5-HALF-OPEN-DEADLOCK: probe {n + 1} refused, state {cb.state(URL)!r}"
        assert cb.allow(URL) is False, "DATB-F5-TWO-PROBES: a second caller got in while a probe was out"
        cb.observe(URL, success=True)
        clock.now += 1
    assert cb.state(URL) == "closed" and cb.allow(URL) is True, f"DATB-F5-HALF-OPEN-DEADLOCK: {cb.state(URL)!r}"


def test_f5_a_probe_that_never_reports_does_not_hold_the_circuit_shut(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    assert cb.allow(URL) is True
    clock.now += cb._OPEN_DURATION - 1
    assert cb.allow(URL) is False, "DATB-F5-TWO-PROBES: the probe is still within its cool-down"
    clock.now += 2
    assert cb.allow(URL) is True, "DATB-F5-LOST-PROBE: no probe admitted a cool-down after one went missing"


def test_f5_reset_leaves_nothing_behind_for_the_host(clock):
    _trip(clock)
    clock.now += cb._OPEN_DURATION + 1
    cb.allow(URL)
    cb.reset(HOST)
    left = sorted(k for k in cb._states if k.startswith(HOST))
    assert left == [] and cb.allow(URL) is True, f"DATB-F5-RESET-LEFTOVER: {left}"


# ── F6: the pooled proxy ────────────────────────────────────────────

_ports = itertools.count(20000)


class _Resp:
    status = 200


@pytest.fixture
def pool(monkeypatch):
    """A fresh pool (and so a fresh breaker: its registry key is the proxy host:port)."""
    url = f"http://127.0.0.1:{next(_ports)}"
    monkeypatch.setenv("BD_HTTP_PROXY", url)
    monkeypatch.setattr(hc, "_POOLS", {})
    return hc._pool(url)


def _through_proxy(pool, monkeypatch, outcome):
    """One proxy_open: returns 'proxy', 'direct' or 'raised <type>'."""

    def fake_open(request, *, timeout):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(pool, "open", fake_open)
    direct = _Resp()
    try:
        got = hc.proxy_open(object(), timeout=1, direct_open=lambda request, timeout: direct)
    except Exception as exc:  # noqa: BLE001 - the test reports what the caller would have seen
        return f"raised {type(exc).__name__}"
    return "direct" if got is direct else "proxy"


@pytest.mark.parametrize("garbage", [http.client.BadStatusLine("\x00\x01"), http.client.IncompleteRead(b"ab", 10),
                                     http.client.LineTooLong("header line")])  # fmt: skip
def test_f6_a_proxy_answering_garbage_is_counted_and_bypassed(pool, monkeypatch, garbage):
    got = _through_proxy(pool, monkeypatch, garbage)
    assert got == "direct", f"DATB-F6-GARBAGE-RAISES: {type(garbage).__name__} -> {got}"
    assert pool.failures == 1, f"DATB-F6-GARBAGE-UNCOUNTED: failures={pool.failures}"


def test_f6_two_failures_after_two_successes_do_not_open_the_proxy(pool, monkeypatch):
    for outcome in (_Resp(), _Resp(), OSError("refused"), OSError("refused")):
        _through_proxy(pool, monkeypatch, outcome)
    assert not pool.open_circuit, "DATB-F6-OPENS-EARLY: 2 proxy errors opened a breaker documented as 3 consecutive"


def test_f6_three_consecutive_failures_open_it_even_after_many_successes(pool, monkeypatch):
    for _ in range(17):
        assert _through_proxy(pool, monkeypatch, _Resp()) == "proxy"
    for _ in range(hc._FAILURE_LIMIT):
        _through_proxy(pool, monkeypatch, OSError("timed out"))
    assert pool.open_circuit, "DATB-F6-OPENS-LATE: 3 consecutive proxy errors after 17 successes left it closed"
    assert _through_proxy(pool, monkeypatch, _Resp()) == "direct"


def test_f6_control_the_documented_fallbacks_are_unchanged(pool, monkeypatch):
    assert _through_proxy(pool, monkeypatch, _Resp()) == "proxy"
    assert _through_proxy(pool, monkeypatch, OSError("refused")) == "direct"
    http_500 = urllib.error.HTTPError("http://x/", 502, "bad gateway", None, None)
    assert _through_proxy(pool, monkeypatch, http_500) == "direct"
    http_404 = urllib.error.HTTPError("http://x/", 404, "not found", None, None)
    assert _through_proxy(pool, monkeypatch, http_404) == "raised HTTPError"
    assert _through_proxy(pool, monkeypatch, ValueError("caller bug")) == "raised ValueError"


def test_f6_control_three_failures_from_fresh_open_it(pool, monkeypatch):
    for _ in range(hc._FAILURE_LIMIT):
        _through_proxy(pool, monkeypatch, OSError("refused"))
    assert pool.open_circuit


# ── F16: spawn environment, exit-IP stamp, reset route, breaker key ─

_LEAKY = {"Http_Proxy": "http://leak-a:1", "All_Proxy": "socks5://leak-b:2", "socks_proxy": "socks5://leak-c:3",
          "SOCKS5_PROXY": "socks5://leak-d:4", "hTTPs_pRoXy": "http://leak-e:5"}  # fmt: skip


@pytest.mark.parametrize("proxy_url", [None, "http://127.0.0.1:3128"])
def test_f16_spawn_env_carries_no_other_proxy_variable(monkeypatch, proxy_url):
    for name, value in {**_LEAKY, "HTTP_PROXY": "http://listed:6", "no_proxy": "*"}.items():
        monkeypatch.setenv(name, value)
    env = download_egress.PreparedHttpProxy(proxy_url).subprocess_env()
    proxies = {k: v for k, v in env.items() if k.lower().endswith("_proxy")}
    want = {"http_proxy": proxy_url, "https_proxy": proxy_url} if proxy_url else {}
    assert proxies == want, f"DATB-F16-PROXY-ENV-LEAK: spawn env still carries {sorted(set(proxies) - set(want))}"


def test_f16_control_spawn_env_keeps_everything_else(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://listed:6")
    monkeypatch.setenv("PROXY_HOME", "/opt/p")
    monkeypatch.setenv("MY_PROXY_URL", "kept")
    env = download_egress.PreparedHttpProxy(None).subprocess_env()
    assert "HTTP_PROXY" not in env and env["PROXY_HOME"] == "/opt/p" and env["MY_PROXY_URL"] == "kept"
    assert env["PATH"]


def test_f16_ipv4_mapped_exit_is_the_same_stamp_as_the_ipv4_exit():
    got = egress_identity.normalize_egress_ip("::ffff:1.2.3.4")
    assert got == "1.2.3.4", f"DATB-F16-MAPPED-IP: '::ffff:1.2.3.4' stamped as {got!r}"
    assert egress_identity.normalize_egress_ip("::FFFF:102:304") == egress_identity.normalize_egress_ip(" 1.2.3.4 ")


def test_f16_control_other_addresses_are_stamped_as_before():
    n = egress_identity.normalize_egress_ip
    assert n("2001:DB8::1") == "2001:db8::1" and n("1.2.3.4") == "1.2.3.4" and n("::1") == "::1"
    assert n("not-an-ip") == n(None) == n("") == egress_identity.UNKNOWN_EGRESS


@pytest.fixture
def client(monkeypatch, clock):
    monkeypatch.setattr(app_circuit, "_check_csrf", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(app_circuit.circuit_bp)
    return app.test_client()


@pytest.mark.parametrize("body", ['"x"', '{"host": 5}', '{"host": ["a"]}', "[1]", "null", "{not json"])
def test_f16_reset_refuses_a_body_it_cannot_read_and_resets_nothing(client, clock, body):
    _trip(clock)
    r = client.post("/api/circuit/reset", data=body, content_type="application/json")
    assert r.status_code == 400, f"DATB-F16-RESET-500: {body!r} -> HTTP {r.status_code} {r.get_data(as_text=True)[:80]!r}"
    assert cb.state(URL) == "open", f"DATB-F16-RESET-WIPED: {body!r} reset the breaker"


@pytest.mark.parametrize("named", ["CDN.EXAMPLE.TEST", "https://cdn.example.test:8443/x?y=1", "cdn.example.test:443",
                                   " cdn.example.test "])  # fmt: skip
def test_f16_reset_finds_the_host_however_it_is_spelled(client, clock, named):
    _trip(clock)
    r = client.post("/api/circuit/reset", json={"host": named})
    assert r.status_code == 200 and cb.state(URL) == "closed", (
        f"DATB-F16-RESET-NOTHING: {named!r} -> HTTP {r.status_code} {r.get_json()!r}, state {cb.state(URL)!r}")
    assert r.get_json() == {"ok": True, "host": HOST, "reset": True}


def test_f16_reset_says_so_when_there_was_nothing_under_that_name(client, clock):
    _trip(clock)
    r = client.post("/api/circuit/reset", json={"host": "other.example.test"})
    assert r.status_code == 200 and r.get_json().get("reset") is False, f"DATB-F16-RESET-NOTHING: {r.get_json()!r}"
    assert cb.state(URL) == "open"


@pytest.mark.parametrize("kwargs", [{"json": {}}, {"json": {"host": ""}}, {"json": {"host": None}}])
def test_f16_control_no_host_resets_everything(client, clock, kwargs):
    _trip(clock)
    r = client.post("/api/circuit/reset", **kwargs)
    assert r.status_code == 200 and r.get_json()["ok"] is True and cb.report() == {}


def test_f16_control_one_breaker_per_hostname_whatever_the_port(clock):
    # documented design (see _host_of): ports and schemes of one host share a breaker
    for ok in (True,) * 4 + (False,) * 6:
        cb.observe("http://cdn.example.test:8080/a", success=ok)
    assert cb.state("https://cdn.example.test:443/b") == "open" and list(cb.report()) == [HOST]
