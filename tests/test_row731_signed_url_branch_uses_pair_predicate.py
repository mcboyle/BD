"""Row 731: signing metadata is not a residual credential."""
BD_GATE_SCOPE = "repo-wide"

from bulk_downloader.capture_artifact_redact import (
    KEEP_FULL, _kv_key_is_secret, _value_findings, current_profile,
    redact_capture, scan_artifact_secrets, scan_floor_secrets)
from bulk_downloader.capture_redact import SENSITIVE_QS_KEY


def test_signed_url_branch_uses_credential_pair_predicate():
    metadata = ("Expires", "Policy", "hash", "X-Amz-Date", "X-Amz-Algorithm", "X-Amz-Expires")
    assert sum(bool(SENSITIVE_QS_KEY.search(key)) != _kv_key_is_secret(key) for key in metadata) == 6
    assert _value_findings("https://cdn.example.invalid/a.m3u8?Expires=1700000000") == []
    assert "signed_url" in _value_findings("https://cdn.example.invalid/a?token=AAAA")


def test_keep_full_round_trip_leaves_no_residual_and_pins_the_kind_string():
    """Row 731's acceptance: redact under the keep_full profile, then scan,
    and assert CLEAN.

    The pair predicate is only worth having because a keep_full surface
    deliberately RETAINS signing metadata; the round trip is what proves the
    retained query does not read back as a residual credential.  Both scans
    are asserted on purpose: scan_floor_secrets FORGIVES a signed_url residual
    under keep_full, so on its own it cannot tell a clean retention from a
    forgiven one -- scan_artifact_secrets does not forgive, and that is the
    scan the workbench gate runs.  The kind string is pinned by name because
    the floor's forgiveness keys off exactly that literal.
    """
    profile = dict(current_profile())
    profile["network_signed_urls"] = KEEP_FULL
    signed = "https://cdn.example.invalid/a.m3u8?Expires=1700000000&Policy=AAAA"
    capture = {"dom_log": [], "network_log": [],
               "action_timeline": [{"selector": "a[download]", "effect_url": signed}]}
    assert "signed_url" in _value_findings("https://cdn.example.invalid/a?token=AAAA"), (
        "precondition: 'signed_url' is the kind string this forgiveness keys off")
    redacted = redact_capture(capture, profile)
    assert "Expires=1700000000" in redacted["action_timeline"][0]["effect_url"], (
        "precondition: keep_full really retained the signing metadata, so the "
        "two scans below have a nonzero subject")
    assert scan_artifact_secrets(redacted) == []
    assert scan_floor_secrets(redacted, profile) == []
