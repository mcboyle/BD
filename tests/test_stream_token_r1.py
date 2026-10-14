"""stream-token r1 -- media-serving FINDINGS F2 F3 F11 F12 (S74-D probes p1/p3/p4).

F2  token TTL unbounded (10**9 s -> 31 years) and max_uses promised but ignored.
F3  ttl_seconds "abc"/null/[] -> 500 with the raw exception text instead of 400.
F11 first-secret race: concurrent first mints provisioned one secret each, so
    only the last writer's tokens verified.
F12 tokens minted for non-completed rows; a full (no-Range) response read to EOF
    and sent more bytes than its Content-Length when the file grew.
F1 is a docstring-only change (the token is a second factor): asserted below.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import threading
import time

import pytest
from flask import Flask

from bulk_downloader import app_stream, db, global_config, stream_relay


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(global_config, "_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    monkeypatch.setattr(stream_relay, "_uses", {}, raising=False)  # absent on base
    return tmp_path


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(str(cfg), "st.db"))
    db.db_init()
    monkeypatch.setattr(app_stream, "_check_csrf", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(app_stream.stream_bp)
    return app.test_client()


def _row(status="done"):
    with db.db_conn() as cx:
        return cx.execute("INSERT INTO history(status, filename) VALUES (?, 'f.mp4')",
                          (status,)).lastrowid


# ── F2: TTL cap ──────────────────────────────────────────────────────

def test_f2_ttl_is_capped_at_24h(client):
    hid = _row()
    r = client.post(f"/api/stream/token/{hid}", json={"ttl_seconds": 10 ** 9})
    assert r.status_code == 200, r.get_json()
    v = stream_relay.verify_token(r.get_json()["token"])
    assert v["ok"] is True, v
    left = v["expires"] - time.time()
    assert left <= 24 * 3600 + 5, f"stream-token F2: token lives {left / 86400:.1f} days"


def test_f2_control_short_ttl_kept(client):
    hid = _row()
    tok = client.post(f"/api/stream/token/{hid}", json={"ttl_seconds": 60}).get_json()["token"]
    left = stream_relay.verify_token(tok)["expires"] - time.time()
    assert 50 <= left <= 61, left


# ── F2: max_uses honoured ────────────────────────────────────────────

def test_f2_max_uses_one_refuses_second_use(client):
    hid = _row()
    tok = client.post(f"/api/stream/token/{hid}", json={"max_uses": 1}).get_json()["token"]
    first = stream_relay.verify_token(tok)
    second = stream_relay.verify_token(tok)
    assert first["ok"] is True, first
    assert second["ok"] is False and second["error"] == "use limit reached", \
        f"stream-token F2: same max_uses=1 token accepted twice: {second}"


def test_f2_max_uses_is_signed(cfg):
    tok = stream_relay.generate_token(7, ttl_seconds=60, max_uses=1)
    v, hid, exp, uses, sig = tok.split(".")
    forged = ".".join((v, hid, exp, "999", sig))
    assert stream_relay.verify_token(forged)["error"] == "bad signature"


def test_f2_control_default_token_serves_repeated_requests(client):
    # A player re-requests the same URL on every seek; the default token
    # (no max_uses) must keep verifying.
    hid = _row()
    tok = client.post(f"/api/stream/token/{hid}", json={}).get_json()["token"]
    assert all(stream_relay.verify_token(tok)["ok"] for _ in range(5))


# ── F3: malformed ttl_seconds / max_uses -> 400 ───────────────────────

@pytest.mark.parametrize("bad", ["abc", None, [], 1e30, True, 0, -100, "3600"])
def test_f3_bad_ttl_is_400_without_exception_text(client, bad):
    hid = _row()
    r = client.post(f"/api/stream/token/{hid}", json={"ttl_seconds": bad})
    body = r.get_json()
    assert r.status_code == 400, f"stream-token F3: ttl {bad!r} -> {r.status_code} {body}"
    assert body == {"ok": False, "error": "ttl_seconds must be a positive integer"}


@pytest.mark.parametrize("bad", ["x", 0, -1, 1.5, False])
def test_f3_bad_max_uses_is_400(client, bad):
    hid = _row()
    r = client.post(f"/api/stream/token/{hid}", json={"max_uses": bad})
    assert r.status_code == 400 and "token" not in r.get_json(), r.get_json()


# ── F11: concurrent first mints share one secret ─────────────────────

def test_f11_concurrent_first_mints_all_verify(cfg, monkeypatch):
    assert not (global_config.get_config() or {}).get("stream_token_secret")
    real_set = global_config.set_config

    def slow_set(updates):  # widen the read-then-write window the race needs
        time.sleep(0.05)
        return real_set(updates)

    monkeypatch.setattr(global_config, "set_config", slow_set)
    barrier = threading.Barrier(8)
    toks = []

    def mint():
        barrier.wait()
        toks.append(stream_relay.generate_token(1, ttl_seconds=60))

    ths = [threading.Thread(target=mint) for _ in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    ok = sum(1 for t in toks if stream_relay.verify_token(t)["ok"])
    assert len(toks) == 8 and ok == 8, f"stream-token F11: {ok}/{len(toks)} first-mint tokens verify"


# ── F12: status gate on mint ─────────────────────────────────────────

@pytest.mark.parametrize("status", ["failed", "paused", "running", "pending", "needs_review"])
def test_f12_no_token_for_non_completed_row(client, status):
    hid = _row(status)
    r = client.post(f"/api/stream/token/{hid}", json={})
    assert r.status_code == 409 and "token" not in r.get_json(), \
        f"stream-token F12: token minted for a {status} row: {r.get_json()}"


def test_f12_control_done_row_mints(client):
    hid = _row("done")
    body = client.post(f"/api/stream/token/{hid}", json={}).get_json()
    assert body["ok"] is True and body["token"].startswith(f"v1.{hid}."), body


# ── F12: full response bounded to its Content-Length ─────────────────

def _drain(gen, after_first=None):
    out = b""
    for i, chunk in enumerate(gen):
        out += chunk
        if i == 0 and after_first:
            after_first()
    return out


def test_f12_growing_file_body_matches_content_length(tmp_path):
    p = tmp_path / "grow.mp4"
    p.write_bytes(b"a" * 100)
    gen, headers, status = stream_relay.serve_range(str(p), "", chunk_size=10)

    def grow():
        with open(p, "ab") as f:
            f.write(b"b" * 900)

    body = _drain(gen, grow)
    assert status == 200 and headers["Content-Length"] == "100"
    assert len(body) == 100, f"stream-token F12: sent {len(body)} B under Content-Length 100"


def test_f12_control_range_already_bounded(tmp_path):
    p = tmp_path / "grow.mp4"
    p.write_bytes(b"a" * 1000)
    gen, headers, status = stream_relay.serve_range(str(p), "bytes=0-99")
    assert status == 206 and headers["Content-Length"] == "100" and len(_drain(gen)) == 100


# ── F1 + F2 docstrings match the behaviour ───────────────────────────

def test_docstrings_state_second_factor_and_persisted_secret():
    doc = stream_relay.__doc__ + (stream_relay._get_secret.__doc__ or "")
    assert "second factor" in doc
    assert "in-memory only; tokens reset on restart" not in doc
    assert "rotate_secret" in doc and "persisted" in doc
