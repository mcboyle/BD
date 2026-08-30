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


RATE_LIMIT = {
    "title": "429 Too Many Requests",
    "text": "Sign in to raise your quota. Retry-After: 60 seconds.",
    "markers": "http_status=429",
}
CONSENT = {
    "title": "Members",
    "text": "Cookie consent",
    "markers": "control-label=Accept All Cookies",
}
INTERSTITIAL = {
    "url": "https://members.example.test/en/interstitial",
    "title": "Members",
    "text": "Special offer\nNo Thanks. Continue to Members Area\nSign In",
    "markers": "",
}


def test_three_non_captcha_gates_have_distinct_types_and_exact_safe_routes():
    cases = [
        (
            RATE_LIMIT,
            "rate-limit",
            "rate_limit_backoff",
            ["challenge_present", "rate_limit_backoff_required"],
        ),
        (
            CONSENT,
            "consent",
            "safe_consent_dismissal",
            ["challenge_present", "safe_consent_dismissal_required"],
        ),
        (
            INTERSTITIAL,
            "interstitial",
            "safe_interstitial_dismissal",
            [
                "challenge_present",
                "safe_interstitial_dismissal_required",
                "destination_re_request_required",
            ],
        ),
    ]

    assert len(cases) == 3
    assert all(observation["text"] for observation, *_ in cases)
    for observation, challenge_type, route, labels in cases:
        classified = cc.classify(observation)
        routed = cc.route_challenge(observation, passive_wait_timed_out=True)

        assert classified["type"] == challenge_type
        assert challenge_type in cc.CHALLENGE_TYPES
        assert routed["challenge_present"] is True
        assert routed["type"] == challenge_type
        assert routed["route"] == route
        assert routed["labels"] == labels
        assert "manual_handoff_required" not in routed["labels"]
        assert "passive_wait_timeout" not in routed["labels"]
        assert routed["clean"] is True


def test_specific_gate_signal_wins_over_incidental_login_language():
    assert "Sign in" in RATE_LIMIT["text"]
    assert "Sign In" in INTERSTITIAL["text"]

    assert cc.classify(RATE_LIMIT)["type"] == "rate-limit"
    assert cc.classify(INTERSTITIAL)["type"] == "interstitial"


def test_ordinary_prose_and_unsafe_exit_control_do_not_become_safe_gate_routes():
    controls = [
        {
            "title": "Download settings",
            "text": (
                "Set a download rate in Settings. Read our cookie policy, "
                "then continue watching the video."
            ),
            "markers": "",
        },
        {
            "title": "Welcome",
            "text": "I Disagree, Exit Here",
            "markers": "control-label=I Disagree, Exit Here",
        },
    ]

    assert len(controls) == 2
    for observation in controls:
        routed = cc.route_challenge(observation)
        assert routed["challenge_present"] is False
        assert routed["type"] == "unknown"
        assert routed["route"] == "none"
        assert routed["labels"] == []


def test_security_interstitial_stays_manual_unknown_not_safe_upsell_dismissal():
    observation = {
        "title": "Just a moment...",
        "text": "Checking your browser before accessing. Ray ID: 0000",
        "markers": "cf-chl",
    }

    routed = cc.route_challenge(observation)

    assert routed["challenge_present"] is True
    assert routed["type"] == "unknown"
    assert routed["route"] == "manual_handoff"
    assert routed["labels"] == [
        "challenge_present",
        "challenge_type_unknown",
        "manual_handoff_required",
    ]
