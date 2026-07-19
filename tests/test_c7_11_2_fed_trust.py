"""RED-first contract for C7 11.2 -- federation trust tiers + peer drift.

Scope-confirm found federation is already a complete DOWNLOAD-coordination system
(federation.py 11 fns + app_fed.py 6 routes + Cluster.tsx wired): HMAC-signed
announce, peer registry, URL claim/release, download-history feed. The plan's
"template push/pull" describes a different feature already covered by marketplace;
Option 1 hardens what EXISTS with the two genuinely-missing pieces:

  * TRUST TIERS -- each peer carries a trust_tier (trusted | observed | blocked;
    default observed). register_peer PRESERVES an existing tier on refresh (an
    announce can't self-elevate). A BLOCKED peer cannot claim URLs (trust gates
    coordination). set_peer_trust(instance_id, tier) sets it; POST /api/fed/set_trust
    exposes it (operator-gated, non-sensitive route).
  * PEER DRIFT -- peer_drift() reports, per peer, how far its last_history_id lags
    this instance's local max history id (local_max - peer.last_history_id), so an
    operator can see which peers are behind. status() gains peers_behind count.

Pre-fix: set_peer_trust / peer_drift / the trust column / the route don't exist -> RED.

Sandbox-runner conventions: zero-arg, BD_HOME temp db (runner sets it), chdir-free
(db path is install-relative under the temp home), no monkeypatch.
"""
from __future__ import annotations


def _fresh_fed():
    """Import federation against the runner's temp BD_HOME db and ensure tables."""
    from bulk_downloader import federation as fed
    fed._ensure_tables()
    return fed


def test_register_peer_defaults_observed_tier():
    fed = _fresh_fed()
    fed.register_peer("peerA", "http://a.local")
    peers = {p["instance_id"]: p for p in fed.active_peers()}
    assert "trust_tier" in peers["peerA"], "fed_peers must carry a trust_tier"
    assert peers["peerA"]["trust_tier"] == "observed"


def test_set_peer_trust_and_preserve_on_refresh():
    fed = _fresh_fed()
    fed.register_peer("peerB", "http://b.local")
    assert fed.set_peer_trust("peerB", "trusted") is True
    peers = {p["instance_id"]: p for p in fed.active_peers()}
    assert peers["peerB"]["trust_tier"] == "trusted"
    # a subsequent announce (register_peer) must NOT reset the tier to observed
    fed.register_peer("peerB", "http://b.local", version="9.9")
    peers = {p["instance_id"]: p for p in fed.active_peers()}
    assert peers["peerB"]["trust_tier"] == "trusted", "announce self-elevated/reset trust"
    # invalid tier rejected
    assert fed.set_peer_trust("peerB", "bogus") is False


def test_blocked_peer_cannot_claim():
    fed = _fresh_fed()
    fed.register_peer("badpeer", "http://bad.local")
    fed.set_peer_trust("badpeer", "blocked")
    # a blocked peer's claim is refused
    assert fed.claim_url("http://x/v.mp4", "badpeer") is False
    # an observed/trusted peer can still claim
    fed.register_peer("okpeer", "http://ok.local")
    assert fed.claim_url("http://x/v.mp4", "okpeer") is True


def test_peer_drift_reports_lag():
    fed = _fresh_fed()
    # seed local history so there is a local max id
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS history("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT, url TEXT, "
                   "status TEXT, filename TEXT, file_size INTEGER, ts REAL)")
        for i in range(5):
            cx.execute("INSERT INTO history(site_id,url,status,ts) "
                       "VALUES('s','u','done',0)")
    # peerC is 2 behind (claims last_history_id=3 vs local max 5)
    fed.register_peer("peerC", "http://c.local", last_history_id=3)
    drift = {d["instance_id"]: d for d in fed.peer_drift()}
    assert "peerC" in drift
    assert drift["peerC"]["local_max"] == 5
    assert drift["peerC"]["peer_last_id"] == 3
    assert drift["peerC"]["behind"] == 2


def test_status_includes_peers_behind():
    fed = _fresh_fed()
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS history("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT, url TEXT, "
                   "status TEXT, filename TEXT, file_size INTEGER, ts REAL)")
        for i in range(3):
            cx.execute("INSERT INTO history(site_id,url,status,ts) "
                       "VALUES('s','u','done',0)")
    fed.register_peer("peerD", "http://d.local", last_history_id=1)  # 2 behind
    st = fed.status()
    assert "peers_behind" in st
    assert st["peers_behind"] >= 1


# ---- route surface ----

def _iso_app():
    from flask import Flask
    from bulk_downloader import app_fed as M
    app = Flask(__name__)
    n = M.register_routes(app)
    return app, n


def test_set_trust_route_registered():
    app, n = _iso_app()
    seen = {}
    for r in app.url_map.iter_rules():
        if r.endpoint.startswith("fed."):
            seen.setdefault(r.rule, set()).update(
                m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    assert seen.get("/api/fed/set_trust") == {"POST"}, "set_trust route missing"
    assert n == 12, f"expected 12 fed routes after v3.66.681 (7 + 5 template-federation), got {n}"
