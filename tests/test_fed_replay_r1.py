"""fed-replay r1 (R257-B A2): federation.verify_request HMACs only the body --
no method, path, timestamp or nonce -- so a captured signed request replays
as-is, and a signed announce body replayed to /api/fed/manual_register -> 200.

The fix signs METHOD, path, X-BD-Fed-Ts, X-BD-Fed-Nonce and sha256(body); the
receiver refuses |skew| > 300 s and a nonce already seen inside the window
(bounded per-peer cache). The legacy body-only signature is accepted only
behind fed_legacy_sig (default OFF), never on manual_register / set_trust /
claim_url, and every legacy acceptance is logged.
"""
import hashlib
import hmac
import json
import logging
import time

import pytest

BD_GATE_SCOPE = "module"

FEDTOK = "fed-replay-r1-fixture"


@pytest.fixture
def client(fresh_app, monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    from bulk_downloader import federation as fed
    reset = getattr(fed, "_reset_nonce_cache", lambda: None)
    reset()
    yield fresh_app
    reset()


def _cfg(monkeypatch, **extra):
    from bulk_downloader import global_config as gc
    orig = gc.get_config
    monkeypatch.setattr(gc, "get_config", lambda: {**(orig() or {}), "fed_token": FEDTOK, **extra})


@pytest.fixture
def fedtok(monkeypatch):
    _cfg(monkeypatch)


@pytest.fixture
def fedtok_legacy(monkeypatch):
    _cfg(monkeypatch, fed_legacy_sig=True)


def signed_headers(path, raw, *, ts=None, nonce=None, method="POST"):
    from bulk_downloader import federation as fed
    return fed.sign_request(FEDTOK, method, path, raw, ts=ts, nonce=nonce)


def post(c, path, obj, headers=None):
    raw = json.dumps(obj).encode()
    return c.post(path, data=raw, headers={"Content-Type": "application/json", **(headers or {})})


def legacy_sig(raw):
    return {"X-BD-Fed-Sig": hmac.new(FEDTOK.encode(), raw, hashlib.sha256).hexdigest()}


def peers():
    from bulk_downloader import federation as fed
    return {p["instance_id"]: p for p in fed.active_peers()}


def refused(r):
    return r.status_code == 401 and r.get_json().get("required") == "X-BD-Fed-Sig or master token"


# ── the defect as R257-B Q4 measured it (body-only signature) ────────────
def test_q4_signed_announce_body_replayed_to_manual_register_refused(client, fedtok):
    body = {"instance_id": "q4", "base_url": "http://q4.test"}
    raw = json.dumps(body).encode()
    r = post(client, "/api/fed/manual_register", body, headers=legacy_sig(raw))
    assert refused(r), f"A2 REPLAY: announce body+sig replayed to manual_register -> {r.status_code}"
    assert "q4" not in peers()


def test_identical_body_only_request_replayed_refused(client, fedtok):
    body = {"instance_id": "rp", "base_url": "http://rp.test"}
    raw = json.dumps(body).encode()
    first = post(client, "/api/fed/announce", body, headers=legacy_sig(raw))
    second = post(client, "/api/fed/announce", body, headers=legacy_sig(raw))
    assert refused(first) and refused(second), \
        f"A2 REPLAY: body-only sig accepted with fed_legacy_sig OFF -> {first.status_code}/{second.status_code}"


# ── spec 3: replay / path swap / stale -> 401; fresh -> 200 ──────────────
def test_fresh_signed_request_ctl(client, fedtok):
    body = {"instance_id": "fresh", "base_url": "http://fresh.test"}
    raw = json.dumps(body).encode()
    r = post(client, "/api/fed/manual_register", body, headers=signed_headers("/api/fed/manual_register", raw))
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert "fresh" in peers()


def test_same_request_replayed_refused(client, fedtok):
    from bulk_downloader import federation as fed
    fed.register_peer("rt", "http://rt.test")
    fed.set_peer_trust("rt", "blocked")
    body = {"instance_id": "rt", "tier": "trusted"}
    h = signed_headers("/api/fed/set_trust", json.dumps(body).encode())
    assert post(client, "/api/fed/set_trust", body, headers=h).status_code == 200
    fed.set_peer_trust("rt", "blocked")
    r = post(client, "/api/fed/set_trust", body, headers=h)
    assert refused(r), f"A2 REPLAY: identical signed request accepted twice -> {r.status_code}"
    assert r.get_json()["reason"] == "nonce replayed"
    assert fed.peer_trust("rt") == "blocked"


def test_same_body_different_path_refused(client, fedtok):
    body = {"instance_id": "sw", "base_url": "http://sw.test"}
    raw = json.dumps(body).encode()
    h = signed_headers("/api/fed/announce", raw)
    r = post(client, "/api/fed/manual_register", body, headers=h)
    assert refused(r), f"A2 PATH: announce-signed request accepted on manual_register -> {r.status_code}"
    assert "sw" not in peers()
    # the same headers on the path they were signed for are still good (ctl)
    assert post(client, "/api/fed/announce", body, headers=h).status_code == 200


@pytest.mark.parametrize("skew", [-301, 301, -3600])
def test_stale_or_future_timestamp_refused(client, fedtok, monkeypatch, skew):
    from bulk_downloader import federation as fed
    now = int(time.time())
    monkeypatch.setattr(fed.time, "time", lambda: float(now))  # no second rollover at the edge
    body = {"url": f"http://x.test/s{skew}", "instance_id": "a"}
    raw = json.dumps(body).encode()
    h = signed_headers("/api/fed/claim_url", raw, ts=now + skew)
    r = post(client, "/api/fed/claim_url", body, headers=h)
    assert refused(r) and r.get_json()["reason"] == "timestamp skew", r.get_json()
    assert fed.is_claimed(body["url"]) is None


@pytest.mark.parametrize("skew", [-300, 300, -290])
def test_timestamp_inside_window_ctl(client, fedtok, monkeypatch, skew):
    from bulk_downloader import federation as fed
    now = int(time.time())
    monkeypatch.setattr(fed.time, "time", lambda: float(now))
    body = {"url": f"http://x.test/in{skew}", "instance_id": "a"}
    raw = json.dumps(body).encode()
    h = signed_headers("/api/fed/claim_url", raw, ts=now + skew)
    assert post(client, "/api/fed/claim_url", body, headers=h).status_code == 200


def test_tampered_body_refused(client, fedtok):
    body = {"instance_id": "tb", "base_url": "http://tb.test"}
    h = signed_headers("/api/fed/announce", json.dumps(body).encode())
    r = post(client, "/api/fed/announce", {**body, "base_url": "http://evil.test"}, headers=h)
    assert refused(r)
    assert "tb" not in peers()


# ── spec 2: legacy body-only signature behind fed_legacy_sig ─────────────
def test_legacy_flag_accepts_body_only_on_announce_and_logs(client, fedtok_legacy, caplog):
    body = {"instance_id": "old", "base_url": "http://old.test"}
    raw = json.dumps(body).encode()
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.federation"):
        r = post(client, "/api/fed/announce", body, headers=legacy_sig(raw))
    assert r.status_code == 200 and "old" in peers()
    assert any("fed-replay: LEGACY body-only signature accepted" in m and "/api/fed/announce" in m
               for m in caplog.messages), caplog.messages


@pytest.mark.parametrize("path,body", [
    ("/api/fed/manual_register", {"instance_id": "lg", "base_url": "http://lg.test"}),
    ("/api/fed/set_trust", {"instance_id": "lg", "tier": "trusted"}),
    ("/api/fed/claim_url", {"url": "http://x.test/lg", "instance_id": "lg"}),
])
@pytest.mark.parametrize("flag", [True, False])
def test_legacy_flag_never_opens_write_routes(client, monkeypatch, flag, path, body):
    _cfg(monkeypatch, fed_legacy_sig=flag)
    r = post(client, path, body, headers=legacy_sig(json.dumps(body).encode()))
    assert refused(r), f"spec 2: legacy body-only sig accepted on {path} -> {r.status_code}"
    assert r.get_json()["reason"] == "legacy signature refused"


def test_legacy_flag_default_off(client, fedtok):
    from bulk_downloader import federation as fed
    assert fed.legacy_sig_enabled() is False


# ── unit: verify_signed_request + the bounded nonce cache ────────────────
def _hdr(h):
    return lambda k, d="": h.get(k, d)


def test_unit_verify_signed_request_roundtrip_and_replay(client):
    from bulk_downloader import federation as fed
    h = fed.sign_request("tok", "POST", "/api/fed/announce", b"{}")
    assert fed.verify_signed_request("POST", "/api/fed/announce", b"{}", _hdr(h), "tok") == (True, "ok")
    assert fed.verify_signed_request("POST", "/api/fed/announce", b"{}", _hdr(h), "tok") == (False, "nonce replayed")
    h2 = fed.sign_request("tok", "POST", "/api/fed/announce", b"{}")
    assert fed.verify_signed_request("GET", "/api/fed/announce", b"{}", _hdr(h2), "tok")[0] is False
    assert fed.verify_signed_request("POST", "/api/fed/announce", b"{}", _hdr(h2), "wrong")[0] is False
    assert fed.verify_signed_request("POST", "/api/fed/announce", b"{}", _hdr(h2), "")[0] is False


@pytest.mark.parametrize("ts,nonce", [("12x", "n" * 24), ("", "n" * 24), (None, "short"),
                                      (None, "bad nonce with spaces!!"), (None, "é" * 24)])
def test_unit_malformed_ts_or_nonce_refused_not_raised(client, ts, nonce):
    from bulk_downloader import federation as fed
    h = fed.sign_request("tok", "POST", "/p", b"x", nonce=nonce)
    if ts is not None:
        h["X-BD-Fed-Ts"] = ts
    ok, reason = fed.verify_signed_request("POST", "/p", b"x", _hdr(h), "tok")
    assert ok is False and reason in ("bad timestamp", "bad nonce")


def test_unit_non_ascii_signature_refused_not_raised(client):
    from bulk_downloader import federation as fed
    h = fed.sign_request("tok", "POST", "/p", b"x")
    h["X-BD-Fed-Sig"] = "é" * 64
    assert fed.verify_signed_request("POST", "/p", b"x", _hdr(h), "tok") == (False, "bad signature")


def test_unit_nonce_cache_full_fails_closed_then_expiry_frees(client, monkeypatch):
    from bulk_downloader import federation as fed
    monkeypatch.setattr(fed, "NONCE_CACHE_PER_PEER", 3)
    now = int(time.time())
    for i in range(3):
        h = fed.sign_request("tok", "POST", "/p", b'{"instance_id":"pp"}', ts=now)
        assert fed.verify_signed_request("POST", "/p", b'{"instance_id":"pp"}', _hdr(h), "tok")[0], i
    h = fed.sign_request("tok", "POST", "/p", b'{"instance_id":"pp"}', ts=now)
    assert fed.verify_signed_request("POST", "/p", b'{"instance_id":"pp"}', _hdr(h), "tok") == \
        (False, "nonce cache full")
    # another peer has its own bucket (ctl)
    h = fed.sign_request("tok", "POST", "/p", b'{"instance_id":"qq"}', ts=now)
    assert fed.verify_signed_request("POST", "/p", b'{"instance_id":"qq"}', _hdr(h), "tok")[0]
    # once the stored nonces leave the window they are pruned and the bucket frees
    monkeypatch.setattr(fed.time, "time", lambda: now + fed.FED_MAX_SKEW_SECONDS + 1)
    h = fed.sign_request("tok", "POST", "/p", b'{"instance_id":"pp"}', ts=now + fed.FED_MAX_SKEW_SECONDS + 1)
    assert fed.verify_signed_request("POST", "/p", b'{"instance_id":"pp"}', _hdr(h), "tok")[0]


def test_unit_peer_table_bounded(client, monkeypatch):
    from bulk_downloader import federation as fed
    monkeypatch.setattr(fed, "NONCE_CACHE_PEERS", 2)
    for iid in ("p1", "p2"):
        body = json.dumps({"instance_id": iid}).encode()
        assert fed.verify_signed_request("POST", "/p", body, _hdr(fed.sign_request("tok", "POST", "/p", body)), "tok")[0]
    body = json.dumps({"instance_id": "p3"}).encode()
    assert fed.verify_signed_request("POST", "/p", body, _hdr(fed.sign_request("tok", "POST", "/p", body)), "tok") == \
        (False, "nonce cache full")
