"""P3-T12 -- challenge content is a capture SIGNAL and is SCRUBBED safely.

Goal: prove, over a handcrafted synthetic fixture, that
  1. the detector classifies the page as a challenge (challenge_present), and
  2. the redaction floor scrubs the challenge-RESPONSE / TOKEN material the same
     way it scrubs any sensitive capture artifact, and
  3. the classifier never EMITS solving/bypass instructions.

DETECTION-AND-SCRUB ONLY. The fixture never solves or bypasses a challenge; the
"solving" values are obviously-fake placeholders used solely as scrubber bait, so
the proof is that solving-related content is DESTROYED, not produced. This is a
characterization + regression tripwire over EXISTING behaviour -- it changes no
detector/redactor code, and asserting it is meaningful because the raw fixture
provably carries sensitive-shaped content (test_raw_fixture_is_sensitive_shaped)
before the floor reduces it.

Runner-safe: zero-arg test fns, repo root from __file__, no pytest builtins.
"""
import json
from pathlib import Path

from bulk_downloader import challenge_classify as cc
from bulk_downloader import capture_artifact_redact as car

_REPO = Path(__file__).resolve().parent.parent
_FIX = _REPO / "tests" / "corpus" / "challenge" / "challenge_solving_synthetic.cap.json"


def _load():
    return json.loads(_FIX.read_text(encoding="utf-8"))


def _observation(f):
    return {"text": f["dom_log"][0]["html"],
            "title": f.get("title", ""),
            "markers": " ".join(f.get("challenge_markers", []))}


# --- the fixture is clearly synthetic -------------------------------------- #
def test_fixture_is_marked_synthetic():
    f = _load()
    assert f.get("_synthetic") is True, "fixture must self-identify as synthetic bait"


# --- (1) DETECTION: the page is flagged as a challenge --------------------- #
def test_page_detected_as_challenge_present():
    f = _load()
    out = cc.classify(_observation(f))
    # challenge_present == a recognized challenge type (not 'unknown')
    assert out["type"] != "unknown", f"page not detected as a challenge: {out}"
    assert out["type"] in ("turnstile", "hcaptcha", "recaptcha", "login-wall")
    assert out["advisory"] is True


# --- (3) the classifier never emits solving/bypass instructions ------------ #
def test_classifier_emits_no_solving_or_bypass_instructions():
    f = _load()
    out = cc.classify(_observation(f))
    blob = (out["observation_summary"] + " " + out["suggested_review_path"]).lower()
    for w in ("bypass", "solve", "evade", "defeat", "auto-submit", "token-harvest"):
        assert w not in blob, f"classifier leaked a forbidden instruction word: {w!r}"
    assert out["clean"] is True


# --- the bait is genuinely sensitive-shaped (else the scrub proof is vacuous) #
def test_raw_fixture_is_sensitive_shaped():
    f = _load()
    kinds = {k for _, k in car.scan_artifact_secrets(f)}
    assert "jwt" in kinds, f"fixture should carry a JWT-shaped challenge response; got {kinds}"
    assert "opaque_token" in kinds, f"fixture should carry opaque challenge tokens; got {kinds}"


# --- (2) SCRUB: the floor destroys the recognized response/token material --- #
def test_redaction_floor_scrubs_challenge_response_tokens():
    f = _load()
    red = car.redact_artifact(f)
    blk = red["challenge_solving_synthetic"]
    # the opaque Turnstile response, the JWT, and the opaque challenge token are
    # all reduced to the placeholder -- challenge-response material is destroyed.
    for key in ("cf_turnstile_response", "challenge_jwt", "challenge_token"):
        assert blk.get(key) == car.PLACEHOLDER, \
            f"{key} not scrubbed: {blk.get(key)!r}"
    # and the scanner finds NO residual jwt / opaque token / signed-url anywhere.
    residual = {k for _, k in car.scan_artifact_secrets(red)}
    assert not (residual & {"jwt", "opaque_token", "signed_url"}), \
        f"redaction left recognized challenge-response secrets: {residual}"


# --- (2b) the cloudflare challenge-response carried as a URL QUERY PARAM is --- #
# --- now ALSO scrubbed by the floor (P3-T12 gap closed: SENSITIVE_QS_KEY was --- #
# --- widened with 'challenge'/'cf_chl' so cf_challenge_response / __cf_chl_tk --- #
# --- values are redacted like any signed token). This was previously a known --- #
# --- gap; the widening is a deliberate redaction-HARDENING (scrubs more,      --- #
# --- never less). If this regresses, the floor stopped covering the form.     --- #
def test_cf_query_param_challenge_token_is_scrubbed():
    f = _load()
    red = car.redact_artifact(f)
    verify_url = red["network_log"][0]["url"]
    # the response token value is gone; the URL structure (host/path/key) stays.
    assert "SYNTHETIC_FAKE_response" not in verify_url, \
        f"cf_challenge_response value not scrubbed: {verify_url}"
    assert car.PLACEHOLDER in verify_url
    assert "cf_challenge_response=" in verify_url  # key preserved, value masked


# --- per-provider challenge-response-shaped query values are all scrubbed --- #
def test_provider_response_shaped_query_values_scrubbed():
    from bulk_downloader.capture_redact import redact_query, PLACEHOLDER
    for url, key in [
        ("https://x.test/v?cf_challenge_response=FAKEcfresp123", "cf_challenge_response"),
        ("https://x.test/v?__cf_chl_tk=FAKEchltk123", "__cf_chl_tk"),
        ("https://x.test/v?h-captcha-response=FAKEhcap123", "h-captcha-response"),
        ("https://x.test/v?g-recaptcha-response=FAKErecap123", "g-recaptcha-response"),
    ]:
        out = redact_query(url)
        assert f"{key}={PLACEHOLDER}" in out, f"{key} value not redacted: {out}"


# --- free-text page instructions are NOT secrets and survive (correctly) ---- #
def test_free_text_page_instructions_survive():
    f = _load()
    red = car.redact_artifact(f)
    # the human-readable challenge instruction is page text, not a token/secret;
    # it is left intact (the dangerous response/token material above is gone).
    assert red["challenge_solving_synthetic"]["solve_hint"] == \
        f["challenge_solving_synthetic"]["solve_hint"]
    assert car.PLACEHOLDER not in red["challenge_solving_synthetic"]["solve_hint"]
