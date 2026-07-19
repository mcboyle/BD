"""Phase 9.13 -- challenge classification, detection only (RED-first)."""
from bulk_downloader import challenge_classify as cc

def test_turnstile_classified():
    out=cc.classify({"text":"<div class='cf-turnstile'></div>"})
    assert out["type"]=="turnstile" and out["advisory"] is True

def test_recaptcha_classified():
    out=cc.classify({"text":"g-recaptcha sitekey"})
    assert out["type"]=="recaptcha"

def test_login_wall_classified():
    out=cc.classify({"text":"Please sign in with your password"})
    assert out["type"]=="login-wall"

def test_unknown_when_no_signature():
    out=cc.classify({"text":"just a normal page"})
    assert out["type"]=="unknown"

def test_no_bypass_text_in_output():
    out=cc.classify({"text":"cf-turnstile"})
    blob=(out["observation_summary"]+" "+out["suggested_review_path"]).lower()
    for w in ("bypass","solve","evade","defeat"):
        assert w not in blob
    assert out["clean"] is True

def test_advisory_only():
    out=cc.classify({"text":"hcaptcha"})
    assert out["advisory"] is True and out["type"]=="hcaptcha"
