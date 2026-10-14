"""fed-input-lows r1 -- federation-mesh FINDINGS F10 F11 F13 (S68-D probes P11a P11b P13 P25 P26 P28 P30 P32 P32b P39)
plus the fed-trust r1 owed wiring: manual_register is the operator call (register_peer operator=True).

F10 claim_url ttl_seconds was unbounded (1e12 held a URL ~31,700 years, P11a); a non-integer ttl
    (P11b) or announce last_history_id (P13) answered 500 with the int() text.
F11 sync_pull limit=-1 returned the whole history (P39); /api/deploy/timeline limit=-1 every row
    (P32) and since_ts=nan a silent empty timeline (P32b).
F13 service_mesh forwarded "/../admin" past a path-bearing traefik_url (P25) and skipped https
    bases (503, P26); ipc_latency get_matrix raced record_rtt (P28) and t3 < t0 became a 0 ms
    sample (P30).
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import threading
import time

import pytest

from bulk_downloader import db
from bulk_downloader import deployment_timeline as dt
from bulk_downloader import federation as fed
from bulk_downloader import ipc_latency as il
from bulk_downloader import service_mesh as sm


@pytest.fixture
def client(fresh_app, monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    fed._ensure_tables()
    return fresh_app


def _lifetime(url):
    c = fed.is_claimed(url) or {}
    return float(c.get("expires_at", 0)) - float(c.get("claimed_at", 0))


# ── F10: claim ttl bound, non-integer fields -> 400 ──────────────────

def test_f10_claim_ttl_is_bounded(client):
    r = client.post("/api/fed/claim_url", json={"url": "http://x.test/1", "instance_id": "a",
                                                 "ttl_seconds": 10 ** 12})
    life = _lifetime("http://x.test/1")
    assert r.status_code == 200 and 0 < life <= 86400, \
        f"fed-input-lows F10: ttl 1e12 -> {r.status_code}, claim lifetime {life:.0f} s (> 1 day)"


def test_f10_negative_ttl_still_claims(client):
    ok = fed.claim_url("http://x.test/neg", "a", ttl_seconds=-5)
    assert ok and fed.is_claimed("http://x.test/neg"), \
        f"fed-input-lows F10: ttl -5 -> claim_url {ok} but no live claim (expired on write)"


def test_f10_control_ttl_in_range_kept_and_second_peer_refused(client):
    r = client.post("/api/fed/claim_url", json={"url": "http://x.test/2", "instance_id": "a",
                                                 "ttl_seconds": 600})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert abs(_lifetime("http://x.test/2") - 600) < 1
    assert fed.claim_url("http://x.test/2", "b") is False


@pytest.mark.parametrize("bad", ["x", "1.5", [1], {"a": 1}, pytest.param("9" * 4301, id="digits4301")])
def test_f10_non_integer_ttl_is_400(client, bad):
    r = client.post("/api/fed/claim_url", json={"url": "http://x.test/3", "instance_id": "a",
                                                 "ttl_seconds": bad})
    assert r.status_code == 400, f"fed-input-lows F10: ttl {bad!r:.40} -> {r.status_code} {r.get_json()}"
    assert r.get_json() == {"ok": False, "error": "ttl_seconds must be an integer"}
    assert fed.is_claimed("http://x.test/3") is None


@pytest.mark.parametrize("bad", ["x", "1.5", [1], pytest.param("9" * 4301, id="digits4301")])
def test_f10_non_integer_last_history_id_is_400(client, bad):
    r = client.post("/api/fed/announce", json={"instance_id": "z", "base_url": "http://z.test",
                                                "last_history_id": bad})
    assert r.status_code == 400, \
        f"fed-input-lows F10: announce last_history_id {bad!r:.40} -> {r.status_code} {r.get_json()}"
    assert r.get_json() == {"ok": False, "error": "last_history_id must be an integer"}
    assert "z" not in {p["instance_id"] for p in fed.active_peers()}


def test_f10_control_integer_last_history_id_registers(client):
    r = client.post("/api/fed/announce", json={"instance_id": "z", "base_url": "http://z.test",
                                                "last_history_id": "7"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert {p["instance_id"]: p for p in fed.active_peers()}["z"]["last_history_id"] == 7


# ── F11: limit bound, nan since_ts -> 400 ────────────────────────────

@pytest.fixture
def history600(client):
    with db.db_conn() as cx:
        for i in range(600):
            cx.execute("INSERT INTO history(site_id, url, status, filename, ts) VALUES (?,?,?,?,?)",
                       ("s", f"http://h.test/{i}", "done", f"f{i}", 1.0))
    return client


@pytest.mark.parametrize("limit", ["-1", "1000000"])
def test_f11_sync_pull_limit_is_bounded(history600, limit):
    r = history600.get(f"/api/fed/sync_pull?limit={limit}")
    n = len(r.get_json()["rows"])
    assert r.status_code == 200 and n <= 500, f"fed-input-lows F11: sync_pull limit={limit} -> {n} rows (> 500)"


def test_f11_history_since_clamps_direct_callers(history600):
    n = len(fed.history_since(0, limit=-1))
    assert 1 <= n <= 500, f"fed-input-lows F11: history_since(limit=-1) -> {n} rows"


def test_f11_control_sync_pull_limit_and_default(history600):
    assert len(history600.get("/api/fed/sync_pull?limit=5").get_json()["rows"]) == 5
    assert len(history600.get("/api/fed/sync_pull").get_json()["rows"]) == 500


def test_f11_sync_pull_limit_zero_is_clamped_to_one(history600):
    n = len(history600.get("/api/fed/sync_pull?limit=0").get_json()["rows"])
    assert n == 1, f"fed-input-lows F11: sync_pull limit=0 -> {n} rows (lower clamp 1 missing)"


@pytest.mark.parametrize("q", ["limit=abc", "since_id=abc", "limit=1.5",
                               pytest.param("since_id=" + "9" * 4301, id="digits4301")])
def test_f11_sync_pull_non_integer_is_400(client, q):
    r = client.get(f"/api/fed/sync_pull?{q}")
    assert r.status_code == 400, f"fed-input-lows F11: sync_pull {q[:40]} -> {r.status_code} {r.get_json()}"
    assert r.get_json() == {"error": "since_id and limit must be integers"}


@pytest.fixture
def events150(client):
    tl = dt.get_deployment_timeline()
    dep = tl.create_deployment("r", target_env="fil")
    for i in range(150):
        tl.record_event(dep.deployment_id, "progress_updated", "pending", f"m{i}")
    return client


def test_f11_deploy_timeline_negative_limit_is_bounded(events150):
    n = len(events150.get("/api/deploy/timeline?limit=-1").get_json()["timeline"])
    assert n == 1, f"fed-input-lows F11: deploy timeline limit=-1 -> {n} entries (unbounded)"


def test_f11_deploy_timeline_upper_bound(events150, monkeypatch):
    monkeypatch.setattr(dt, "MAX_TIMELINE_LIMIT", 40, raising=False)
    n = len(events150.get("/api/deploy/timeline?limit=1000000").get_json()["timeline"])
    assert n == 40, f"fed-input-lows F11: deploy timeline limit=1e6 -> {n} entries (cap 40 ignored)"


@pytest.mark.parametrize("ts", ["nan", "NaN", "inf", "-inf", "1e400"])
def test_f11_deploy_timeline_non_finite_since_ts_is_400(events150, ts):
    r = events150.get(f"/api/deploy/timeline?since_ts={ts}")
    assert r.status_code == 400, \
        f"fed-input-lows F11: since_ts={ts} -> {r.status_code} timeline={len((r.get_json() or {}).get('timeline', []))}"
    assert r.get_json() == {"error": "since_ts must be a finite number"}


def test_f11_control_deploy_timeline_limits(events150):
    assert len(events150.get("/api/deploy/timeline?limit=5").get_json()["timeline"]) == 5
    assert len(events150.get("/api/deploy/timeline?since_ts=0").get_json()["timeline"]) == 100
    assert events150.get("/api/deploy/timeline?since_ts=abc").status_code == 400


# ── F13: service mesh dot segments + https, ipc lock + clock ─────────

def _mesh(routes):
    from flask import Flask
    calls = []

    def transport(url, *, headers, payload):
        calls.append(url)
        return {"status": 200, "body": {"u": url}}
    a = Flask("fil-mesh")
    a.config.update(SERVICE_MESH_ENABLED=True, SERVICE_MESH_TRANSPORT=transport, SERVICE_MESH_ROUTES=routes)
    sm.register_service_mesh(a)
    return a.test_client(), calls


SVC = {"svc": {"host": "svc.internal", "traefik_url": "http://traefik:80/svc", "direct_urls": []}}


@pytest.mark.parametrize("path", ["/../admin", "/%2e%2e/admin", "/%2E%2e/admin", "/a/./b", "/a/%2e/b",
                                  "/a/..", "/.", "/a/.%2e/b"])
def test_f13_dot_segments_refused(path):
    c, calls = _mesh(SVC)
    r = c.post("/api/service-mesh/svc", json={"path": path})
    assert r.status_code == 400 and calls == [], \
        f"fed-input-lows F13: path {path} -> {r.status_code}, forwarded {calls}"


@pytest.mark.parametrize("path", ["/ok", "/a..b", "/.well-known/x", "/a/.../b", "/a.b/c."])
def test_f13_control_dotted_names_forwarded(path):
    c, calls = _mesh(SVC)
    r = c.post("/api/service-mesh/svc", json={"path": path})
    assert r.status_code == 200 and calls == [f"http://traefik:80/svc{path}"]


def test_f13_https_base_is_tried():
    c, calls = _mesh({"tls": {"host": "t", "traefik_url": "https://traefik:443", "direct_urls": ["https://d:443"]}})
    r = c.post("/api/service-mesh/tls", json={"path": "/x"})
    assert r.status_code == 200 and calls == ["https://traefik:443/x"], \
        f"fed-input-lows F13: https-only service -> {r.status_code} {r.get_json()}, calls {calls}"


def test_f13_control_other_schemes_still_skipped():
    c, calls = _mesh({"f": {"host": "t", "traefik_url": "ftp://traefik", "direct_urls": ["file:///etc"]}})
    r = c.post("/api/service-mesh/f", json={"path": "/x"})
    assert r.status_code == 503 and calls == []


def test_f13_matrix_readers_survive_a_write_mid_iteration():
    """Deterministic interleave: a record lands while get_matrix walks the pairs."""
    m = il.IPCLatencyMatrix(max_pairs=8)
    for i in range(4):
        m.record_rtt("s", f"r{i}", 1.0)
    orig = m.get_pair_summary
    n = iter(range(100))

    def summary_with_a_concurrent_write(src, dst):
        m.record_rtt("w", f"new{next(n)}", 2.0)
        return orig(src, dst)
    m.get_pair_summary = summary_with_a_concurrent_write
    for reader in (m.get_matrix, m.get_degraded_links, m.get_highest_latency_pairs):
        try:
            reader()
        except RuntimeError as e:
            pytest.fail(f"fed-input-lows F13: {reader.__name__} with a concurrent record_rtt -> RuntimeError {e}")


def test_f13_matrix_writer_inside_a_writer_keeps_max_pairs():
    """Deterministic interleave: writer B runs while writer A sits between its evict and its insert."""
    import collections
    m = il.IPCLatencyMatrix(max_pairs=2)
    m.record_rtt("s", "k1", 1.0)
    m.record_rtt("s", "k2", 1.0)
    fired = []

    class Interleave(collections.OrderedDict):
        def __setitem__(self, key, value):
            if key == ("s", "k3") and not fired:
                fired.append(1)
                b = threading.Thread(target=m.record_rtt, args=("s", "k4", 1.0))
                b.start()
                b.join(0.5)  # with the lock B blocks here until A releases; without it B runs now
                fired.append(b)
            super().__setitem__(key, value)
    m._pairs = Interleave(m._pairs)
    m.record_rtt("s", "k3", 1.0)
    fired[1].join(5)
    assert len(m._pairs) <= 2, \
        f"fed-input-lows F13: two writers interleaved -> {len(m._pairs)} pairs {list(m._pairs)} (max_pairs 2)"


def test_f13_matrix_concurrent_writers_and_reader():
    m = il.IPCLatencyMatrix(max_pairs=8)
    errs, stop = [], threading.Event()

    def w(k):
        i = 0
        while not stop.is_set():
            m.record_rtt(f"s{k}", f"r{i % 64}", 1.0)
            i += 1

    def rd():
        while not stop.is_set():
            try:
                m.get_matrix()
            except RuntimeError as e:
                errs.append(str(e))
    ts = [threading.Thread(target=w, args=(k,)) for k in range(3)] + [threading.Thread(target=rd)]
    for t in ts:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in ts:
        t.join()
    assert not errs, f"fed-input-lows F13: concurrent get_matrix errors {len(errs)} first={errs[:1]}"
    assert len(m.get_matrix()) >= 1


def test_f13_clock_backwards_is_not_a_zero_ms_sample():
    with pytest.raises(ValueError, match="precedes"):
        s = il.IPCPingSample("a", "b", t0_ns=10_000_000, t3_ns=5_000_000)
        pytest.fail(f"fed-input-lows F13: t3 < t0 -> rtt_ms={s.rtt_ms} (recorded as a real sample)")


def test_f13_control_forward_clock_sample():
    assert il.IPCPingSample("a", "b", t0_ns=1_000_000, t3_ns=3_500_000).rtt_ms == 2.5
    assert il.IPCPingSample("a", "b", t0_ns=5, t3_ns=5).rtt_ms == 0.0


# ── fed-trust r1 owed wiring: manual_register is the operator call ───

def test_manual_register_operator_moves_peer(client):
    assert fed.register_peer("op", "http://old.test")
    r = client.post("/api/fed/manual_register", json={"instance_id": "op", "base_url": "http://new.test"})
    got = {p["instance_id"]: p for p in fed.active_peers()}["op"]["base_url"]
    assert got == "http://new.test", \
        f"fed-input-lows: operator manual_register left op at {got} ({r.status_code} {r.get_json()})"
    assert r.get_json() == {"ok": True}


def test_control_announce_still_cannot_move_peer(client):
    assert fed.register_peer("op", "http://old.test")
    r = client.post("/api/fed/announce", json={"instance_id": "op", "base_url": "http://new.test"})
    assert r.get_json() == {"ok": False}
    assert {p["instance_id"]: p for p in fed.active_peers()}["op"]["base_url"] == "http://old.test"
