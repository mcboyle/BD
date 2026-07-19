"""RED-first guard for F-COCKPIT02-01.

cockpit_core.redact() imported a non-existent
bulk_downloader.capture_redact.redact_text, so every call fell through to a
narrow value-strip fallback that misses the I0008 floor classes
(code/state/otp/nonce/authorization/bearer/csrf/challenge/captcha). The fix
points redact() at the existing canonical
capture_artifact_redact.redact_value, which delegates kv-secret classification
to the I0008 SoT. UI-surfaced log / doc excerpts must not leak floor secrets.

Pre-fix: the fallback leaves ?code=/?state=/... values intact -> this fails.
"""
from tools import cockpit_core as C

_FLOOR = ["code", "state", "otp", "nonce", "authorization", "bearer",
          "csrf", "challenge", "captcha"]


def test_redact_covers_floor_classes():
    leaked = []
    for k in _FLOOR:
        red = C.redact(f"https://ex.com/cb?{k}=SECRETVALUE12345")
        if "SECRETVALUE12345" in red:
            leaked.append(k)
    assert not leaked, f"redact() leaked floor-class secret values for: {leaked}"


def test_redact_preserves_non_secret_structure():
    # Control: non-secret query params (selectors) must survive unredacted --
    # the canonical redactor must not become redact-everything.
    red = C.redact("https://ex.com/x?color=red&page=2")
    assert "color=red" in red and "page=2" in red, red
