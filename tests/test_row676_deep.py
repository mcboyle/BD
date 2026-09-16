"""Row 676 DEEP LANE adversary (O809). One RED test per REFUTE finding on
this cut's .review/ (current + superseded), plus adversarial CLASS probing
of the required-login-vs-trailer-copy exemption. Detection/routing only,
no login started, no site contacted. Author fixes to GREEN without
weakening or deleting any test here.
"""
from bulk_downloader import challenge_classify as cc

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# CURRENT open REFUTE (.review/VERDICT-shape.md, PATCH-SHA256 115f6276...):
# _is_public_trailer_sign_in_copy / _REQUIRED_LOGIN_WALL only recognizes
# "member login required" or bare "password"; any OTHER required-login
# signal (plain sign-in/log-in/authenticate) appended to trailer copy is
# wrongly exempted and loses operator handoff.
# ---------------------------------------------------------------------------

def test_trailer_copy_plus_sign_in_required_still_halts():
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Sign in required to view this video"),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_trailer_copy_plus_log_in_to_continue_still_halts():
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Log in to continue watching this scene"),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_trailer_copy_plus_please_authenticate_still_halts():
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Please authenticate to continue"),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


# ---------------------------------------------------------------------------
# HISTORICAL REFUTE regression guards (already closed on this tree; pinned
# here so a future edit cannot reopen them).
# ---------------------------------------------------------------------------

def test_regression_full_login_wall_alternation_present():
    """.review/superseded/VERDICT-correctness.f0791cfe8003b47c.md REFUTE:
    an earlier revision narrowed the login-wall signature to bare
    `\\bpassword\\b`, silently dropping sign-in/log-in/authenticate from
    detection everywhere (not only beside trailer copy)."""
    for text in (
        "Log in to continue watching this scene",
        "Sign in required to view this video",
        "Please authenticate to continue",
    ):
        routed = cc.route_challenge({"text": text, "title": "", "markers": [], "frame_urls": []})
        assert routed["challenge_present"] is True
        assert routed["type"] == "login-wall"
        assert "manual_handoff_required" in routed["labels"]


def test_regression_member_login_required_beats_trailer_exemption():
    """.review/superseded/VERDICT-shape.bc61900315099a46.md REFUTE DEFECT 1:
    a required login wall (member login required + password field) must
    still halt even when public-trailer affordance copy is also present."""
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Member login required to continue watching. Email address Password Sign in."),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_regression_trailer_exemption_survives_newline_layout():
    """.review/superseded/VERDICT-shape.bc61900315099a46.md REFUTE DEFECT 2:
    replacing the single-line separator with a newline must not flip the
    (correct) public-trailer exemption back to login-wall."""
    observation = {
        "text": "Watch the trailer.\nSign in to save this scene to your favourites.",
        "title": "Filthy Kings | Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


# ---------------------------------------------------------------------------
# ADVERSARIAL CLASS PROBING (input classes named by O809): whitespace/layout,
# casing/near-unicode, order-of-operations (title gate before/without text),
# empty/absent fields, and real-world variants not in the existing corpus.
# ---------------------------------------------------------------------------

def test_trailer_copy_plus_uppercase_sign_in_required_still_halts():
    """Casing: the exemption's own trailer-copy match is case-insensitive
    (re.I); the required-login side must not regain case-sensitivity."""
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "SIGN IN REQUIRED TO VIEW THIS VIDEO"),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"


def test_trailer_copy_plus_required_login_across_extra_whitespace():
    """Whitespace/layout: tabs and repeated spaces between trailer copy and
    the required-login phrase must not defeat detection."""
    observation = {
        "text": ("Watch the trailer.   Sign in to save this scene to your favourites.\t\t"
                 "Sign   in required to view this video"),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"


def test_required_login_before_trailer_copy_in_text_order():
    """Order of operations: the required-login phrase preceding the trailer
    affordance copy (not just following it) must still halt."""
    observation = {
        "text": ("Sign in required to view this video. Watch the trailer. "
                 "Sign in to save this scene to your favourites."),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"


def test_missing_title_field_does_not_grant_trailer_exemption():
    """Empty/absent fields: no title key at all must not satisfy the
    'public trailer' title gate, so a plain required-login page halts."""
    observation = {
        "text": "Sign in required to view this video",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_empty_observation_is_not_a_challenge():
    """Empty/absent fields: an all-empty observation must not be minted as
    a login-wall (would be worse than the row's own over-halting defect)."""
    observation = {"text": "", "title": "", "markers": [], "frame_urls": []}
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


def test_real_world_two_factor_code_beats_trailer_exemption():
    """Real-world variant: a 2FA code prompt is a required-login signal with
    neither 'member login required' nor the bare word 'password'."""
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Enter the 6-digit code sent to your phone. Sign in required to continue."),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_real_world_session_expired_beats_trailer_exemption():
    """Real-world variant: a session-expired re-auth wall is a required-login
    signal that carries neither exemption keyword."""
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Your session has expired. Please log in again to continue."),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_real_world_age_verification_login_beats_trailer_exemption():
    """Real-world variant: an age-verification sign-in wall (no 'password',
    no 'member login required') beside trailer copy."""
    observation = {
        "text": ("Watch the trailer. Sign in to save this scene to your favourites. "
                 "Verify your age. Sign in to confirm you are 18 or older."),
        "title": "Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]
