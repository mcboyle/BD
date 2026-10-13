"""Bug-hunt dva-accounts (deep-vpn-admin sweep, user_accounts.py rows).

U14 a password reset left sessions issued under the old password valid.
U15 deleting a user and re-creating the name revived the old cookie.
U16 a corrupt store loaded as an EMPTY store: count() == 0 reopens the
    first-admin bootstrap and the next save wipes every other account.
U19 verify_password answered for an unknown user without doing a PBKDF2.
U21 the store's .tmp was created with the process umask (chmod came after
    the replace), so hashes and the signing key were briefly world-readable.
U10 a non-object user record made set_role / set_password raise TypeError.

Module-level tests on a temp directory. No app, no network.
"""
from __future__ import annotations

import json
import os
import stat
import time

import pytest

from bulk_downloader import user_accounts as A

BD_GATE_SCOPE = "module"


@pytest.fixture
def base(tmp_path):
    return str(tmp_path)


def _store(base):
    return os.path.join(base, A.ACCOUNTS_FILE)


def _doc(base):
    with open(_store(base)) as fh:
        return json.load(fh)


# ── U14 / U15: sessions are bound to the user's current stamp ─────────

def test_u14_password_reset_ends_old_sessions(base):
    assert A.create_user("bob", "pw1", base_dir=base)[0]
    old = A.issue_session("bob", base_dir=base)
    assert A.verify_session(old, base_dir=base) == {"username": "bob", "role": "operator"}
    assert A.set_password("bob", "pw2", base_dir=base) == (True, "updated")
    assert A.verify_session(old, base_dir=base) is None
    new = A.issue_session("bob", base_dir=base)
    assert A.verify_session(new, base_dir=base)["username"] == "bob"


def test_u14_reset_of_one_user_leaves_other_sessions_alone(base):
    A.create_user("bob", "pw", base_dir=base)
    A.create_user("amy", "pw", role="admin", base_dir=base)
    amy = A.issue_session("amy", base_dir=base)
    A.set_password("bob", "pw2", base_dir=base)
    assert A.verify_session(amy, base_dir=base) == {"username": "amy", "role": "admin"}


def test_u15_delete_and_recreate_does_not_revive_the_cookie(base):
    A.create_user("bob", "pw", base_dir=base)
    old = A.issue_session("bob", base_dir=base)
    assert A.delete_user("bob", base_dir=base) is True
    assert A.verify_session(old, base_dir=base) is None          # user gone
    A.create_user("bob", "pw", base_dir=base)                     # same name, same password
    assert A.verify_session(old, base_dir=base) is None
    assert A.verify_session(A.issue_session("bob", base_dir=base), base_dir=base)


def test_u14_role_change_keeps_the_session_and_shows_the_new_role(base):
    A.create_user("bob", "pw", base_dir=base)
    tok = A.issue_session("bob", base_dir=base)
    A.set_role("bob", "reviewer", base_dir=base)
    assert A.verify_session(tok, base_dir=base) == {"username": "bob", "role": "reviewer"}


def test_u14_token_format_is_unchanged(base):
    """app_auth and the browser cookie see the same 3-part token."""
    A.create_user("bob", "pw", base_dir=base)
    name, exp, sig = A.issue_session("bob", base_dir=base).split(".")
    assert name == "bob" and int(exp) > time.time() and len(sig) == 64
    assert "|" not in sig


def test_u14_record_from_before_stamps_keeps_its_session_until_reset(base):
    """A store written by the previous version has no "sess". Its users must
    not be logged out by the upgrade; the first reset then stamps them."""
    A.create_user("old", "pw", base_dir=base)
    doc = _doc(base)
    del doc["users"]["old"]["sess"]
    with open(_store(base), "w") as fh:
        json.dump(doc, fh)
    tok = A.issue_session("old", base_dir=base)
    assert A.verify_session(tok, base_dir=base)["username"] == "old"
    A.set_password("old", "pw2", base_dir=base)
    assert isinstance(_doc(base)["users"]["old"]["sess"], str)
    assert A.verify_session(tok, base_dir=base) is None


def test_u14_stamp_is_not_guessable_from_the_token(base):
    A.create_user("bob", "pw", base_dir=base)
    stamp = _doc(base)["users"]["bob"]["sess"]
    assert len(stamp) == 16 and stamp not in A.issue_session("bob", base_dir=base)


# ── U16: a corrupt store is not an empty store ────────────────────────

CORRUPT = [b"{not json", b"", b"[]", b'"x"', b"\xff\xfe\x00", b'{"users": []}',
           b'{"users": "x", "signing_key": "ab"}']


@pytest.mark.parametrize("raw", CORRUPT)
def test_u16_corrupt_store_fails_closed_and_is_kept(base, raw):
    A.create_user("admin1", "pw", role="admin", base_dir=base)
    tok = A.issue_session("admin1", base_dir=base)
    with open(_store(base), "wb") as fh:
        fh.write(raw)
    with pytest.raises(A.AccountsStoreError):
        A.count(base_dir=base)                       # never 0: bootstrap stays shut
    ok, msg = A.create_user("mallory", "pw", role="admin", base_dir=base)
    assert ok is False and "unreadable" in msg
    assert A.set_role("admin1", "operator", base_dir=base)[0] is False
    assert A.set_password("admin1", "x", base_dir=base)[0] is False
    assert A.delete_user("admin1", base_dir=base) is False
    assert A.bind_oidc_login("admin1", ("iss", "sub"), base_dir=base,
                             rebind_issuer="iss")[0] is False
    assert A.verify_password("admin1", "pw", base_dir=base) is False
    assert A.verify_session(tok, base_dir=base) is None
    assert A.get_user("admin1", base_dir=base) is None
    assert A.list_users(base_dir=base) == []
    assert A.get_oidc_binding("admin1", base_dir=base) is None
    forged = A.issue_session("admin1", base_dir=base)            # never raises
    assert A.verify_session(forged, base_dir=base) is None
    with open(_store(base), "rb") as fh:
        assert fh.read() == raw                      # the file was not rewritten


def test_u16_unreadable_file_is_not_an_empty_store(base):
    A.create_user("admin1", "pw", role="admin", base_dir=base)
    os.remove(_store(base))
    os.mkdir(_store(base))                           # open() -> IsADirectoryError
    with pytest.raises(A.AccountsStoreError):
        A.count(base_dir=base)
    assert A.create_user("mallory", "pw", role="admin", base_dir=base)[0] is False


def test_u16_missing_file_is_still_a_first_run(base):
    assert A.count(base_dir=base) == 0
    assert A.list_users(base_dir=base) == []
    assert A.create_user("admin1", "pw", role="admin", base_dir=base) == (True, "created")
    assert A.count(base_dir=base) == 1


def test_u16_error_is_an_oserror_for_existing_callers():
    assert issubclass(A.AccountsStoreError, OSError)


# ── U19: unknown user costs one PBKDF2 ────────────────────────────────

def test_u19_unknown_user_runs_a_dummy_hash(base, monkeypatch):
    A.create_user("bob", "pw", base_dir=base)
    calls = []
    real = A._hash_password

    def spy(password, salt, iters):
        calls.append((salt, iters))
        return real(password, salt, iters)

    monkeypatch.setattr(A, "_hash_password", spy)
    assert A.verify_password("nobody", "pw", base_dir=base) is False
    assert calls == [(A._DUMMY_SALT, A._PBKDF2_ITERS)]
    calls.clear()
    assert A.verify_password("bob", "wrong", base_dir=base) is False
    assert len(calls) == 1 and calls[0][1] == A._PBKDF2_ITERS
    calls.clear()
    assert A.verify_password("bob", "pw", base_dir=base) is True
    assert len(calls) == 1


def test_u19_unknown_user_on_missing_or_corrupt_store_also_hashes(base, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "_hash_password",
                        lambda p, s, i: calls.append(i) or "00")
    assert A.verify_password("nobody", "pw", base_dir=base) is False     # no file
    with open(_store(base), "w") as fh:
        fh.write("{broken")
    assert A.verify_password("nobody", "pw", base_dir=base) is False     # corrupt
    assert calls == [A._PBKDF2_ITERS, A._PBKDF2_ITERS]


# ── U21: the temp file is never wider than 0600 ───────────────────────

def test_u21_tmp_is_owner_only_before_the_replace(base, monkeypatch):
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
        seen["src"] = src
        return real_replace(src, dst)

    old_umask = os.umask(0)                          # worst case: nothing masked
    try:
        monkeypatch.setattr(os, "replace", spy)
        assert A.create_user("bob", "pw", base_dir=base)[0]
    finally:
        os.umask(old_umask)
    assert seen["src"].endswith(A.ACCOUNTS_FILE + ".tmp")
    assert seen["mode"] == 0o600
    assert stat.S_IMODE(os.stat(_store(base)).st_mode) == 0o600


def test_u21_leftover_wide_tmp_is_narrowed(base):
    """O_CREAT's mode is ignored for a file that already exists."""
    tmp = _store(base) + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("stale")
    os.chmod(tmp, 0o666)
    modes = []
    real_dump = json.dump

    def spy(doc, fh, **kw):
        modes.append(stat.S_IMODE(os.fstat(fh.fileno()).st_mode))
        return real_dump(doc, fh, **kw)

    import unittest.mock as mock
    with mock.patch.object(A.json, "dump", spy):
        assert A.create_user("bob", "pw", base_dir=base)[0]
    assert modes and set(modes) == {0o600}           # narrowed before any byte
    assert A.verify_password("bob", "pw", base_dir=base) is True


# ── U10: a non-object user record ─────────────────────────────────────

@pytest.mark.parametrize("bad", ["a string", 5, None, ["x"], True])
def test_u10_non_object_record_is_a_clean_refusal(base, bad):
    A.create_user("amy", "pw", role="admin", base_dir=base)
    doc = _doc(base)
    doc["users"]["bob"] = bad
    with open(_store(base), "w") as fh:
        json.dump(doc, fh)
    ok, msg = A.set_role("bob", "admin", base_dir=base)
    assert ok is False and "corrupt" in msg and msg != "no such user"
    ok, msg = A.set_password("bob", "pw", base_dir=base)
    assert ok is False and "corrupt" in msg
    assert _doc(base)["users"]["bob"] == bad         # not rewritten
    assert A.verify_password("bob", "pw", base_dir=base) is False
    assert A.get_user("bob", base_dir=base) is None
    assert A.verify_session(A.issue_session("bob", base_dir=base), base_dir=base) is None
    # the healthy account beside it is unaffected
    assert A.set_role("amy", "reviewer", base_dir=base) == (True, "updated")
    assert A.delete_user("bob", base_dir=base) is True           # and it can be removed


def test_u10_missing_user_message_unchanged(base):
    A.create_user("amy", "pw", base_dir=base)
    assert A.set_role("nobody", "admin", base_dir=base) == (False, "no such user")
    assert A.set_password("nobody", "pw", base_dir=base) == (False, "no such user")
