"""fed-auth r1 (federation-mesh FINDINGS F2): with a fed_token configured, every
state-changing peer-registry route -- manual_register, set_trust, claim_url, and
announce / template_push as before -- needs the fed HMAC (X-BD-Fed-Sig over the
raw body) or the master token. Before, only announce / template_push checked it,
so the same register_peer / trust / claim writes were reachable unsigned.

Auth OFF with no fed_token stays open by design (PM-B 1307Z item 1). Each
``*_ctl`` test passes on the base as well.
"""
import hashlib
import hmac
import json

import pytest

BD_GATE_SCOPE = "module"

FEDTOK = "fed-auth-r1-fixture"
MASTER = "fed-auth-r1-master-fixture"


@pytest.fixture
def client(fresh_app, monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    return fresh_app


@pytest.fixture
def fedtok(monkeypatch):
    from bulk_downloader import global_config as gc
    orig = gc.get_config
    monkeypatch.setattr(gc, "get_config", lambda: {**(orig() or {}), "fed_token": FEDTOK})


def send(c, path, obj, *, signed=False, sig=None, headers=None):
    raw = json.dumps(obj).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    if signed:
        # fed-replay r1: the signature binds method, path, timestamp and nonce
        import secrets
        import time
        ts, nonce = str(int(time.time())), secrets.token_hex(16)
        canon = "\n".join(("POST", path, ts, nonce, hashlib.sha256(raw).hexdigest())).encode()
        h.update({"X-BD-Fed-Sig": hmac.new(FEDTOK.encode(), canon, hashlib.sha256).hexdigest(),
                  "X-BD-Fed-Ts": ts, "X-BD-Fed-Nonce": nonce})
    if sig is not None:
        h["X-BD-Fed-Sig"] = sig
    return c.post(path, data=raw, headers=h)


def peers():
    from bulk_downloader import federation as fed
    return {p["instance_id"]: p for p in fed.active_peers()}


def refused(r):
    return r.status_code == 401 and r.get_json().get("required") == "X-BD-Fed-Sig or master token"


# ── P04: manual_register ─────────────────────────────────────────────────
def test_p04_unsigned_manual_register_refused(client, fedtok):
    r = send(client, "/api/fed/manual_register", {"instance_id": "evil", "base_url": "http://evil.test"})
    assert refused(r), f"F2: unsigned manual_register under fed_token -> {r.status_code} {r.get_json()}"
    assert "evil" not in peers()


def test_p04_signed_manual_register_ctl(client, fedtok):
    r = send(client, "/api/fed/manual_register",
             {"instance_id": "good", "base_url": "http://good.test"}, signed=True)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert "good" in peers()


# ── P05: set_trust ───────────────────────────────────────────────────────
def test_p05_unsigned_set_trust_refused(client, fedtok):
    from bulk_downloader import federation as fed
    fed.register_peer("p5", "http://p5.test")
    fed.set_peer_trust("p5", "blocked")
    r = send(client, "/api/fed/set_trust", {"instance_id": "p5", "tier": "trusted"})
    assert refused(r), f"F2: unsigned set_trust under fed_token -> {r.status_code} {r.get_json()}"
    assert fed.peer_trust("p5") == "blocked"


def test_p05_signed_set_trust_ctl(client, fedtok):
    from bulk_downloader import federation as fed
    fed.register_peer("p5s", "http://p5s.test")
    fed.set_peer_trust("p5s", "blocked")
    r = send(client, "/api/fed/set_trust", {"instance_id": "p5s", "tier": "trusted"}, signed=True)
    assert r.status_code == 200 and fed.peer_trust("p5s") == "trusted"


# ── P06: claim_url ───────────────────────────────────────────────────────
def test_p06_unsigned_claim_refused(client, fedtok):
    from bulk_downloader import federation as fed
    r = send(client, "/api/fed/claim_url", {"url": "http://x.test/p6", "instance_id": "nobody"})
    assert refused(r), f"F2: unsigned claim_url under fed_token -> {r.status_code} {r.get_json()}"
    assert fed.is_claimed("http://x.test/p6") is None


def test_p06_signed_claim_ctl(client, fedtok):
    from bulk_downloader import federation as fed
    r = send(client, "/api/fed/claim_url", {"url": "http://x.test/p6s", "instance_id": "a"}, signed=True)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert fed.is_claimed("http://x.test/p6s")["source_instance"] == "a"


def test_wrong_signature_refused(client, fedtok):
    r = send(client, "/api/fed/set_trust", {"instance_id": "x", "tier": "trusted"}, sig="0" * 64)
    assert refused(r)


# ── announce / template_push: unchanged HMAC gate ────────────────────────
def test_announce_signed_ctl_and_unsigned_refused_ctl(client, fedtok):
    body = {"instance_id": "ann", "base_url": "http://ann.test"}
    assert send(client, "/api/fed/announce", body).status_code == 401
    assert "ann" not in peers()
    r = send(client, "/api/fed/announce", body, signed=True)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert "ann" in peers()


def test_no_fed_token_auth_off_stays_open_ctl(client):
    """By design (PM-B 1307Z): auth OFF and no fed_token -> open."""
    r = send(client, "/api/fed/manual_register", {"instance_id": "open", "base_url": "http://open.test"})
    assert r.status_code == 200 and "open" in peers()


# ── master token path (auth ON) ──────────────────────────────────────────
def test_master_bearer_needs_no_hmac(client, fedtok, monkeypatch):
    from bulk_downloader import federation as fed
    monkeypatch.setenv("BD_AUTH_TOKEN", MASTER)
    fed.register_peer("m", "http://m.test")
    r = send(client, "/api/fed/set_trust", {"instance_id": "m", "tier": "trusted"},
             headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code == 200 and fed.peer_trust("m") == "trusted"
    r = send(client, "/api/fed/manual_register", {"instance_id": "m2", "base_url": "http://m2.test"},
             headers={"X-BD-Token": MASTER})
    assert r.status_code == 200 and "m2" in peers()


def test_auth_on_no_credentials_401_ctl(client, fedtok, monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", MASTER)
    r = send(client, "/api/fed/set_trust", {"instance_id": "m", "tier": "trusted"}, signed=True)
    assert r.status_code == 401 and "required" not in (r.get_json() or {})
