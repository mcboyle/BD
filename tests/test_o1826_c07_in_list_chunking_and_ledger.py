"""O1826 BRIEF-07: bounded IN-lists and ledger shard routing.

M052  discovery._already_seen bound one ``?`` per sitemap URL; past SQLite's
      bound-variable limit the query raised, the bare except returned set(),
      and every already-seen URL was reported (and enqueued) as new.
M088  ledger_reconcile.digest_from_conn did the same with head_id/every plus
      peer want_ids; validate_digest put no cap on a peer's checkpoints.
M089  ledger_sharding._extract_domain used netloc.split(":"), so userinfo and
      IPv6 literals sharded under "user" / "[".
M090  verify_all_partitions re-hashed every shard while holding the router
      lock, blocking record_entry for the whole pass.

The variable limit is the runtime library's own (Ubuntu builds raise the
upstream 32766 to 250000), so the over-limit cases use limit + 1.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

BD_GATE_SCOPE = "module"


def _variable_limit() -> int:
    return sqlite3.connect(":memory:").getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from bulk_downloader import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "o1826-c07.db"))
    return db


def test_positive_control_over_limit_in_list_raises():
    """Rule 7: an un-chunked IN-list of limit + 1 params really fails here."""
    n = _variable_limit() + 1
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE t(v TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="too many SQL variables"):
        cx.execute("SELECT v FROM t WHERE v IN (%s)" % ",".join("?" * n),
                   [str(i) for i in range(n)]).fetchall()


def test_already_seen_returns_full_set_past_variable_limit(isolated_db):
    from bulk_downloader import discovery
    urls = [f"https://site.example/p/{i}" for i in range(_variable_limit() + 1)]
    discovery._record_seen("site-c07", urls)
    seen = discovery._already_seen("site-c07", urls)
    assert len(seen) == len(urls), (
        f"M052: _already_seen returned {len(seen)} of {len(urls)} recorded URLs")


def test_already_seen_small_list_unchanged(isolated_db):
    from bulk_downloader import discovery
    discovery._record_seen("site-c07", ["https://a/1", "https://a/2"])
    assert discovery._already_seen("site-c07", ["https://a/1", "https://a/3"]) == {"https://a/1"}
    assert discovery._already_seen("other", ["https://a/1"]) == set()
    assert discovery._already_seen("site-c07", []) == set()


def _ledger(n: int) -> sqlite3.Connection:
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE provenance(id INTEGER PRIMARY KEY, chain_hash TEXT)")
    cx.executemany("INSERT INTO provenance(id, chain_hash) VALUES (?, ?)",
                   ((i, f"h{i}") for i in range(1, n + 1)))
    return cx


def test_digest_checkpoints_past_variable_limit():
    from bulk_downloader import ledger_reconcile as lr
    n = _variable_limit() + 1
    d = lr.digest_from_conn(_ledger(n), checkpoint_every=1)
    assert len(d["checkpoints"]) == n, (
        f"M088: digest carried {len(d['checkpoints'])} of {n} checkpoints")
    assert d["checkpoints"][0] == [1, "h1"] and d["checkpoints"][-1] == [n, f"h{n}"]
    assert [c[0] for c in d["checkpoints"]] == list(range(1, n + 1))


def test_digest_small_ledger_unchanged():
    from bulk_downloader import ledger_reconcile as lr
    d = lr.digest_from_conn(_ledger(10), checkpoint_every=4, want_ids=[3, 99])
    assert d == {"count": 10, "head_id": 10, "head_chain_hash": "h10",
                 "checkpoints": [[3, "h3"], [4, "h4"], [8, "h8"], [10, "h10"]]}


def test_validate_digest_caps_peer_checkpoints():
    from bulk_downloader import ledger_reconcile as lr
    cap = getattr(lr, "MAX_PEER_CHECKPOINTS", 100_000)
    ok = {"count": cap, "head_id": cap, "head_chain_hash": "h",
          "checkpoints": [[i, "h"] for i in range(1, cap + 1)]}
    assert len(lr.validate_digest(ok)["checkpoints"]) == cap
    over = dict(ok, checkpoints=ok["checkpoints"] + [[cap + 1, "h"]])
    with pytest.raises(ValueError, match="too many checkpoints"):
        lr.validate_digest(over)


def test_reconcile_caps_peer_side_only():
    """r1 F1: the peer cap applied to this node's OWN digest too, so a ledger
    of cap + 1 rows at checkpoint_every=1 (base: in_sync) raised."""
    from bulk_downloader import ledger_reconcile as lr
    n = lr.MAX_PEER_CHECKPOINTS + 1
    cx = _ledger(n)
    own = lr.digest_from_conn(cx, checkpoint_every=1)
    peer = lr.digest_from_conn(cx, checkpoint_every=n)
    assert len(own["checkpoints"]) == n and len(peer["checkpoints"]) == 1
    try:
        verdict = lr.reconcile(own, peer)
    except ValueError as e:
        pytest.fail(f"r1 F1: own digest of {n} checkpoints refused: {e}")
    assert verdict["status"] == "in_sync"
    with pytest.raises(ValueError, match="too many checkpoints"):
        lr.reconcile(peer, own)   # the same digest arriving FROM a peer is still capped


def test_route_reconcile_own_ledger_over_peer_cap(monkeypatch):
    """r1 F1 at the endpoint: checkpoint_every=1 on a ledger past the cap
    answered 500; base answered 200 in_sync."""
    from flask import Flask
    from bulk_downloader import app_provenance
    from bulk_downloader import ledger_reconcile as lr
    n = lr.MAX_PEER_CHECKPOINTS + 1
    cx = _ledger(n)
    monkeypatch.setattr(lr, "local_conn", lambda: cx)
    monkeypatch.setattr(app_provenance, "_check_csrf", lambda *a, **k: None)
    app = Flask("o1826-c07")
    app.register_blueprint(app_provenance.provenance_bp)
    peer = lr.digest_from_conn(cx, checkpoint_every=n)
    r = app.test_client().post("/api/provenance/reconcile",
                               json={"digest": peer, "checkpoint_every": 1})
    body = r.get_json()
    assert r.status_code == 200, f"r1 F1: reconcile answered {r.status_code}: {body.get('error')}"
    assert body["verdict"]["status"] == "in_sync"
    assert len(body["digest"]["checkpoints"]) == n


@pytest.mark.parametrize("url, domain", [
    ("http://user:pw@host/x", "host"),
    ("https://user@Host.Example:8443/x", "host.example"),
    ("http://[2001:db8::1]:8080/x", "2001:db8::1"),
])
def test_shard_domain_is_url_hostname(url, domain):
    from bulk_downloader import ledger_sharding as ls
    assert ls.MultiTenantLedgerRouter().resolve_shard_key("t", url, 0).domain == domain


@pytest.mark.parametrize("url, domain", [
    ("https://Example.com:443/a", "example.com"),
    ("https://example.com/a", "example.com"),
    ("example.com:80", "example.com"),
    ("", "default"),
    ("http:///path", "default"),
])
def test_shard_domain_plain_hosts_unchanged(url, domain):
    from bulk_downloader import ledger_sharding as ls
    assert ls.MultiTenantLedgerRouter().resolve_shard_key("t", url, 0).domain == domain


def test_verify_all_partitions_hashes_outside_router_lock(monkeypatch):
    from bulk_downloader import ledger_sharding as ls
    router = ls.MultiTenantLedgerRouter()
    router.record_entry("t", "https://a.example/1", {"k": 1}, timestamp=0)
    hashing, release = threading.Event(), threading.Event()
    real_verify = ls.LedgerPartitionShard.verify_chain

    def slow_verify(shard):
        hashing.set()
        release.wait(30)
        return real_verify(shard)

    monkeypatch.setattr(ls.LedgerPartitionShard, "verify_chain", slow_verify)
    report = {}
    verifier = threading.Thread(target=lambda: report.update(router.verify_all_partitions()))
    verifier.start()
    try:
        assert hashing.wait(10)
        writer = threading.Thread(
            target=router.record_entry, args=("t", "https://b.example/1", {"k": 2}, 0))
        writer.start()
        writer.join(5)
        blocked = writer.is_alive()
    finally:
        release.set()
        verifier.join(30)
    writer.join(30)
    assert not blocked, "M090: record_entry blocked behind verify_all_partitions"
    assert report["valid"] is True and report["tampered_partitions"] == []


def test_verify_all_partitions_snapshot_blocks_concurrent_writer():
    """r1 F2: the shard snapshot must hold the router lock. A writer adding a
    new shard mid-snapshot has to wait; without the lock it mutates _shards
    during iteration ("dictionary changed size during iteration")."""
    from bulk_downloader import ledger_sharding as ls
    router = ls.MultiTenantLedgerRouter()
    for host in ("a", "b", "c"):
        router.record_entry("t", f"https://{host}.example/1", {"k": host}, timestamp=0)
    writers, wrote, waited = [], [], []

    def write_new_shard():
        wrote.append(router.record_entry("t", "https://new.example/1", {"k": "new"}, 0))

    class WriterMidSnapshot(dict):
        def values(self):
            for n, shard in enumerate(dict.values(self)):
                yield shard
                if n == 0:   # start a writer while the snapshot is part-way through
                    w = threading.Thread(target=write_new_shard)
                    writers.append(w)
                    w.start()
                    w.join(2)
                    waited.append(w.is_alive())

    router._shards = WriterMidSnapshot(router._shards)
    try:
        report = router.verify_all_partitions()
    except RuntimeError as e:
        report = {"error": str(e)}
    for w in writers:
        w.join(30)
    assert waited == [True], f"r1 F2: record_entry ran inside the shard snapshot: {report}"
    assert len(wrote) == 1 and len(router._shards) == 4
    assert report["valid"] is True and report["verified_count"] == 3
