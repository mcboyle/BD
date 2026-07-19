"""RED-first repro for F-COREBD02-01 (secret_handling / DP-08).

audit_security.secret_scan flags hardcoded-secret SHAPES and, for each hit,
emits the matched line as a `context` field via `line.strip()[:40]`. Its own
comment claims "never echo the matched secret", but that 40-char preview
INCLUDES the secret value (fully for short secrets), so the scanner's own
output discloses the credential it flagged.

Fix: redact the value in the context (keep only the assignment target/label,
mask the value) so scanner output never leaks the credential.
"""
import inspect


def test_secret_scan_source_does_not_emit_raw_context():
    from bulk_downloader.dev_suite import audit_security as asec
    src = inspect.getsource(asec.secret_scan)
    assert "line.strip()[:40]" not in src, \
        "secret_scan still emits the raw matched line as context (leaks the secret)"


def test_secret_context_redactor_masks_value():
    from bulk_downloader.dev_suite import audit_security as asec
    red = asec._redact_secret_context
    cases = [
        ('api_key = "sk-LIVE-SECRETVALUE-123456"', "SECRETVALUE"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMISECRETbPxRfiCYEXAMPLEKEY", "SECRETbPxRfi"),
        ("bearer_token: ghp_SECRETtokenVALUE0123456789abcdef", "SECRETtokenVALUE"),
    ]
    for line, secret in cases:
        out = red(line)
        assert secret not in out, f"redactor leaked secret {secret!r}: {out!r}"
        assert "redacted" in out.lower(), f"no redaction marker: {out!r}"
