"""Row676 public-page and required-authentication state-machine acceptance."""
import re
import json
from pathlib import Path

import pytest

from bulk_downloader import challenge_classify as cc
from bulk_downloader import challenge_handling as ch

BD_GATE_SCOPE = "module"
OPTIONAL = "Sign in to save this scene to your favourites."
TITLE = "Example Studio | Public trailer"


PUBLIC = [
    pytest.param(OPTIONAL, TITLE, id="single-line"),
    pytest.param(OPTIONAL.replace("Sign in", "Sign\nin"), TITLE, id="newline"),
    pytest.param(OPTIONAL.replace(" ", "\u00a0"), TITLE, id="nbsp"),
    pytest.param(OPTIONAL.replace(" ", "\t"), TITLE, id="tabs"),
    pytest.param(OPTIONAL, "Second Studio | PUBLIC TRAILER", id="second-site"),
    pytest.param(OPTIONAL, "Example Studio | Free preview", id="second-template"),
    pytest.param(OPTIONAL, "", id="absent-title"),
    pytest.param(OPTIONAL + "\n" + OPTIONAL, TITLE, id="repeated-affordance"),
]


@pytest.mark.parametrize("copy,title", PUBLIC)
def test_public_video_stays_inert(copy, title):
    observation = dict(text="Watch the free public trailer.\n" + copy,
                       title=title, markers=[], frame_urls=[])
    assert re.search(r"sign\s+in", copy, re.I)
    assert len(observation) == 4 and observation["markers"] == observation["frame_urls"] == []
    routed = cc.route_challenge(observation, passive_wait_timed_out=True)
    handler = ch.ChallengeHandler(observation)
    assert handler.state is None, f"public video halted: {routed}"
    assert handler.history == [] and not handler.is_paused()
    assert routed["labels"] == [] and routed["challenge_present"] is False


WALLS = [
    pytest.param("Password required to continue", "", [], id="plain-password"),
    pytest.param("Log in to continue. " + OPTIONAL, TITLE, [], id="required-before"),
    pytest.param(OPTIONAL + " Log in to continue", TITLE, [], id="required-after"),
    pytest.param("Sign in to save favourites\nPassword required to continue\nFavourites",
                 TITLE, [], id="greedy-password"),
    pytest.param("Sign in to save favourites\nLog in to continue viewing favourites",
                 TITLE, [], id="greedy-login"),
    pytest.param("Sign in to save favourites\nPlease authenticate to view favourites",
                 TITLE, [], id="greedy-authenticate"),
    pytest.param(OPTIONAL, TITLE, ["Password required"], id="marker-auth"),
    pytest.param(OPTIONAL, TITLE + " - Password required", [], id="title-auth"),
    pytest.param(OPTIONAL + "\nLog\u00a0in to continue", TITLE, [], id="nbsp-auth"),
    pytest.param("Sign in to save\nPassword required\nfavourites", TITLE, [], id="inner-password"),
    pytest.param("Sign in to save\nLog in required\nfavourites", TITLE, [], id="inner-login"),
    pytest.param("Sign in to save\nPlease authenticate\nfavourites", TITLE, [], id="inner-authenticate"),
]


@pytest.mark.parametrize("copy,title,markers", WALLS)
def test_real_login_wall_reaches_operator(copy, title, markers):
    observation = dict(text=copy, title=title, markers=markers, frame_urls=[])
    assert re.search(r"password|log\s*in|authenticate", " ".join((copy, title, str(markers))), re.I)
    routed = cc.route_challenge(observation)
    handler = ch.ChallengeHandler(observation)
    assert routed["type"] == "login-wall", f"required authentication erased: {routed}"
    assert routed["labels"] == ["challenge_present", "manual_handoff_required"]
    handler.require_manual_handoff()
    result = handler.hand_off_to_operator()
    assert result["state"] == ch.OPERATOR_ACTION_REQUIRED
    assert handler.history == [ch.CHALLENGE_PRESENT, ch.MANUAL_HANDOFF_REQUIRED,
                               ch.OPERATOR_ACTION_REQUIRED]
    assert handler.is_paused() and not handler.solved


@pytest.fixture
def captured_landing():
    from bulk_downloader.session_capture import observe_page_for_challenge

    data = json.loads((Path(__file__).parent / "fixtures" /
                       "row676_public_landing.json").read_text())
    assert data["password_inputs"] == 0
    assert len(re.findall(r"\blogin\b", data["text"], re.I)) == 2
    assert "public trailer" not in data["text"].lower()
    class CapturedPage:
        url = "https://www.filthykings.com/"
        frames = []

        def title(self):
            return data["title"]

        def inner_text(self, selector):
            assert selector == "body"
            return data["text"]

    observation = observe_page_for_challenge(CapturedPage())
    assert observation["markers"] == observation["frame_urls"] == []
    return observation


@pytest.mark.parametrize("flatten", [False, True])
def test_measured_public_landing_has_no_operator_handoff(captured_landing, flatten):
    from bulk_downloader.session_capture import drive_challenge_handling

    if flatten:
        captured_landing["text"] = " ".join(captured_landing["text"].split())
    routed = cc.route_challenge(captured_landing, passive_wait_timed_out=True)
    handler = drive_challenge_handling(lambda: captured_landing, passive_budget_s=0)
    assert routed["type"] == "unknown", routed
    assert routed["labels"] == []
    assert handler.state is None and handler.history == []


@pytest.mark.parametrize("field,value,kind", [
    ("text", "Login required to continue", "login-wall"),
    ("text", "Login\nrequired", "login-wall"),
    ("text", "Login to view the video", "login-wall"),
    ("text", "Please login", "login-wall"),
    ("text", "You must login", "login-wall"),
    ("text", "Login first", "login-wall"),
    ("text", "Email address Password", "login-wall"),
    ("title", "Login", "login-wall"),
    ("markers", ["Login"], "login-wall"),
    ("markers", ["g-recaptcha"], "recaptcha"),
    ("frame_urls", ["https://challenges.cloudflare.com/widget"], "turnstile"),
])
def test_measured_landing_with_real_gate_still_halts(captured_landing, field, value, kind):
    from bulk_downloader.session_capture import drive_challenge_handling

    if field == "text":
        captured_landing[field] += "\n" + value
    else:
        captured_landing[field] = value
    handler = drive_challenge_handling(lambda: captured_landing, passive_budget_s=0)
    assert cc.classify(captured_landing)["type"] == kind
    assert handler.state == ch.OPERATOR_ACTION_REQUIRED
    assert handler.is_paused()
