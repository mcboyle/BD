"""A DNS health probe is bounded by its own timeout and never touches the process socket default.

sl-dns (O1789 sites-lifecycle row 4, PM ruling 1540Z item 2): probe_dns used to set socket.setdefaulttimeout(timeout)
for the lookup. That default is process-global: a socket any other thread created meanwhile got the probe's timeout,
and gethostbyname is not bounded by it anyway. The old assertion here pinned that bug --
`observed == [("good.example", 0.25), ("bad.example", 0.5)]` (the default WAS the probe timeout during the lookup).
It now reads the process value (None / 7.0) during the lookup.
"""

import socket
import threading
import time
import types

import pytest

from bulk_downloader import site_weather

BD_GATE_SCOPE = "module"


@pytest.fixture(autouse=True)
def _fresh_dns_inflight(monkeypatch):
    # sl-dns r2: every test starts with no outstanding lookup (raising=False: the r1 tree has no table).
    monkeypatch.setattr(site_weather, "_dns_inflight", {}, raising=False)


def test_probe_dns_leaves_the_default_timeout_alone_on_success_and_failure(monkeypatch):
    observed = []
    recorded = []
    original = socket.getdefaulttimeout()
    monkeypatch.setattr(
        site_weather, "_record", lambda *a, **kw: recorded.append((a, kw))
    )

    def resolve(host):
        observed.append((host, socket.getdefaulttimeout()))
        if host == "bad.example":
            raise OSError("lookup failed")
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    try:
        socket.setdefaulttimeout(None)
        good = site_weather.probe_dns("test-site", "good.example", timeout=0.25)
        assert good["ok"] is True
        assert good["ip"] == "203.0.113.8"
        assert socket.getdefaulttimeout() is None
        bad = site_weather.probe_dns("test-site", "bad.example", timeout=0.5)
        assert bad["ok"] is False and "lookup failed" in bad["error"]
        assert socket.getdefaulttimeout() is None
        assert observed == [("good.example", None), ("bad.example", None)], (
            f"DNS_PROBE_SET_GLOBAL_TIMEOUT {observed}"
        )
        assert [entry[0][2] for entry in recorded] == [True, False]

        # lens (B1): a process that set its own default keeps it -- during the lookup too.
        socket.setdefaulttimeout(7.0)
        site_weather.probe_dns("test-site", "good.example", timeout=0.25)
        assert socket.getdefaulttimeout() == 7.0
        assert observed[-1] == ("good.example", 7.0), (
            f"DNS_PROBE_SET_GLOBAL_TIMEOUT {observed[-1]}"
        )
    finally:
        socket.setdefaulttimeout(original)


def test_a_socket_made_in_another_thread_during_a_probe_keeps_the_process_default(
    monkeypatch,
):
    """FINDINGS repro P4: a socket created by another thread while a probe runs got gettimeout() 5.0."""
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: None)
    in_lookup, release = threading.Event(), threading.Event()

    def resolve(host):
        in_lookup.set()
        release.wait(5)
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    original = socket.getdefaulttimeout()
    result = {}
    try:
        socket.setdefaulttimeout(None)
        prober = threading.Thread(
            target=lambda: result.update(
                site_weather.probe_dns("test-site", "slow.example", timeout=5.0)
            )
        )
        prober.start()
        assert in_lookup.wait(5), "the probe never reached its lookup"
        with socket.socket() as other:
            seen = other.gettimeout()
        release.set()
        prober.join(10)
        assert seen is None, (
            f"DNS_PROBE_LEAKED_TIMEOUT other-thread socket gettimeout()={seen}"
        )
        assert result.get("ok") is True
    finally:
        release.set()
        socket.setdefaulttimeout(original)


def test_a_hung_lookup_is_cut_off_at_the_probe_timeout(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        site_weather, "_record", lambda *a, **kw: recorded.append((a, kw))
    )
    release = threading.Event()

    def resolve(host):
        release.wait(
            5
        )  # a resolver that hangs; gethostbyname never honoured the socket default
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    try:
        started = time.monotonic()
        out = site_weather.probe_dns("test-site", "hang.example", timeout=0.2)
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, (
            f"DNS_PROBE_UNBOUNDED took {elapsed:.2f}s for timeout=0.2 -> {out}"
        )
        assert out["ok"] is False and "timed out" in out["error"], out
        assert [entry[0][2] for entry in recorded] == [False]
    finally:
        release.set()


def test_control_a_fast_lookup_inside_the_timeout_succeeds(monkeypatch):
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: None)
    monkeypatch.setattr(
        site_weather.socket, "gethostbyname", lambda host: "198.51.100.7"
    )
    out = site_weather.probe_dns("test-site", "fast.example", timeout=0.2)
    assert out["ok"] is True and out["ip"] == "198.51.100.7", out


# ─── sl-dns r2 (cx178 F1): abandoned lookups are bounded, not one new thread per probe ───


def _stalled_resolver(monkeypatch, release):
    started = []
    lock = threading.Lock()

    def resolve(host):
        with lock:
            started.append((host, threading.current_thread()))
        release.wait(5)
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    return started


def _join_all(started):
    for _, thread in started:
        thread.join(5)
    assert not any(t.is_alive() for _, t in started)


def test_r2_repeated_probes_of_a_stalled_host_reuse_one_lookup(monkeypatch):
    """cx178 repro: 12 probes of one stalled host retained 4, 8, 12 daemon workers."""
    recorded = []
    monkeypatch.setattr(
        site_weather, "_record", lambda *a, **kw: recorded.append((a, kw))
    )
    release = threading.Event()
    started = _stalled_resolver(monkeypatch, release)
    try:
        counts = []
        for _batch in range(3):
            for _ in range(4):
                out = site_weather.probe_dns("test-site", "stall.example", timeout=0.015)
                assert out["ok"] is False and "timed out" in out["error"], out
            counts.append(sum(t.is_alive() for _, t in started))
        assert counts == [1, 1, 1], f"DNS_PROBE_WORKERS_UNBOUNDED retained={counts}"
        assert len(started) == 1
        assert [entry[0][2] for entry in recorded] == [False] * 12
    finally:
        release.set()
        _join_all(started)
    assert site_weather._dns_inflight == {}


def test_r2_stalled_lookups_of_many_hosts_are_capped_and_reported_busy(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        site_weather, "_record", lambda *a, **kw: recorded.append((a, kw))
    )
    release = threading.Event()
    started = _stalled_resolver(monkeypatch, release)
    cap = site_weather._DNS_MAX_OUTSTANDING
    try:
        outs = [
            site_weather.probe_dns("test-site", f"h{i}.example", timeout=0.015)
            for i in range(12)
        ]
        alive = sum(t.is_alive() for _, t in started)
        assert len(started) == cap and alive == cap, (
            f"DNS_PROBE_WORKERS_UNBOUNDED created={len(started)} alive={alive} cap={cap}"
        )
        assert all("timed out" in o["error"] for o in outs[:cap]), outs[:cap]
        busy = outs[cap:]
        assert all(o["ok"] is False and "busy" in o["error"] for o in busy), (
            f"DNS_PROBE_BUSY_NOT_REPORTED {busy}"
        )
        assert [entry[0][2] for entry in recorded] == [False] * 12
    finally:
        release.set()
        _join_all(started)
    # the slots come back once the resolver returns: a new host resolves again
    assert site_weather._dns_inflight == {}, "DNS_PROBE_SLOT_NOT_FREED"
    monkeypatch.setattr(site_weather.socket, "gethostbyname", lambda host: "198.51.100.9")
    again = site_weather.probe_dns("test-site", "h99.example", timeout=0.5)
    assert again["ok"] is True and again["ip"] == "198.51.100.9", again


def test_r2_a_probe_waiting_on_an_outstanding_lookup_gets_its_answer(monkeypatch):
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: None)
    release = threading.Event()
    started = _stalled_resolver(monkeypatch, release)
    try:
        first = site_weather.probe_dns("test-site", "late.example", timeout=0.015)
        assert first["ok"] is False and "timed out" in first["error"], first
        threading.Timer(0.1, release.set).start()
        second = site_weather.probe_dns("test-site", "late.example", timeout=3.0)
        assert second["ok"] is True and second["ip"] == "203.0.113.8", second
        assert len(started) == 1, f"DNS_PROBE_WORKERS_UNBOUNDED created={len(started)}"
    finally:
        release.set()
        _join_all(started)


def test_r2_control_fast_lookups_retain_no_worker(monkeypatch):
    before = set(threading.enumerate())
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: None)
    monkeypatch.setattr(site_weather.socket, "gethostbyname", lambda _: "203.0.113.8")
    for i in range(12):
        out = site_weather.probe_dns("test-site", f"f{i % 3}.example", timeout=0.5)
        assert out["ok"] is True and set(out) == {"ok", "ip", "latency_ms"}, out
    assert set(threading.enumerate()) == before
    assert getattr(site_weather, "_dns_inflight", {}) == {}


def test_r2_a_concurrent_default_change_survives_a_probe(monkeypatch):
    """cx178 own fixture: a default another thread sets while a probe runs is not clobbered."""
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: None)
    entered, release = threading.Event(), threading.Event()
    result = []

    def resolve(_):
        entered.set()
        release.wait(5)
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    original = socket.getdefaulttimeout()
    caller = threading.Thread(
        target=lambda: result.append(
            site_weather.probe_dns("test-site", "conc.example", timeout=3)
        )
    )
    try:
        socket.setdefaulttimeout(None)
        caller.start()
        assert entered.wait(3)
        socket.setdefaulttimeout(7)
        release.set()
        caller.join(5)
        assert result and result[0]["ok"] is True, result
        assert socket.getdefaulttimeout() == 7, "DNS_PROBE_CLOBBERED_CONCURRENT_DEFAULT"
    finally:
        release.set()
        caller.join(5)
        socket.setdefaulttimeout(original)


# ─── sl-dns r3 (cx206 F1): a busy local resolver is not a failure of the probed site ───


@pytest.fixture
def _weather_db(monkeypatch, tmp_path):
    import contextlib
    import sqlite3

    import bulk_downloader

    path = tmp_path / "weather.sqlite"

    @contextlib.contextmanager
    def db_conn():
        cx = sqlite3.connect(path)
        try:
            with cx:
                yield cx
        finally:
            cx.close()

    monkeypatch.setattr(bulk_downloader, "db", types.SimpleNamespace(db_conn=db_conn), raising=False)


@pytest.mark.parametrize("occupied", [0, 4])
def test_r3_a_busy_resolver_leaves_a_healthy_site_as_base_reports_it(monkeypatch, _weather_db, occupied):
    """cx206 F1 proof: `occupied` stalled hosts hold the lookup slots (4 = all of them on the r2 tree), then a
    healthy site whose HTTP answers 200 is probed. BASE reports it overall_ok / green / 100.0; occupied=0 is the
    zero-slot control."""
    release = threading.Event()
    entered = [threading.Event() for _ in range(occupied)]
    lookups = []

    def resolve(host):
        if host.startswith("stall"):
            lookups.append(threading.current_thread())
            entered[int(host[5])].set()
            release.wait(5)
        return "203.0.113.8"

    def http(site_id, url):
        site_weather._record(site_id, "http", True, latency_ms=1, status_code=200)
        return {"ok": True, "status_code": 200, "latency_ms": 1}

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    monkeypatch.setattr(site_weather, "probe_http", http)
    original = socket.getdefaulttimeout()
    stallers = [
        threading.Thread(target=site_weather.probe_dns, args=(f"stall{i}", f"stall{i}.example"),
                         kwargs={"timeout": 0.03})
        for i in range(occupied)
    ]
    try:
        for t in stallers:
            t.start()
        assert all(e.wait(3) for e in entered), "fixture: stalled lookups never started"
        out = site_weather.probe_one("healthy", {"weather_url": "https://healthy.example/"})
        status = site_weather.site_status("healthy")
        dns = out["probes"]["dns"]
        assert dns["ok"] is True or "busy" in dns["error"], f"BUSY_PATH_NOT_REACHED {dns}"
        got = (out["overall_ok"], status["color"], status["success_rate"])
        assert got == (True, "green", 100.0), (
            f"LOCAL_BUSY_MARKED_HEALTHY_SITE_DOWN occupied={occupied} got={got} out={out} status={status}"
        )
    finally:
        release.set()
        for t in stallers + lookups:
            t.join(5)
        socket.setdefaulttimeout(original)
