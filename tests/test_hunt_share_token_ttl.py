import sqlite3
import time

from flask import Flask

from bulk_downloader import app_shares, db, shares

BD_GATE_SCOPE = "module"


def test_share_token_fractional_ttl_expires(tmp_path, monkeypatch):
    database = tmp_path / "shares.db"

    def db_conn():
        cx = sqlite3.connect(database)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(db, "db_conn", db_conn)
    monkeypatch.setattr(shares, "_signing_secret", lambda: "test-signing-secret")
    monkeypatch.setattr(app_shares, "_check_csrf", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(app_shares.shares_bp)
    client = app.test_client()
    before = time.time()

    whole = client.post("/api/shares", json={"scopes": ["all_stats"], "ttl_hours": 1})
    assert whole.status_code == 200
    assert before + 3500 < whole.json["expires_at"] < before + 3700

    fractional = client.post("/api/shares", json={"scopes": ["all_stats"], "ttl_hours": 0.5})
    assert fractional.status_code == 200
    assert before + 1700 < fractional.json["expires_at"] < before + 1900

    for permanent in ({}, {"ttl_hours": None}):
        result = client.post("/api/shares", json={"scopes": ["all_stats"], **permanent})
        assert result.status_code == 200
        assert result.json["expires_at"] is None

    with db_conn() as cx:
        original_count = cx.execute("SELECT count(*) FROM share_tokens").fetchone()[0]
    for invalid in (0, -1, "NaN", "inf", "", "bad", True, [1]):
        result = client.post("/api/shares", json={"scopes": ["all_stats"], "ttl_hours": invalid})
        assert result.status_code == 400
    with db_conn() as cx:
        assert cx.execute("SELECT count(*) FROM share_tokens").fetchone()[0] == original_count
