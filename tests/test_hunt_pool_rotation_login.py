"""Both account rotation paths must start login for the new account."""

from bulk_downloader.account_pool import configure_pool, remove_pool
from bulk_downloader.runner_accounts import AccountsMixin


def test_pool_rotation_starts_login_like_legacy_rotation():
    class Runner:
        _rotate_account_if_available = AccountsMixin._rotate_account_if_available

        def __init__(self, site_id):
            self.site_id = site_id
            self.config = {"accounts": [{"username": "alice"}, {"username": "bob"}]}
            self._active_account_idx = 0
            self.login_calls = []
            self.cookies = []

        def _persist_pool_state(self):
            pass

        def _persist_account_state(self):
            pass

        def set_cookies(self, cookies):
            self.cookies = cookies

        def login_async(self, **kwargs):
            self.login_calls.append(kwargs)

    legacy = Runner("hunt-pool-login-legacy")
    assert legacy._rotate_account_if_available("rate limited") is True
    assert legacy._active_account_idx == 1
    assert legacy.login_calls == [{"allow_manual": False}]

    pooled = Runner("hunt-pool-login-pooled")
    configure_pool(pooled.site_id, pooled.config["accounts"])
    try:
        assert pooled._rotate_account_if_available("rate limited") is True
        assert pooled._active_account_idx == 1
        assert pooled.cookies == []
        assert pooled.login_calls == [{"allow_manual": False}]
    finally:
        remove_pool(pooled.site_id)
        remove_pool(legacy.site_id)


def test_pool_rotation_login_start_failure_does_not_rotate_twice():
    class Runner:
        _rotate_account_if_available = AccountsMixin._rotate_account_if_available

        def __init__(self, site_id):
            self.site_id = site_id
            self.config = {"accounts": [{"username": "alice"}, {"username": "bob"}, {"username": "carol"}]}
            self._active_account_idx = 0
            self.cookies = []

        def _persist_pool_state(self):
            pass

        def _persist_account_state(self):
            pass

        def set_cookies(self, cookies):
            self.cookies = cookies

        def login_async(self, **kwargs):
            raise RuntimeError("can't start new thread")

    pooled = Runner("hunt-pool-login-raises")
    configure_pool(pooled.site_id, pooled.config["accounts"])
    try:
        assert pooled._rotate_account_if_available("rate limited") is True
        assert pooled._active_account_idx == 1
        assert pooled.config["username"] == "bob"
        # the legacy fallback never ran: no 24h cfg cooldown written on any account
        assert not any("cooldown_until" in a for a in pooled.config["accounts"])
    finally:
        remove_pool(pooled.site_id)
