"""Bug-hunt das-high (deep-api-auth-csrf sweep, the two HIGH findings).

F1 a corrupt or unreadable user_accounts.json read as "no accounts", so
   POST /api/auth/users (first-admin bootstrap) let an unauthenticated caller
   create an admin and replace the file.
F2 a share token created with scopes that are not a list ("all_stats", a dict
   with an "all" key) passed the super-scope check, because `"all" in scopes`
   is a substring / key test on those types.

Scratch directory + scratch SQLite file per test. No network.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

import pytest
from flask import Flask

from bulk_downloader import app_auth, app_shares, db, shares
from bulk_downloader import user_accounts as A

BD_GATE_SCOPE = "module"

EVERY_SCOPE = sorted({scope for _prefix, scope in shares.SHARE_ROUTES})


# ── F1: corrupt accounts store never reopens the bootstrap ────────────

@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                       # the store lives in cwd
    monkeypatch.setattr(A, "_refusals_logged", set(), raising=False)
    app = Flask(__name__)
    app.register_blueprint(app_auth.auth_bp)
    return app.test_client()


def _post_user(client, name):
    return client.post("/api/auth/users", json={
        "username": name, "password": "password123", "role": "admin"})


CORRUPT = [b"{corrupt json", b"", b"[]", b"null", b'{"users": []}', b"\xff\xfe"]


@pytest.mark.parametrize("raw", CORRUPT)
def test_f1_corrupt_store_refuses_unauthenticated_admin_create(auth_client, raw):
    assert _post_user(auth_client, "admin1").status_code == 200       # real first run
    auth_client.delete_cookie("bd_user")
    with open(A.ACCOUNTS_FILE, "wb") as fh:
        fh.write(raw)
    reply = _post_user(auth_client, "attacker")
    assert reply.status_code != 200, reply.get_data(as_text=True)
    assert reply.status_code >= 400
    with open(A.ACCOUNTS_FILE, "rb") as fh:
        assert fh.read() == raw                       # not replaced by the attacker's store
    login = auth_client.post("/api/auth/login", json={
        "username": "attacker", "password": "password123"})
    assert login.status_code != 200


def test_f1_unreadable_store_refuses_unauthenticated_admin_create(auth_client):
    assert _post_user(auth_client, "admin1").status_code == 200
    os.remove(A.ACCOUNTS_FILE)
    os.mkdir(A.ACCOUNTS_FILE)                         # open() -> IsADirectoryError
    assert _post_user(auth_client, "attacker").status_code >= 400
    assert os.path.isdir(A.ACCOUNTS_FILE)


def test_f1_bootstrap_still_opens_on_a_true_first_run(auth_client):
    assert not os.path.exists(A.ACCOUNTS_FILE)
    reply = _post_user(auth_client, "admin1")
    assert reply.status_code == 200 and reply.json["user"]["role"] == "admin"
    # and closes again once an account exists
    auth_client.delete_cookie("bd_user")
    assert _post_user(auth_client, "second").status_code == 403


def test_f1_intact_store_keeps_working_beside_the_refusal(auth_client):
    assert _post_user(auth_client, "admin1").status_code == 200
    login = auth_client.post("/api/auth/login", json={
        "username": "admin1", "password": "password123"})
    assert login.status_code == 200
    assert _post_user(auth_client, "second").status_code == 200       # admin cookie


def test_f1_refusal_is_logged_once_per_reason(auth_client, caplog):
    _post_user(auth_client, "admin1")
    with open(A.ACCOUNTS_FILE, "w") as fh:
        fh.write("{corrupt json")
    with caplog.at_level(logging.ERROR, logger=A.__name__):
        for _ in range(3):
            with pytest.raises(A.AccountsStoreError):
                A.count()
    lines = [r.getMessage() for r in caplog.records if r.name == A.__name__]
    assert len(lines) == 1
    assert "auth fails closed" in lines[0] and A.ACCOUNTS_FILE in lines[0]


# ── F2: share-token scopes are a list of known names ──────────────────

@pytest.fixture
def share_db(tmp_path, monkeypatch):
    database = tmp_path / "shares.db"

    def db_conn():
        cx = sqlite3.connect(database)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(db, "db_conn", db_conn)
    monkeypatch.setattr(shares, "_signing_secret", lambda: "test-signing-secret")
    monkeypatch.setattr(app_shares, "_check_csrf", lambda: None)
    return db_conn


@pytest.fixture
def share_client(share_db):
    app = Flask(__name__)
    app.register_blueprint(app_shares.shares_bp)
    return app.test_client()


def _granted(token):
    """The scopes a token is actually granted, asked through the middleware helper."""
    out = []
    for prefix, scope in shares.SHARE_ROUTES:
        if shares.check_share_access(token, prefix, "GET", client_ip="10.0.0.1")["ok"]:
            out.append(scope)
    return sorted(set(out))


BAD_SCOPES = ["all_stats", "small", "all", {"all": False}, {"all_stats": 1},
              ["all_stats", "nope"], ["all_stats", 5], [["all"]], [None],
              ["site_view:x"], 7, True, ["ALL"], [" all"]]


@pytest.mark.parametrize("bad", BAD_SCOPES, ids=repr)
def test_f2_create_route_refuses_scopes_that_are_not_known_names(share_client, share_db, bad):
    reply = share_client.post("/api/shares", json={"scopes": bad, "label": "x"})
    assert reply.status_code == 400, reply.get_data(as_text=True)
    assert reply.json["ok"] is False and "token" not in reply.json
    with share_db() as cx:
        shares._ensure_tables()
        assert cx.execute("SELECT count(*) FROM share_tokens").fetchone()[0] == 0


@pytest.mark.parametrize("bad", BAD_SCOPES, ids=repr)
def test_f2_create_token_itself_refuses_them(share_db, bad):
    res = shares.create_token(scopes=bad)
    assert res["ok"] is False and "token" not in res


@pytest.mark.parametrize("empty", [None, [], "", {}, 0])
def test_f2_empty_scopes_are_refused(share_client, share_db, empty):
    assert share_client.post("/api/shares", json={"scopes": empty}).status_code == 400
    assert share_client.post("/api/shares", json={}).status_code == 400
    assert shares.create_token(scopes=empty) == {"ok": False, "error": "scopes required"}


def _plant(share_db, stored_text):
    """A token row as an older build could have written it: mint a good token,
    then put ``stored_text`` in its scopes column."""
    res = shares.create_token(scopes=["metrics"], label="planted")
    assert res["ok"]
    with share_db() as cx:
        cx.execute("UPDATE share_tokens SET scopes = ? WHERE token_id = ?",
                   (stored_text, res["token_id"]))
    return res


STORED_NON_LIST = ['"all_stats"', '"small"', '"all"', '{"all": false}', '{"all_stats": 1}',
                   '5', 'true', 'null', '["all", 5]', '[["all"]]', 'not json', '"metrics"']


@pytest.mark.parametrize("stored", STORED_NON_LIST)
def test_f2_existing_token_with_non_list_scopes_grants_nothing_and_is_listed(share_db, stored):
    res = _plant(share_db, stored)
    assert _granted(res["token"]) == []
    for scope in EVERY_SCOPE + ["all"]:
        out = shares.verify_token(res["token"], required_scope=scope)
        assert out["ok"] is False
    row = [t for t in shares.list_tokens() if t["token_id"] == res["token_id"]][0]
    assert row["scopes_invalid"] is True and row["scopes"] == []
    with share_db() as cx:
        used = cx.execute("SELECT use_count FROM share_tokens WHERE token_id = ?",
                          (res["token_id"],)).fetchone()[0]
        reasons = {r[0] for r in cx.execute(
            "SELECT reason FROM share_access_log WHERE token_id = ? AND allowed = 0",
            (res["token_id"],))}
    assert not used
    assert reasons == {"invalid stored scopes"}


def test_f2_list_scopes_grant_exactly_what_they_name(share_client, share_db):
    one = share_client.post("/api/shares", json={"scopes": ["all_stats"]})
    assert one.status_code == 200 and one.json["scopes"] == ["all_stats"]
    assert _granted(one.json["token"]) == ["all_stats"]
    two = shares.create_token(scopes=["recent_events", "metrics"])
    assert _granted(two["token"]) == ["metrics", "recent_events"]
    every = shares.create_token(scopes=["all"])
    assert _granted(every["token"]) == EVERY_SCOPE
    rows = {t["token_id"]: t for t in shares.list_tokens()}
    assert rows[two["token_id"]]["scopes"] == ["recent_events", "metrics"]
    assert all(t["scopes_invalid"] is False for t in rows.values())


def test_f2_every_known_scope_can_still_be_granted(share_db):
    for name in shares.KNOWN_SCOPES:
        assert shares.create_token(scopes=[name])["ok"] is True
    assert shares.scope_error(list(shares.KNOWN_SCOPES)) == ""


def test_f2_stored_json_is_a_list(share_db):
    res = shares.create_token(scopes=["all_stats", "metrics"])
    with share_db() as cx:
        raw = cx.execute("SELECT scopes FROM share_tokens WHERE token_id = ?",
                         (res["token_id"],)).fetchone()[0]
    assert json.loads(raw) == ["all_stats", "metrics"]
