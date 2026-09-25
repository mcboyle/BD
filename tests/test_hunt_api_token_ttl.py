"""Explicit API token TTLs must not silently become permanent."""

BD_GATE_SCOPE = "module"

import time

from bulk_downloader import api_tokens


def test_fractional_ttl_expires_and_zero_is_rejected(clean_workdir):
    hourly = api_tokens.create_token(scope="read", ttl_hours=1)
    assert hourly["ok"] and hourly["expires_at"] > time.time()

    before = time.time()
    half_hour = api_tokens.create_token(scope="read", ttl_hours=0.5)
    assert half_hour["ok"]
    assert before + 1700 < half_hour["expires_at"] < before + 1900

    zero = api_tokens.create_token(scope="read", ttl_hours=0)
    assert zero["ok"] is False

    # A finite TTL whose expiry overflows to inf must not mint a permanent token.
    huge = api_tokens.create_token(scope="read", ttl_hours=1e305)
    assert huge["ok"] is False
