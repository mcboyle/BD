"""The pinned download transport must not wait on an unreachable IPv6 peer."""
from __future__ import annotations

import socket
import threading
import time

from bulk_downloader import happy_eyeballs, ssrf_transport

BD_GATE_SCOPE = "module"


def test_pinned_transport_prefers_ipv4_after_ipv6_candidate(monkeypatch):
    """A dual-stack CDN with a black-holed v6 route still gets a v4 socket."""
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ]
    monkeypatch.setattr(ssrf_transport.socket, "getaddrinfo", lambda *a, **k: answers)
    request = ssrf_transport.httpx.Request("GET", "https://cdn.example/video")

    selected = ssrf_transport.PinnedTransport().pin(request)

    assert selected == "8.8.8.8"
    assert request.url.host == "8.8.8.8"


def test_pinned_transport_rejects_link_local_in_either_family(monkeypatch):
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ]
    monkeypatch.setattr(ssrf_transport.socket, "getaddrinfo", lambda *a, **k: answers)
    request = ssrf_transport.httpx.Request("GET", "https://cdn.example/video")

    try:
        ssrf_transport.PinnedTransport().pin(request)
    except ssrf_transport.GuardedTransportRefused as exc:
        assert exc.reason == "link-local"
    else:
        raise AssertionError("link-local race candidate was admitted")


# ── race_connect (row867-r2 lens ground 4: the race itself was untested) ──────


class _Stream:
    def __init__(self, ip):
        self.ip = ip
        self.closed = False

    def close(self):
        self.closed = True


def _connector(behaviour, calls, release=None):
    """behaviour[ip] = "ok" | "fail" | "hang" (hang = block until `release` is set, then fail)."""
    def connect(ip, port, timeout):
        calls.append((ip, port, time.monotonic()))
        what = behaviour[ip]
        if what == "ok":
            return _Stream(ip)
        if what == "hang":
            release.wait(5.0)
            raise OSError(f"{ip}: timed out")
        raise OSError(f"{ip}: refused")
    return connect


def test_black_holed_ipv6_does_not_block_the_ipv4_winner():
    """The row's defect: a v6 route that never answers must not stall the v4 connect."""
    calls, release = [], threading.Event()
    connect = _connector({"2001:db8::1": "hang", "192.0.2.1": "ok"}, calls, release)
    started = time.monotonic()
    stream = happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 443, connect=connect,
                                         timeout=5.0, head_start=0.05)
    elapsed = time.monotonic() - started
    release.set()
    assert stream.ip == "192.0.2.1"
    assert elapsed < 1.0, f"caller waited on the hung v6 attempt: {elapsed:.2f}s"
    assert [c[0] for c in calls] == ["2001:db8::1", "192.0.2.1"]  # v6 first, v4 after the head start
    assert calls[1][2] - calls[0][2] >= 0.05 - 0.01


def test_ipv6_refusal_starts_ipv4_before_the_head_start_elapses():
    calls = []
    connect = _connector({"2001:db8::1": "fail", "192.0.2.1": "ok"}, calls)
    stream = happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 443, connect=connect,
                                         head_start=2.0)
    assert stream.ip == "192.0.2.1"
    assert calls[1][2] - calls[0][2] < 1.0, "v4 waited for the full head start after v6 had already failed"


def test_every_candidate_failing_raises_the_last_os_error():
    calls = []
    connect = _connector({"2001:db8::1": "fail", "192.0.2.1": "fail"}, calls)
    try:
        happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 443, connect=connect, head_start=0.01)
    except OSError as exc:
        assert "192.0.2.1" in str(exc)
    else:
        raise AssertionError("two refused connects returned a stream")
    assert len(calls) == 2


def test_single_candidate_connects_inline_and_a_transport_bug_is_not_a_lost_race():
    calls = []
    stream = happy_eyeballs.race_connect(("192.0.2.1",), 80, connect=_connector({"192.0.2.1": "ok"}, calls))
    assert stream.ip == "192.0.2.1" and threading.active_count() >= 1 and len(calls) == 1

    bug_calls = []

    def buggy(ip, port, timeout):
        bug_calls.append(ip)
        if ip == "2001:db8::1":
            raise TypeError("transport bug")
        return _Stream(ip)

    try:
        happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 80, connect=buggy, head_start=2.0)
    except TypeError:
        pass
    else:
        raise AssertionError("a non-OSError from connect was swallowed as a lost race")
    assert bug_calls == ["2001:db8::1"], "the bug was masked by racing the next candidate"


def test_late_winner_is_closed_not_leaked():
    calls, release = [], threading.Event()
    behaviour = {"2001:db8::1": "late", "192.0.2.1": "ok"}
    late = []

    def connect(ip, port, timeout):
        calls.append(ip)
        if behaviour[ip] == "late":
            release.wait(5.0)
            stream = _Stream(ip)
            late.append(stream)
            return stream
        return _Stream(ip)

    stream = happy_eyeballs.race_connect(("2001:db8::1", "192.0.2.1"), 443, connect=connect, head_start=0.01)
    assert stream.ip == "192.0.2.1"
    release.set()
    deadline = time.monotonic() + 2.0
    while not late and time.monotonic() < deadline:
        time.sleep(0.01)
    deadline = time.monotonic() + 2.0
    while late and not late[0].closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert late and late[0].closed, "the losing stream that connected late was not closed"


def test_backend_races_only_the_literals_pin_vetted_and_never_resolves(monkeypatch):
    """The row 703 invariant survives the race: pin() resolves once, the backend gets
    the pinned literal and its vetted sibling, and never calls getaddrinfo itself."""
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ]
    resolutions = []

    def getaddrinfo(*a, **k):
        resolutions.append(a[:2])
        return answers

    monkeypatch.setattr(ssrf_transport.socket, "getaddrinfo", getaddrinfo)
    transport = ssrf_transport.PinnedTransport()
    request = ssrf_transport.httpx.Request("GET", "https://cdn.example/video")
    assert transport.pin(request) == "8.8.8.8"
    assert transport._vetted_siblings["8.8.8.8"] == ("2001:4860:4860::8888", "8.8.8.8")

    raced = []
    monkeypatch.setattr(happy_eyeballs, "race_connect",
                        lambda candidates, port, **kw: raced.append((tuple(candidates), port)) or "stream")
    monkeypatch.setattr(ssrf_transport, "race_connect", happy_eyeballs.race_connect)
    backend = transport._pool._network_backend
    assert isinstance(backend, ssrf_transport._HappyEyeballsBackend)
    assert backend.connect_tcp("8.8.8.8", 443, timeout=1.0) == "stream"
    assert raced == [(("2001:4860:4860::8888", "8.8.8.8"), 443)]
    assert resolutions == [("cdn.example", 443)], "the backend must not resolve a second time"
    # a literal pin() never saw (or a single-family answer) connects plainly: one candidate
    assert backend.connect_tcp("203.0.113.9", 443) == "stream"
    assert raced[-1] == (("203.0.113.9",), 443)
