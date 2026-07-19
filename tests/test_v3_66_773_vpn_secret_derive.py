"""v3.66.773 -- T-DERIVE-SECRET: the vpn secret-key-hints drift.

bulk_downloader/vpn.py and bulk_downloader/vpn_config.py each hand-kept a
_SECRET_KEY_HINTS tuple, and they DRIFTED: vpn_config carried "account_number",
vpn did not. Both feed a redaction check (is_secret_config_key(k) OR hint match),
and is_secret_config_key does NOT catch "account_number", so vpn._redact_config
LEAKED a VPN "account_number" config value in cleartext while vpn_config redacted
it. Deriving the two mirrors from one canonical (vpn is the SoT; vpn_config derives
via the already-existing vpn_config->vpn edge) closes the leak and the drift.

RED on pristine v3.66.772: vpn._redact_config leaks account_number, and the two
hint tuples are not unified. GREEN after the derive.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_vpn_redaction_covers_account_number():
    """The concrete leak: an account_number VPN config value must be redacted."""
    from bulk_downloader import vpn
    out = vpn._redact_config({"account_number": "12345", "host": "example.com"})
    assert out["account_number"] == "***", (
        "vpn._redact_config leaked account_number in cleartext: %r" % out["account_number"])
    assert out["host"] == "example.com", "non-secret host must not be redacted"


def test_vpn_and_vpn_config_share_one_canonical_hint_set():
    """No drift: vpn_config must not keep its own hand-kept _SECRET_KEY_HINTS tuple;
    it derives the canonical set from vpn, so the two can never diverge again."""
    from bulk_downloader import vpn, vpn_config
    # both must agree on the secret verdict for every hint-covered key
    hints = vpn._SECRET_KEY_HINTS
    assert "account_number" in hints, "canonical hints must include account_number"
    for probe in ("my_account_number", "vpn_password", "auth_token"):
        assert vpn_config._vpn_key_is_secret(probe) is True, (
            "vpn_config disagrees on %r -- the hint sets drifted again" % probe)


def test_vpn_config_derives_hints_not_redefines():
    """vpn_config.py must NOT hold its own module-level _SECRET_KEY_HINTS tuple
    (the source of the drift); the canonical lives in vpn."""
    src = (REPO / "bulk_downloader" / "vpn_config.py").read_text(encoding="utf-8")
    # a bare module-level assignment of the tuple must be gone; an import is fine
    assert "\n_SECRET_KEY_HINTS = (" not in src, (
        "vpn_config still defines its own _SECRET_KEY_HINTS tuple -- derive it from vpn")
