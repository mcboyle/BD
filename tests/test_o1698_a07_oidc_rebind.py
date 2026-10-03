"""O1698 a07-oidc-rebind (operator O1717 item 3): the migration that rides one train ahead of O1671 a07.

A successful OIDC login reuses an existing BD user only when that user is bound to the login's exact (iss, sub)
(accounts.json users[name]["oidc"] = {"iss", "sub"}, a07's schema). A user with no binding, or a legacy/partial one, is
bound to the caller under the accounts store lock and logged at WARN -- admins too (PM ruling O1720 (a)) -- but only
when the login's iss is the configured issuer. A user bound to any other (iss, sub), or an unbound one under another
issuer, is refused and the store is left byte-unchanged. New users are bound at create.

Hermetic: a tmp accounts store (cwd), no IdP. The store is seeded and read as raw JSON so the hijack/legacy cases fail
on main by behaviour (no refusal / no binding), not by a missing helper.
"""
import hashlib
import json
import logging
import os
import threading
import time

import pytest

import bulk_downloader.oidc as oidc
import bulk_downloader.user_accounts as ua

BD_GATE_SCOPE = "module"

ISS = "https://idp.example.test"
STORE = ua.ACCOUNTS_FILE
REFUSED = "OIDC_ACCOUNT_BINDING_REFUSED"


def _rec(role="operator", oidc_rec=None):
    rec = {"pw_hash": "00", "salt": "00", "iters": 1, "role": role, "created_ts": 1}
    if oidc_rec is not None:
        rec["oidc"] = oidc_rec
    return rec


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A tmp accounts store in cwd, oidc_issuer pinned to ISS; returns a seeder that writes raw users -> store path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(oidc, "oidc_config", lambda: {"enabled": True, "issuer": ISS, "client_id": "bd"})
    path = tmp_path / STORE

    def seed(**users):
        path.write_text(json.dumps({"users": users, "signing_key": "ab" * 32}, indent=1))
        return path
    return seed


def _users(path):
    return json.loads(path.read_text())["users"]


def _snap(path):
    return path.read_bytes(), os.stat(path).st_mtime_ns


def _login(name, sub, iss=ISS):
    return oidc.provision_user({"iss": iss, "sub": sub, "preferred_username": name})


# ---- hijack: a bound user is reused only by its exact (iss, sub) -----------------------------------------------------
@pytest.mark.parametrize("iss,sub", [(ISS, "sub-eve"), ("https://other-idp.example.test", "sub-bob"),
                                     (ISS, " sub-bob"), (ISS, "sub-bob "), (ISS, "SUB-BOB")],
                         ids=["other-sub", "other-iss", "lead-space-sub", "trail-space-sub", "case-sub"])
def test_bound_user_other_subject_is_refused_store_unchanged(store, iss, sub):
    path = store(bob=_rec(oidc_rec={"iss": ISS, "sub": "sub-bob"}))
    before = _snap(path)
    with pytest.raises(ValueError, match=REFUSED):
        _login("bob", sub, iss)
    assert _snap(path) == before


def test_bound_user_exact_subject_is_reused_without_a_write(store):
    path = store(bob=_rec(oidc_rec={"iss": ISS, "sub": "sub-bob"}))
    before = _snap(path)
    assert _login("bob", "sub-bob") == "bob"
    assert _login("bob", "sub-bob", ISS + "/") == "bob"      # iss compared as verify_id_token accepted it
    assert _snap(path) == before


# ---- legacy rebind: no / partial binding -> bound to the caller, once ------------------------------------------------
LEGACY = {"none": None, "iss-only": {"iss": ISS}, "sub-only": {"sub": "sub-old"}, "empty": {"iss": "", "sub": ""},
          "non-str-sub": {"iss": ISS, "sub": 123}, "non-dict": "sub-old"}


@pytest.mark.parametrize("legacy", list(LEGACY.values()), ids=list(LEGACY))
def test_legacy_user_is_rebound_to_the_caller_and_audited(store, caplog, legacy):
    path = store(carol=_rec(oidc_rec=legacy), dave=_rec(oidc_rec={"iss": ISS, "sub": "sub-dave"}))
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.oidc"):
        assert _login("carol", "sub-carol", ISS + "/") == "carol"
    users = _users(path)
    assert users["carol"]["oidc"] == {"iss": ISS, "sub": "sub-carol"}
    assert users["carol"]["role"] == "operator" and users["carol"]["pw_hash"] == "00"
    assert users["dave"] == _rec(oidc_rec={"iss": ISS, "sub": "sub-dave"})
    audit = [r.getMessage() for r in caplog.records if "OIDC_ACCOUNT_REBOUND" in r.getMessage()]
    assert len(audit) == 1 and "'carol'" in audit[0]
    assert hashlib.sha256(b"sub-carol").hexdigest()[:16] in audit[0] and "sub-carol" not in audit[0]
    # the binding now holds: a second subject is refused, the same one is reused with no write
    before = _snap(path)
    with pytest.raises(ValueError, match=REFUSED):
        _login("carol", "sub-eve")
    assert _login("carol", "sub-carol") == "carol"
    assert _snap(path) == before


# ---- idempotence + new users ----------------------------------------------------------------------------------------
def test_new_user_is_bound_at_create_and_relogin_is_byte_equal(store, caplog):
    path = store()
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.oidc"):
        assert _login("erin", "sub-erin") == "erin"
    assert _users(path)["erin"]["oidc"] == {"iss": ISS, "sub": "sub-erin"}
    assert _users(path)["erin"]["role"] == "operator"
    assert not any("OIDC_ACCOUNT_REBOUND" in r.getMessage() for r in caplog.records)   # create is not a rebind
    before = _snap(path)
    time.sleep(0.01)
    assert _login("erin", "sub-erin") == "erin"
    assert _snap(path) == before
    with pytest.raises(ValueError, match=REFUSED):
        _login("erin", "sub-mallory")
    assert _snap(path) == before


# ---- admin rebinds like every user, under the pinned issuer only (O1720 (a)) ------------------------------------------
@pytest.mark.parametrize("legacy", [None, {"iss": ISS}], ids=["no-binding", "partial"])
def test_unbound_admin_under_pinned_issuer_binds_and_logs(store, caplog, legacy):
    path = store(admin=_rec(role="admin", oidc_rec=legacy))
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.oidc"):
        assert _login("admin", "sub-admin") == "admin"
    rec = _users(path)["admin"]
    assert rec["oidc"] == {"iss": ISS, "sub": "sub-admin"} and rec["role"] == "admin"
    audit = [r for r in caplog.records if "OIDC_ACCOUNT_REBOUND" in r.getMessage()]
    assert len(audit) == 1 and audit[0].levelno == logging.WARNING
    assert "'admin'" in audit[0].getMessage() and repr(ISS) in audit[0].getMessage()
    assert hashlib.sha256(b"sub-admin").hexdigest()[:16] in audit[0].getMessage()


@pytest.mark.parametrize("role", ["admin", "operator"])
@pytest.mark.parametrize("iss", ["https://other-idp.example.test", ISS + "/evil"], ids=["other-idp", "path-suffix"])
def test_unbound_user_under_another_issuer_is_refused_store_unchanged(store, role, iss):
    path = store(ivy=_rec(role=role))
    before = _snap(path)
    with pytest.raises(ValueError, match=REFUSED + ".*configured OIDC issuer"):
        _login("ivy", "sub-ivy", iss)
    assert _snap(path) == before


def test_no_configured_issuer_rebinds_nobody(store, monkeypatch):
    path = store(ivy=_rec())
    monkeypatch.setattr(oidc, "oidc_config", lambda: {"enabled": False, "issuer": "", "client_id": ""})
    before = _snap(path)
    with pytest.raises(ValueError, match=REFUSED):
        _login("ivy", "sub-ivy")
    assert _snap(path) == before


def test_bound_admin_exact_subject_is_reused(store):
    path = store(admin=_rec(role="admin", oidc_rec={"iss": ISS, "sub": "sub-admin"}))
    before = _snap(path)
    assert _login("admin", "sub-admin") == "admin"
    assert _snap(path) == before


# ---- race: two first logins, two subjects -> exactly one binds ------------------------------------------------------
def _race(monkeypatch, name, subs):
    """Run one login per sub at once; widen the load->save window so an unlocked read-check-write loses."""
    real_load = ua._load

    def slow_load(*a, **k):
        doc = real_load(*a, **k)
        time.sleep(0.2)
        return doc
    monkeypatch.setattr(ua, "_load", slow_load)
    gate = threading.Barrier(len(subs))
    results = {}

    def run(sub):
        gate.wait()
        try:
            results[sub] = _login(name, sub)
        except ValueError as exc:
            results[sub] = exc
    threads = [threading.Thread(target=run, args=(s,)) for s in subs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


@pytest.mark.parametrize("seeded", [True, False], ids=["legacy-user", "new-user"])
def test_concurrent_first_logins_bind_exactly_one(store, monkeypatch, seeded):
    path = store(frank=_rec()) if seeded else store()
    subs = ("sub-f1", "sub-f2")
    results = _race(monkeypatch, "frank", subs)
    won = [s for s in subs if results.get(s) == "frank"]
    lost = [s for s in subs if isinstance(results.get(s), ValueError) and REFUSED in str(results[s])]
    assert len(won) == 1 and len(lost) == 1, results
    assert _users(path)["frank"]["oidc"] == {"iss": ISS, "sub": won[0]}


_OTHER_PROCESS = r"""
import fcntl, json, sys, time
store = sys.argv[1]
with open(store + ".lock", "a+b") as fh:
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(0.3)
    doc = json.load(open(store))
    doc["users"]["hank"]["oidc"] = {"iss": sys.argv[2], "sub": "sub-other-process"}
    json.dump(doc, open(store, "w"))
"""


@pytest.mark.skipif(os.name == "nt", reason="flock: POSIX only")
def test_other_process_holding_the_store_lock_binds_first(store):
    """The flock half of the lock: another app process mid-bind is waited for, then its binding refuses this login."""
    import subprocess
    import sys
    path = store(hank=_rec())
    child = subprocess.Popen([sys.executable, "-c", _OTHER_PROCESS, str(path), ISS], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(ValueError, match=REFUSED):
            _login("hank", "sub-this-process")
    finally:
        child.wait(10)
    assert _users(path)["hank"]["oidc"] == {"iss": ISS, "sub": "sub-other-process"}


def test_unlockable_store_refuses_every_write_and_never_raises_from_reads(store, tmp_path):
    """Fail closed: when the lock can't be taken nothing is written unlocked; issue_session still never raises."""
    path = store(jo=_rec())
    (tmp_path / (STORE + ".lock")).mkdir()          # open(..., "a+b") -> IsADirectoryError
    before = _snap(path)
    with pytest.raises(ValueError, match="could not lock accounts store"):
        _login("jo", "sub-jo")
    with pytest.raises(ValueError, match="could not provision 'kim'.*could not lock"):
        _login("kim", "sub-kim")
    assert ua.set_password("jo", "pw") == (False, "could not lock accounts store")
    assert ua.set_role("jo", "admin") == (False, "could not lock accounts store")
    assert ua.delete_user("jo") is False
    assert _snap(path) == before
    path.write_text(json.dumps({"users": {}}))   # no signing key yet: issue_session must still return a token
    assert isinstance(ua.issue_session("jo"), str)


# ---- every writer holds the lock: a concurrent writer cannot drop a fresh binding ------------------------------------
def test_set_password_racing_a_rebind_keeps_the_binding(store, monkeypatch):
    path = store(gina=_rec())
    real_load = ua._load

    def slow_load(*a, **k):
        doc = real_load(*a, **k)
        if threading.current_thread().name == "setpw":   # unlocked, its stale doc would land after the rebind
            time.sleep(0.1)
        return doc
    monkeypatch.setattr(ua, "_load", slow_load)
    monkeypatch.setattr(ua, "_hash_password", lambda pw, salt, iters: "11")
    gate = threading.Barrier(2)
    out = {}

    def rebind():
        gate.wait()
        out["login"] = _login("gina", "sub-gina")

    def setpw():
        gate.wait()
        out["pw"] = ua.set_password("gina", "new-pw")
    threads = [threading.Thread(target=rebind), threading.Thread(target=setpw, name="setpw")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert out == {"login": "gina", "pw": (True, "updated")}
    rec = _users(path)["gina"]
    assert rec["oidc"] == {"iss": ISS, "sub": "sub-gina"} and rec["pw_hash"] == "11"


# ---- the callback: a refused login is sso_error with no session ------------------------------------------------------
def test_callback_hijack_is_sso_error_and_no_cookie(store, monkeypatch):
    import secrets
    from bulk_downloader import app as a
    path = store(bob=_rec(oidc_rec={"iss": ISS, "sub": "sub-bob"}))
    before = _snap(path)
    monkeypatch.setattr(oidc, "exchange_code", lambda code: {"id_token": "fixture"})
    monkeypatch.setattr(oidc, "verify_id_token",
                        lambda tok, nonce=None: {"iss": ISS, "sub": "sub-eve", "preferred_username": "bob"})
    monkeypatch.setattr(a.app, "secret_key", secrets.token_hex(16))
    client = a.app.test_client()
    with client.session_transaction() as s:
        s["oidc_state"] = "st-1"
        s["oidc_nonce"] = "n-1"
    r = client.get("/api/auth/oidc/callback?code=c-1&state=st-1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/?sso_error=exchange"), r.headers.get("Location")
    assert "bd_user" not in r.headers.get("Set-Cookie", "")
    assert _snap(path) == before
