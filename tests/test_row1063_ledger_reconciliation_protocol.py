"""Row 1063 -- Multi-Node Cross-Replica Ledger Reconciliation Protocol.

The provenance ledger (provenance.py) is a hash chain: each row's
chain_hash = sha256(prev_chain || content_hash). verify_chain() proves ONE
ledger is internally consistent; nothing on base can say whether a
Litestream replica (db_replication.restore_store) or another node's
ledger is the SAME chain -- behind, ahead, or forked after some id.

Protocol (bulk_downloader.ledger_reconcile):
  digest      = {count, head_id, head_chain_hash, checkpoints[[id, chain_hash]...]}
                checkpoints on a fixed id grid + the head + any ids the peer
                asked for (want_ids), so two digests always share comparable ids.
  reconcile(local, remote) -> in_sync | behind | ahead | forked | unknown,
                never a guess: with no comparable id the verdict is 'unknown'.
Routes: GET /api/provenance/digest, POST /api/provenance/reconcile
(body: peer digest; answer carries the verdict + a local digest that
includes the peer's checkpoint ids).
"""
import importlib
import importlib.util
import sqlite3

import pytest
from flask import Flask

BD_GATE_SCOPE = "repo-wide"

prov = importlib.import_module("bulk_downloader.provenance")
app_prov = importlib.import_module("bulk_downloader.app_provenance")


def _mod():
    # Probe with find_spec and fail OUTSIDE any except block, so the base RED is this
    # capability assertion alone, not a chained ModuleNotFoundError traceback.
    if importlib.util.find_spec("bulk_downloader.ledger_reconcile") is None:
        pytest.fail("row1063: nothing reconciles two provenance ledger chains "
                    "(bulk_downloader.ledger_reconcile absent)")
    return importlib.import_module("bulk_downloader.ledger_reconcile")


_SCHEMA = """CREATE TABLE provenance(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, site_id TEXT NOT NULL,
    account TEXT DEFAULT '', source_url TEXT NOT NULL, resolved_url TEXT DEFAULT '',
    final_filename TEXT NOT NULL, file_size INTEGER DEFAULT 0, sha256 TEXT DEFAULT '',
    vpn_endpoint TEXT DEFAULT '', external_ip TEXT DEFAULT '', mirror_host TEXT DEFAULT '',
    ts_requested REAL DEFAULT 0, ts_started REAL DEFAULT 0, ts_finished REAL DEFAULT 0,
    content_hash TEXT NOT NULL, chain_hash TEXT NOT NULL, extra_json TEXT DEFAULT '')"""


def _ledger(n, *, fork_at=None, path=":memory:"):
    """A chained ledger of n rows built with provenance's own hash helpers.
    fork_at=k makes row k (1-based) carry different content, so every
    chain hash from k on differs from an unforked ledger."""
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.execute(_SCHEMA)
    prev = ""
    for i in range(1, n + 1):
        row = {"ts": float(i), "site_id": "s", "account": "", "source_url": f"u{i}",
               "resolved_url": "", "final_filename": f"f{i}", "file_size": i,
               "sha256": "", "vpn_endpoint": "", "external_ip": "", "mirror_host": "",
               "ts_requested": 0, "ts_started": 0, "ts_finished": 0, "extra_json": ""}
        if fork_at is not None and i == fork_at:
            row["source_url"] = f"forked{i}"
        ch = prov._row_content_hash(row)
        chain = prov._chain_hash(prev, ch)
        cols = list(row) + ["content_hash", "chain_hash"]
        cx.execute(f"INSERT INTO provenance({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [row[c] for c in row] + [ch, chain])
        prev = chain
    cx.commit()
    return cx


# ---------------------------------------------------------------- digest

def test_digest_shape_and_grid():
    m = _mod()
    d = m.digest_from_conn(_ledger(10), checkpoint_every=4)
    assert d["count"] == 10 and d["head_id"] == 10
    ids = [c[0] for c in d["checkpoints"]]
    assert ids == [4, 8, 10]          # grid + head, ascending
    assert d["head_chain_hash"] == d["checkpoints"][-1][1]
    assert len(d["head_chain_hash"]) == 64


def test_digest_want_ids_are_included():
    m = _mod()
    d = m.digest_from_conn(_ledger(10), checkpoint_every=4, want_ids=[3, 7, 99])
    ids = [c[0] for c in d["checkpoints"]]
    assert ids == [3, 4, 7, 8, 10]    # 99 does not exist: silently absent, never fabricated


def test_digest_of_empty_ledger():
    m = _mod()
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute(_SCHEMA)
    d = m.digest_from_conn(cx)
    assert d == {"count": 0, "head_id": 0, "head_chain_hash": "", "checkpoints": []}


def test_digest_from_replica_path_is_read_only(tmp_path):
    m = _mod()
    p = tmp_path / "replica.db"
    _ledger(5, path=str(p)).close()
    d = m.digest_from_path(str(p), checkpoint_every=2)
    assert d["count"] == 5 and [c[0] for c in d["checkpoints"]] == [2, 4, 5]
    with pytest.raises(m.ReplicaUnavailable):
        m.digest_from_path(str(tmp_path / "missing.db"))
    tableless = tmp_path / "tableless.db"
    sqlite3.connect(str(tableless)).execute("CREATE TABLE other(x)").connection.close()
    with pytest.raises(m.ReplicaUnavailable, match="provenance"):
        m.digest_from_path(str(tableless))


# ---------------------------------------------------------------- reconcile

def _pair(n_local, n_remote, *, fork_at=None, every=4):
    m = _mod()
    local = m.digest_from_conn(_ledger(n_local), checkpoint_every=every)
    remote = m.digest_from_conn(_ledger(n_remote, fork_at=fork_at), checkpoint_every=every)
    return m, local, remote


def test_in_sync():
    m, local, remote = _pair(10, 10)
    v = m.reconcile(local, remote)
    assert v["status"] == "in_sync" and v["common_id"] == 10 and v["fork_after_id"] is None


def test_behind_and_ahead_are_symmetric():
    m, local, remote = _pair(8, 12)          # local head 8 is on remote's grid
    v = m.reconcile(local, remote)
    assert v["status"] == "behind" and v["common_id"] == 8 and v["lag"] == 4
    w = m.reconcile(remote, local)
    assert w["status"] == "ahead" and w["common_id"] == 8 and w["lag"] == 4


def test_forked_names_last_agreeing_checkpoint():
    m, local, remote = _pair(12, 12, fork_at=6)
    v = m.reconcile(local, remote)
    assert v["status"] == "forked"
    assert v["common_id"] == 4 and v["fork_after_id"] == 4


def test_negative_control_same_length_different_content_is_not_in_sync():
    """Equal counts alone never mean in_sync: the head hashes decide."""
    m, local, remote = _pair(10, 10, fork_at=10)
    v = m.reconcile(local, remote)
    assert v["status"] != "in_sync" and v["common_id"] == 8


def test_no_comparable_id_is_unknown_not_a_verdict():
    m = _mod()
    local = m.digest_from_conn(_ledger(3), checkpoint_every=4)   # checkpoints: [3]
    remote = m.digest_from_conn(_ledger(9), checkpoint_every=4)  # checkpoints: [4, 8, 9]
    v = m.reconcile(local, remote)
    assert v["status"] == "unknown" and v["common_id"] == 0   # genesis only
    assert v["ask_ids"] == [3]        # what the peer must include to resolve


def test_empty_vs_nonempty():
    m = _mod()
    cx = sqlite3.connect(":memory:"); cx.row_factory = sqlite3.Row; cx.execute(_SCHEMA)
    empty = m.digest_from_conn(cx)
    full = m.digest_from_conn(_ledger(5), checkpoint_every=2)
    assert m.reconcile(empty, full)["status"] == "behind"
    assert m.reconcile(full, empty)["status"] == "ahead"
    assert m.reconcile(empty, empty)["status"] == "in_sync"


# ---------------------------------------------------------------- routes

def _client(monkeypatch, cx, csrf=lambda *a, **k: None):
    if importlib.util.find_spec("bulk_downloader.ledger_reconcile") is not None:
        # at base the module is absent; let the route answer be the RED
        monkeypatch.setattr(importlib.import_module("bulk_downloader.ledger_reconcile"),
                            "local_conn", lambda: cx)
    if csrf is not None:
        monkeypatch.setattr(app_prov, "_check_csrf", csrf)
    app = Flask("row1063")
    app.register_blueprint(app_prov.provenance_bp)
    return app.test_client()


def test_route_digest(monkeypatch):
    c = _client(monkeypatch, _ledger(10))
    r = c.get("/api/provenance/digest?checkpoint_every=4")
    assert r.status_code == 200, f"row1063: /api/provenance/digest answered {r.status_code}"
    body = r.get_json()
    assert body["ok"] is True and body["digest"]["count"] == 10


def test_route_reconcile_answers_with_peer_ids_included(monkeypatch):
    m = _mod()
    c = _client(monkeypatch, _ledger(12))
    peer = m.digest_from_conn(_ledger(7), checkpoint_every=4)     # [4, 7]
    r = c.post("/api/provenance/reconcile", json={"digest": peer, "checkpoint_every": 4})
    assert r.status_code == 200, f"row1063: /api/provenance/reconcile answered {r.status_code}"
    body = r.get_json()
    assert body["ok"] is True
    assert body["verdict"]["status"] == "ahead" and body["verdict"]["common_id"] == 7
    assert 7 in [cp[0] for cp in body["digest"]["checkpoints"]]


def test_route_reconcile_rejects_bad_digest(monkeypatch):
    c = _client(monkeypatch, _ledger(2))
    r = c.post("/api/provenance/reconcile", json={"digest": {"count": "x"}})
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_route_reconcile_rejects_a_body_that_is_not_an_object(monkeypatch):
    """T71 drop (DP-01): a JSON array/string/number body is a 400, not an AttributeError 500."""
    c = _client(monkeypatch, _ledger(2))
    for body in ([{"digest": {}}], "digest", 5):
        r = c.post("/api/provenance/reconcile", json=body)
        assert r.status_code == 400, f"row1063: non-object body {body!r} answered {r.status_code}"
        assert r.get_json()["ok"] is False


def test_route_reconcile_rejects_malformed_checkpoints(monkeypatch):
    """Bounce E2: the checkpoint-shape validation is pinned (a lax validator must fail this)."""
    m = _mod()
    c = _client(monkeypatch, _ledger(4))
    good = m.digest_from_conn(_ledger(4), checkpoint_every=2)
    for bad_cps in ([[2]], [[2, 3]], [["x", "ab"]], ["not-a-pair"], [[1, "a", "b"]]):
        r = c.post("/api/provenance/reconcile", json={"digest": dict(good, checkpoints=bad_cps)})
        assert r.status_code == 400, f"row1063: malformed checkpoints {bad_cps!r} answered {r.status_code}"
        assert r.get_json()["ok"] is False
    # control: the same digest with well-formed checkpoints is accepted
    assert c.post("/api/provenance/reconcile", json={"digest": good}).status_code == 200


def test_route_reconcile_honours_a_refusing_csrf_guard(monkeypatch):
    """Bounce E2: the guard's refusal is returned, and the ledger is never read."""
    _mod()
    reads = []

    def refuse(*_a, **_k):
        return {"error": "csrf refused"}, 403

    c = _client(monkeypatch, None, csrf=refuse)
    monkeypatch.setattr(importlib.import_module("bulk_downloader.ledger_reconcile"),
                        "local_conn", lambda: reads.append(1))
    r = c.post("/api/provenance/reconcile", json={"digest": {"count": 0, "head_id": 0,
                                                            "head_chain_hash": "", "checkpoints": []}})
    assert r.status_code == 403, f"row1063: reconcile ignored the CSRF guard ({r.status_code})"
    assert reads == []


def test_route_reconcile_refuses_cross_origin_post_with_real_guard(monkeypatch):
    """Bounce E2: the real app._check_csrf (not patched) refuses a cross-site POST; same-origin passes."""
    m = _mod()
    c = _client(monkeypatch, _ledger(3), csrf=None)
    peer = m.digest_from_conn(_ledger(3), checkpoint_every=2)
    r = c.post("/api/provenance/reconcile", json={"digest": peer},
               headers={"Origin": "http://evil.example", "Host": "localhost"})
    assert r.status_code == 403, f"row1063: cross-origin reconcile answered {r.status_code}"
    ok = c.post("/api/provenance/reconcile", json={"digest": peer},
                headers={"Origin": "http://localhost", "Host": "localhost"})
    assert ok.status_code == 200 and ok.get_json()["verdict"]["status"] == "in_sync"


def test_routes_on_a_node_that_never_wrote_provenance(monkeypatch, tmp_path):
    """Bounce R1: a fresh history DB has no provenance table yet (created lazily on the
    first record()). The digest is the empty ledger (200, count 0) and reconciling against
    a populated peer answers 'behind' -- not a 500 'no such table'. Nothing is written."""
    m = _mod()
    fresh = sqlite3.connect(str(tmp_path / "fresh_history.db"))
    fresh.row_factory = sqlite3.Row
    fresh.execute("CREATE TABLE unrelated(x)")
    c = _client(monkeypatch, fresh)
    r = c.get("/api/provenance/digest")
    assert r.status_code == 200, f"row1063 R1: fresh-node digest answered {r.status_code} {r.get_json()}"
    assert r.get_json()["digest"] == {"count": 0, "head_id": 0, "head_chain_hash": "", "checkpoints": []}
    peer = m.digest_from_conn(_ledger(5), checkpoint_every=2)
    r = c.post("/api/provenance/reconcile", json={"digest": peer, "checkpoint_every": 2})
    assert r.status_code == 200, f"row1063 R1: fresh-node reconcile answered {r.status_code} {r.get_json()}"
    v = r.get_json()["verdict"]
    assert v["status"] == "behind" and v["lag"] == 5
    names = {row[0] for row in fresh.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"unrelated"}, "digest path must stay read-only"



def test_both_routes_are_wired_to_an_spa_control():
    """T70 drop: /api/provenance/digest and /reconcile are operator-facing, so the ratchet
    (unwired_operator_endpoints) needs an SPA caller for each, found by the same scanner the
    route index uses. Positive control: an existing wired route is seen; an unwired one is not."""
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "gui_parity_inventory_row1063",
        pathlib.Path(__file__).resolve().parents[1] / "tools" / "gui_parity_inventory.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    _eps, method_eps = g._spa_wiring(pathlib.Path(__file__).resolve().parents[1])
    assert ("POST", "/api/interop/register") in method_eps, "scanner sees no SPA calls at all"
    assert ("POST", "/api/provenance/verify") not in method_eps
    missing = {("GET", "/api/provenance/digest"), ("POST", "/api/provenance/reconcile")} - method_eps
    assert not missing, f"row1063: no SPA control calls {sorted(missing)}"
