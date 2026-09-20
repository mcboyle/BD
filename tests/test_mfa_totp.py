"""Tests for bulk_downloader.login_impl.mfa_helper (row895).

RFC 6238 TOTP module for a single operator-owned test2 target's MFA
endpoint. Secrets are resolved through secrets_store, namespaced apart
from site passwords, never touching a real third-party login.

Coverage:
  - RFC 6238 Appendix B SHA1 test vectors (8-digit codes)
  - drift-window verification (+/-1 step tolerates clock skew)
  - graceful error on missing/invalid configuration
"""
import base64
import pytest

from bulk_downloader.login_impl import mfa_helper as mfa

BD_GATE_SCOPE = "module"

# RFC 6238 Appendix B: seed "12345678901234567890" (ASCII) for SHA1,
# base32-encoded below. 8-digit codes at X=30s, T0=0.
_SHA1_SECRET_B32 = base64.b32encode(b"12345678901234567890").decode("ascii")  # derived, not a literal (gitleaks)

_RFC6238_SHA1_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize("timestamp,expected", _RFC6238_SHA1_VECTORS)
def test_generate_totp_matches_rfc6238_vectors(timestamp, expected):
    code = mfa.generate_totp(
        _SHA1_SECRET_B32, timestamp=timestamp, digits=8, algorithm="sha1"
    )
    assert code == expected


def test_verify_totp_accepts_exact_step():
    code = mfa.generate_totp(_SHA1_SECRET_B32, timestamp=59, digits=8)
    assert mfa.verify_totp(_SHA1_SECRET_B32, code, timestamp=59, digits=8)


def test_verify_totp_tolerates_one_step_drift():
    code = mfa.generate_totp(_SHA1_SECRET_B32, timestamp=59, digits=8)
    # 59 -> step 1; timestamp 89 is step 2 (30s later), one step of drift.
    assert mfa.verify_totp(
        _SHA1_SECRET_B32, code, timestamp=89, digits=8, window=1
    )


def test_verify_totp_rejects_beyond_window():
    code = mfa.generate_totp(_SHA1_SECRET_B32, timestamp=59, digits=8)
    # 59 -> step 1; timestamp 150 is step 5, well outside a 1-step window.
    assert not mfa.verify_totp(
        _SHA1_SECRET_B32, code, timestamp=150, digits=8, window=1
    )


def test_generate_totp_missing_secret_raises_config_error():
    with pytest.raises(mfa.MfaConfigError):
        mfa.generate_totp("", timestamp=59)


def test_generate_totp_invalid_base32_raises_config_error():
    with pytest.raises(mfa.MfaConfigError):
        mfa.generate_totp("not-valid-base32!!!", timestamp=59)


def test_resolve_totp_secret_missing_config_is_graceful(monkeypatch):
    from bulk_downloader import secrets_store as ss

    monkeypatch.setattr(ss, "get_backend", lambda: ss.PlaintextBackend())
    with pytest.raises(mfa.MfaConfigError):
        mfa.resolve_totp_secret("nonexistent-test2-target")


def test_resolve_totp_secret_uses_namespaced_key(monkeypatch):
    from bulk_downloader import secrets_store as ss

    class _StubBackend:
        def get(self, key):
            return _SHA1_SECRET_B32 if key == mfa.site_totp_key("test2-target") else None

    monkeypatch.setattr(ss, "get_backend", lambda: _StubBackend())
    assert mfa.resolve_totp_secret("test2-target") == _SHA1_SECRET_B32
    with pytest.raises(mfa.MfaConfigError):
        mfa.resolve_totp_secret("some-other-site")
