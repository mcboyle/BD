"""RED-first tests for Cut 625 / C7 sub-wave 11.1a: accounts + roles engine.

A self-contained user store (``accounts.json``) with PBKDF2 password hashing,
roles (admin > reviewer > operator), and signed session tokens for a parallel
``bd_user`` identity cookie -- deliberately NOT entangled with the existing
``bd_session`` pairing cookie in app.py's auth hot path.

Default-OFF / backward-compatible: ``multi_user_enabled`` defaults False, so with
zero accounts the single-operator experience is byte-identical; role gating only
activates when the operator creates accounts AND enables it in app_config.json.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp, base_dir params, no
monkeypatch. All read paths never raise; passwords are never stored or returned
in the clear.
"""
from __future__ import annotations

import json
import os
import tempfile


def _base():
    return tempfile.mkdtemp(prefix="bdacct_")


# ── create / verify ─────────────────────────────────────────────────────

def test_create_then_verify_password():
    from bulk_downloader import user_accounts as A
    base = _base()
    ok, _ = A.create_user("matt", "s3cret-pass", role="admin", base_dir=base)
    assert ok
    assert A.verify_password("matt", "s3cret-pass", base_dir=base) is True
    assert A.verify_password("matt", "wrong", base_dir=base) is False
    assert A.verify_password("nobody", "x", base_dir=base) is False


def test_create_rejects_dupe_bad_name_empty_pw_bad_role():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("u", "pw123456", base_dir=base)
    dupe, _ = A.create_user("u", "pw123456", base_dir=base)
    badname, _ = A.create_user("has space", "pw123456", base_dir=base)
    emptypw, _ = A.create_user("v", "", base_dir=base)
    badrole, _ = A.create_user("w", "pw123456", role="wizard", base_dir=base)
    assert not dupe and not badname and not emptypw and not badrole


def test_password_is_hashed_not_plaintext():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("u", "plaintextpw", base_dir=base)
    raw = json.load(open(os.path.join(base, "user_accounts.json")))
    rec = raw["users"]["u"]
    assert "plaintextpw" not in json.dumps(rec)   # never stored in clear
    assert rec.get("salt") and rec.get("pw_hash") and rec.get("iters", 0) >= 100000


# ── get / list / roles ──────────────────────────────────────────────────

def test_get_user_hides_hash_and_list():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("a", "pw123456", role="reviewer", base_dir=base)
    A.create_user("b", "pw123456", role="operator", base_dir=base)
    u = A.get_user("a", base_dir=base)
    assert u["role"] == "reviewer" and "pw_hash" not in u and "salt" not in u
    assert {x["username"] for x in A.list_users(base_dir=base)} == {"a", "b"}


def test_roles_and_can_promote():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("adm", "pw123456", role="admin", base_dir=base)
    A.create_user("rev", "pw123456", role="reviewer", base_dir=base)
    A.create_user("op", "pw123456", role="operator", base_dir=base)
    assert A.user_can_promote("adm", base_dir=base) is True
    assert A.user_can_promote("rev", base_dir=base) is True
    assert A.user_can_promote("op", base_dir=base) is False
    assert A.user_can_promote("ghost", base_dir=base) is False


def test_set_role_set_password_delete_count():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("u", "oldpw123", role="operator", base_dir=base)
    assert A.count(base_dir=base) == 1
    ok, _ = A.set_role("u", "reviewer", base_dir=base)
    assert ok and A.user_can_promote("u", base_dir=base) is True
    ok2, _ = A.set_password("u", "newpw456", base_dir=base)
    assert ok2 and A.verify_password("u", "newpw456", base_dir=base) is True
    assert A.verify_password("u", "oldpw123", base_dir=base) is False
    assert A.delete_user("u", base_dir=base) is True
    assert A.count(base_dir=base) == 0


# ── signed sessions ─────────────────────────────────────────────────────

def test_session_issue_and_verify_roundtrip():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("matt", "pw123456", role="reviewer", base_dir=base)
    tok = A.issue_session("matt", base_dir=base)
    who = A.verify_session(tok, base_dir=base)
    assert who and who["username"] == "matt" and who["role"] == "reviewer"


def test_session_tampered_or_expired_or_deleted_is_none():
    from bulk_downloader import user_accounts as A
    base = _base()
    A.create_user("matt", "pw123456", base_dir=base)
    tok = A.issue_session("matt", base_dir=base)
    # tamper
    assert A.verify_session(tok + "x", base_dir=base) is None
    assert A.verify_session("garbage.token.here", base_dir=base) is None
    # expired (ttl 0)
    exp = A.issue_session("matt", ttl_seconds=-1, base_dir=base)
    assert A.verify_session(exp, base_dir=base) is None
    # deleted user
    A.delete_user("matt", base_dir=base)
    assert A.verify_session(tok, base_dir=base) is None


# ── enablement flag + empty-store safety ────────────────────────────────

def test_multi_user_defaults_off_and_reads_config():
    from bulk_downloader import user_accounts as A
    base = _base()
    assert A.multi_user_enabled(base_dir=base) is False   # no config -> off
    with open(os.path.join(base, "app_config.json"), "w") as fh:
        json.dump({"multi_user": {"enabled": True}}, fh)
    assert A.multi_user_enabled(base_dir=base) is True


def test_reads_never_raise_on_empty_store():
    from bulk_downloader import user_accounts as A
    base = _base()
    assert A.count(base_dir=base) == 0
    assert A.list_users(base_dir=base) == []
    assert A.get_user("x", base_dir=base) is None
    assert A.verify_password("x", "y", base_dir=base) is False
    assert A.verify_session("t", base_dir=base) is None
    assert A.user_can_promote("x", base_dir=base) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for fn in fns:
        try:
            fn(); p += 1; print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            f += 1; print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p} passed / {f} failed")
    raise SystemExit(1 if f else 0)
