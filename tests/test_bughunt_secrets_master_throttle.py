"""Bug-hunt secrets-master-throttle-atomic (dva-auth r3 F3, on app_secrets).

/api/secrets/unlock and /api/secrets/change_password share one throttle label
on the vault master password. Both ran ``auth_throttle.check`` and only counted
the failure after the verify: a burst sent at once passed one stale check
together and got one verify, and one 401 verdict, per request.

The fix checks AND counts under one lock hold before the verify, with one
attempt in flight (the rest 429). A storage or status refusal gives the attempt
back as it was (earlier failures kept); an exception in the verify leaves it
counted (fail closed). Stacked on dva-auth r4's reserve/release(owner).
"""
from __future__ import annotations

import threading
import types

import pytest
from flask import Flask

from bulk_downloader import app_secrets
from bulk_downloader import auth_throttle as AT
from bulk_downloader import secrets_store as ss

BD_GATE_SCOPE = "module"

PW = "the-right-master-pw"
NEW = "the-next-master-pw"
BAD = "a-wrong-guess-pw"
UNLOCK = "/api/secrets/unlock"
CHANGE = "/api/secrets/change_password"


class _Vault:
    """Master-password backend whose verifies can be held open together."""
    name = "master_password"

    def __init__(self):
        self.pw = PW
        self.unlocked = False
        self.verifies = 0
        self.raise_in_verify = None
        self.go = threading.Event()
        self.go.set()
        self.cv = threading.Condition()

    def _verify(self, pw):
        with self.cv:
            self.verifies += 1
            self.cv.notify_all()
        self.go.wait(timeout=20)
        if self.raise_in_verify is not None:
            raise self.raise_in_verify
        return pw == self.pw

    def is_initialized(self):
        return True

    def is_unlocked(self):
        return self.unlocked

    def lock(self):
        self.unlocked = False

    def unlock(self, _pw):  # pragma: no cover
        raise AssertionError("non-atomic unlock path used")

    def change_password(self, _old, _new):  # pragma: no cover
        raise AssertionError("non-atomic rotation path used")

    def unlock_with_status(self, pw, **_kw):
        self.unlocked = self._verify(pw)
        return {"unlocked": self.unlocked, "initialized_now": False,
                "is_initialized": True, "is_unlocked": self.unlocked}

    def change_password_with_status(self, old, new):
        if not self._verify(old):
            return {"changed": False, "reason": "incorrect_password"}
        self.pw = new
        return {"changed": True, "reason": "changed"}


@pytest.fixture
def vault(monkeypatch):
    v = _Vault()
    monkeypatch.setattr(ss, "get_backend", lambda: v)
    monkeypatch.setattr(AT, "_state", {})
    monkeypatch.setattr(AT, "_store_get", lambda _key: None)  # env decides
    monkeypatch.setattr(AT, "time", types.SimpleNamespace(monotonic=lambda: 1000.0))
    monkeypatch.setenv("BD_AUTH_THROTTLE", "1")
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", "2")
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", "60")
    monkeypatch.setenv("BD_AUTH_THROTTLE_MAX", "300")
    return v


def _post(path, guess):
    flask_app = Flask("secrets-master-throttle")
    flask_app.register_blueprint(app_secrets.secrets_bp)
    body = ({"password": guess} if path == UNLOCK
            else {"old_password": guess, "new_password": NEW})
    return flask_app.test_client().post(path, json=body)


def _state():
    """(failures counted, attempt still in flight) on the shared label."""
    st = AT.snapshot().get(AT.LABEL_MASTER_PASSWORD, {})
    return st.get("fails", 0), "held" in st


def _burst(vault, paths, n):
    """n concurrent wrong guesses (paths[i % len]); every verify that starts is
    held until each request is either answered or inside the verify, so all n
    are in the check/verify window together. Returns (codes, verifies)."""
    codes = []
    vault.verifies = 0
    vault.go.clear()

    def one(i):
        code = _post(paths[i % len(paths)], BAD).status_code
        with vault.cv:
            codes.append(code)
            vault.cv.notify_all()
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    with vault.cv:
        vault.cv.wait_for(lambda: len(codes) + vault.verifies == n, timeout=20)
    vault.go.set()
    for t in ts:
        t.join(60)
    return codes, vault.verifies


@pytest.mark.parametrize("paths,n", [
    ([UNLOCK], 40), ([UNLOCK], 100), ([CHANGE], 40), ([UNLOCK, CHANGE], 40),
], ids=["unlock40", "unlock100", "change40", "both40"])
def test_burst_gets_no_more_401_than_sequential(vault, paths, n):
    seq = [_post(paths[i % len(paths)], BAD).status_code for i in range(n)]
    seq_401 = seq.count(401)
    assert seq_401 == 3 and seq.count(429) == n - 3, seq  # control: sequential is throttled
    AT.reset()
    codes, verifies = _burst(vault, paths, n)
    assert len(codes) == n, codes
    assert codes.count(401) <= seq_401 and codes.count(429) == n - codes.count(401), \
        (sorted(codes), seq_401)
    assert verifies == 1, verifies  # one verify in flight; a refused guess costs no KDF
    assert _state() == (1, False)


def test_burst_ctl_throttle_off_answers_every_guess(vault, monkeypatch):
    monkeypatch.delenv("BD_AUTH_THROTTLE")
    codes, verifies = _burst(vault, [UNLOCK, CHANGE], 20)
    assert codes == [401] * 20 and verifies == 20
    assert AT.snapshot() == {}


def test_success_clears_and_never_costs_an_attempt(vault):
    assert [_post(UNLOCK, BAD).status_code for _ in range(2)] == [401, 401]
    assert _post(UNLOCK, PW).status_code == 200
    assert _state() == (0, False)
    for _ in range(4):
        assert _post(UNLOCK, PW).status_code == 200
    assert [_post(UNLOCK, BAD).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_change_password_success_clears(vault):
    assert _post(CHANGE, BAD).status_code == 401
    assert _post(CHANGE, PW).status_code == 200 and vault.pw == NEW
    assert _state() == (0, False)


@pytest.mark.parametrize("path", [UNLOCK, CHANGE])
def test_verify_exception_stays_counted_and_frees_the_label(vault, path):
    vault.raise_in_verify = RuntimeError("kdf blew up")
    assert _post(path, BAD).status_code == 500
    assert _state() == (1, False)  # counted (fail closed), not left in flight
    vault.raise_in_verify = None
    assert _post(path, BAD).status_code == 401
    assert _state() == (2, False)


REFUSALS = [
    (UNLOCK, ss.SecretsUnreadableError("cannot read"), 409),
    (UNLOCK, ss.SecretsIntegrityError("damaged"), 409),
    (UNLOCK, ss.SecretsPersistError("disk full"), 500),
    (UNLOCK, ss.SecretsPasswordPolicyError("too short"), 400),
    (CHANGE, ss.SecretsUnreadableError("cannot read"), 409),
    (CHANGE, ss.SecretsPersistError("disk full"), 500),
]
REFUSAL_IDS = ["unlock_unreadable", "unlock_integrity", "unlock_persist", "unlock_policy",
               "change_unreadable", "change_persist"]


@pytest.mark.parametrize("path,error,code", REFUSALS, ids=REFUSAL_IDS)
def test_storage_refusal_gives_the_attempt_back(vault, monkeypatch, path, error, code):
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", "0")  # one counted guess would lock
    vault.raise_in_verify = error
    assert _post(path, BAD).status_code == code
    assert _state() == (0, False)
    vault.raise_in_verify = None
    assert _post(path, BAD).status_code == 401


@pytest.mark.parametrize("path,error,code", REFUSALS, ids=REFUSAL_IDS)
def test_storage_refusal_keeps_the_earlier_failures(vault, monkeypatch, path, error, code):
    assert [_post(path, BAD).status_code for _ in range(3)] == [401, 401, 401]
    before = AT.snapshot()[AT.LABEL_MASTER_PASSWORD]
    assert (before["fails"], before["until"]) == (3, 1060.0)  # control: priors exist
    assert set(before) == {"fails", "until", "last"}, before  # a settled guess keeps no snapshot
    monkeypatch.setattr(AT, "time", types.SimpleNamespace(monotonic=lambda: 1061.0))
    vault.raise_in_verify = error
    assert _post(path, BAD).status_code == code
    assert AT.snapshot().get(AT.LABEL_MASTER_PASSWORD) == before  # given back, priors kept
    vault.raise_in_verify = None
    assert _post(path, BAD).status_code == 401
    assert _state() == (4, False)


def test_refund_by_another_owner_frees_nothing(vault):
    me, other = object(), object()
    assert AT.reserve((AT.LABEL_MASTER_PASSWORD,), owner=me) == (True, 0.0)
    AT.release((AT.LABEL_MASTER_PASSWORD,), refund=True, owner=other)
    assert _state() == (1, True)
    AT.release((AT.LABEL_MASTER_PASSWORD,), refund=True, owner=me)
    assert AT.snapshot() == {}


def test_incoherent_status_gives_the_attempt_back(vault, monkeypatch):
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", "0")
    monkeypatch.setattr(vault, "unlock_with_status", lambda *_a, **_k: {"unlocked": "yes"})
    assert _post(UNLOCK, PW).status_code == 500
    assert _state() == (0, False)


# ── AMEND 1318Z: a long flood never overflows the back-off (R224-A F4 shape) ──
# free (2) + 1024 failures: the next count's exponent would be 2**1024, past a float.
FLOOD = 2 + 1024


def _seed_flood():
    AT._state[AT.LABEL_MASTER_PASSWORD] = {"fails": FLOOD, "until": 0.0, "last": 0.0}


@pytest.mark.parametrize("path", [UNLOCK, CHANGE])
def test_flood_then_correct_master_password_is_200(vault, path):
    _seed_flood()  # cooldown over: the real user tries again
    r = _post(path, PW)
    assert r.status_code == 200, (r.status_code, r.get_json())
    assert _state() == (0, False)


def test_flood_wrong_guess_is_capped_not_500(vault):
    _seed_flood()
    r = _post(UNLOCK, BAD)
    assert r.status_code == 401, (r.status_code, r.get_json())
    st = AT.snapshot()[AT.LABEL_MASTER_PASSWORD]
    assert (st["fails"], st["until"], "held" in st) == (FLOOD + 1, 1000.0 + 300.0, False)
    r = _post(UNLOCK, PW)
    assert r.status_code == 429 and r.headers["Retry-After"] == "301"


def test_flood_record_failure_does_not_overflow(vault):
    _seed_flood()
    try:
        AT.record_failure(AT.LABEL_MASTER_PASSWORD)
    except OverflowError as e:
        pytest.fail(f"record_failure overflowed past free+1024 fails: {e}")
    st = AT.snapshot()[AT.LABEL_MASTER_PASSWORD]
    assert (st["fails"], st["until"]) == (FLOOD + 1, 1000.0 + 300.0)


class _PowRaises:
    def __pow__(self, _other):
        raise ArithmeticError("planted")


def test_count_that_raises_leaves_the_label_as_it_was(vault, monkeypatch):
    _seed_flood()
    monkeypatch.setattr(AT, "_MULTIPLIER", _PowRaises())
    with pytest.raises(ArithmeticError):
        AT.record_failure(AT.LABEL_MASTER_PASSWORD)
    assert AT.snapshot()[AT.LABEL_MASTER_PASSWORD]["fails"] == FLOOD
