"""Tests for the opt-in auth attempt throttle (NEW-9).

Two layers:
  * unit — the back-off math, free allowance, escalation + cap, success
    reset, monotonic recovery, and the disabled no-op contract.
  * endpoint — /api/secrets/unlock and /api/secrets/change_password
    return 429 + Retry-After once locked out, share ONE label (guesses
    on either endpoint accumulate together), reset on a correct verify,
    and are byte-identical (never 429) when the flag is unset.

Plain functions + context managers (no local fixtures) so the suite is
green under both real pytest and the custom runner. Endpoint tests use a
fake master backend to exercise the throttle path without crypto setup.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import time

from bulk_downloader import auth_throttle as at


# ─── helpers ─────────────────────────────────────────────────────────

@contextmanager
def _env(**kw):
    prior = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeMaster:
    """Duck-typed master-password backend for endpoint tests."""
    name = "master_password"

    def __init__(self, pw="correct-pw"):
        self._pw = pw
        self._unlocked = False

    def unlock(self, pw):
        self._unlocked = (pw == self._pw)
        return self._unlocked

    def is_unlocked(self):
        return self._unlocked

    def lock(self):
        self._unlocked = False

    def change_password(self, old, new):
        if old != self._pw:
            return False
        self._pw = new
        return True

    def get(self, k):
        return None

    def set(self, k, v):
        pass

    def delete(self, k):
        return False

    def list_keys(self):
        return []


@contextmanager
def _client():
    """Flask test client with CSRF header, isolated cwd, a fake master
    backend installed, and throttle state reset."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        prior_be, prior_pref = ss._backend, ss._backend_pref
        try:
            db_init()
            c = A.app.test_client()
            r = c.get('/api/pair'); token = r.get_json()['token']
            r = c.post('/api/pair/redeem', json={'token': token})
            H = {'X-CSRF-Token': r.get_json()['csrf_token']}
            fake = _FakeMaster()
            ss._backend = fake
            ss._backend_pref = "master_password"
            ss._audited_cache = None
            at.reset()
            yield c, H, fake
        finally:
            ss._backend, ss._backend_pref = prior_be, prior_pref
            at.reset()
            os.chdir(orig_cwd)


# ─── unit: math + contracts ──────────────────────────────────────────

def test_disabled_is_a_noop():
    with _env(BD_AUTH_THROTTLE=None):
        at.reset()
        for _ in range(50):
            at.record_failure(at.LABEL_MASTER_PASSWORD)
        allowed, retry = at.check(at.LABEL_MASTER_PASSWORD)
        assert allowed is True and retry == 0.0


def test_free_allowance_then_lockout():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="2",
              BD_AUTH_THROTTLE_BASE="5", BD_AUTH_THROTTLE_MAX="300"):
        at.reset()
        at.record_failure(at.LABEL_MASTER_PASSWORD)
        at.record_failure(at.LABEL_MASTER_PASSWORD)
        assert at.check(at.LABEL_MASTER_PASSWORD)[0] is True   # within free
        at.record_failure(at.LABEL_MASTER_PASSWORD)            # over by 1
        allowed, retry = at.check(at.LABEL_MASTER_PASSWORD)
        assert allowed is False
        assert 0 < retry <= 5


def test_escalation_and_cap():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0",
              BD_AUTH_THROTTLE_BASE="1", BD_AUTH_THROTTLE_MAX="4"):
        at.reset()
        seen = []
        for _ in range(5):
            at.record_failure(at.LABEL_MASTER_PASSWORD)
            seen.append(at.check(at.LABEL_MASTER_PASSWORD)[1])
        # base*2**(over-1): 1, 2, 4, then capped at max=4, 4
        assert seen[0] == 1
        assert seen[1] == 2
        assert seen[2] == 4
        assert all(v <= 4 for v in seen)            # never exceeds the cap


def test_success_resets_counter():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0",
              BD_AUTH_THROTTLE_BASE="10"):
        at.reset()
        at.record_failure(at.LABEL_MASTER_PASSWORD)
        assert at.check(at.LABEL_MASTER_PASSWORD)[0] is False
        at.record_success(at.LABEL_MASTER_PASSWORD)
        assert at.check(at.LABEL_MASTER_PASSWORD) == (True, 0.0)
        assert at.snapshot() == {}


def test_recovery_after_cooldown_elapses():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0",
              BD_AUTH_THROTTLE_BASE="0.15"):
        at.reset()
        at.record_failure(at.LABEL_MASTER_PASSWORD)
        assert at.check(at.LABEL_MASTER_PASSWORD)[0] is False
        time.sleep(0.2)
        assert at.check(at.LABEL_MASTER_PASSWORD)[0] is True   # cooldown elapsed


def test_reset_one_label_only():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0", BD_AUTH_THROTTLE_BASE="10"):
        at.reset()
        at.record_failure("a")
        at.record_failure("b")
        at.reset("a")
        assert at.check("a") == (True, 0.0)
        assert at.check("b")[0] is False


# ─── endpoint: integration ───────────────────────────────────────────

def test_unlock_disabled_never_locks_out():
    with _env(BD_AUTH_THROTTLE=None), _client() as (c, H, fake):
        for _ in range(8):
            r = c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
            assert r.status_code == 401           # byte-identical: always 401, never 429


def test_unlock_locks_out_after_free_with_retry_after():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="2",
              BD_AUTH_THROTTLE_BASE="30"), _client() as (c, H, fake):
        codes = []
        for _ in range(4):
            r = c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
            codes.append(r.status_code)
        assert codes[:2] == [401, 401]            # free allowance
        assert 429 in codes                        # locked out after
        # the locked response carries Retry-After
        r = c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1


def test_unlock_success_resets_counter():
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="2",
              BD_AUTH_THROTTLE_BASE="30"), _client() as (c, H, fake):
        c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
        c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
        r = c.post('/api/secrets/unlock', json={'password': 'correct-pw'}, headers=H)
        assert r.status_code == 200                # correct -> resets
        # counter cleared: a fresh wrong attempt is 401, not 429
        r = c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
        assert r.status_code == 401


def test_shared_label_across_both_endpoints():
    # FREE=0 -> the first failure on EITHER endpoint locks the shared label.
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0",
              BD_AUTH_THROTTLE_BASE="30"), _client() as (c, H, fake):
        r = c.post('/api/secrets/unlock', json={'password': 'wrong'}, headers=H)
        assert r.status_code == 401                # failure recorded on shared label
        # change_password (valid-length new pw) must now be locked out too
        r = c.post('/api/secrets/change_password',
                   json={'old_password': 'whatever', 'new_password': 'a-valid-pw'},
                   headers=H)
        assert r.status_code == 429                # shared label -> locked
        assert int(r.headers["Retry-After"]) >= 1


def test_change_password_short_new_pw_still_400_when_locked():
    # The new-password length gate precedes the throttle check, so a
    # malformed request is still a 400 (input validation), not a 429.
    with _env(BD_AUTH_THROTTLE="1", BD_AUTH_THROTTLE_FREE="0",
              BD_AUTH_THROTTLE_BASE="30"), _client() as (c, H, fake):
        at.record_failure(at.LABEL_MASTER_PASSWORD)   # pre-lock
        r = c.post('/api/secrets/change_password',
                   json={'old_password': 'x', 'new_password': 'short'}, headers=H)
        assert r.status_code == 400
