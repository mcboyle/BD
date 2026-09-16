"""P3-T12 -- challenge ROUTING + handoff invariants (detection/routing only).

Proves the routing layer over a detected challenge:
  * emits detection/routing labels (challenge_present / challenge_type_unknown /
    manual_handoff_required / passive_wait_timeout) and NOTHING else,
  * routes a passive-wait TIMEOUT (the site's own challenge did not self-clear)
    to manual operator handoff,
  * NEVER claims a challenge was solved (no code path here asserts "solved"),
  * NEVER emits solver/bypass instructions or a challenge-response value,
  * proves the redaction boundary stores NO raw challenge-response material.

NO SOLVING. route_challenge does no waiting, no widget interaction, no solving;
passive-wait facts are supplied BY THE CALLER from the real browser's normal
page-load wait. Runner-safe: zero-arg fns, repo root from __file__.
"""
import json
from pathlib import Path

from bulk_downloader import challenge_classify as cc
from bulk_downloader import capture_artifact_redact as car

_REPO = Path(__file__).resolve().parent.parent
_FIX = _REPO / "tests" / "corpus" / "challenge" / "challenge_solving_synthetic.cap.json"


def _load():
    return json.loads(_FIX.read_text(encoding="utf-8"))


def _obs(f):
    return {"text": f["dom_log"][0]["html"], "title": f.get("title", ""),
            "markers": " ".join(f.get("challenge_markers", []))}


# --- routing emits challenge_present for the synthetic fixture -------------- #
def test_route_emits_challenge_present():
    r = cc.route_challenge(_obs(_load()))
    assert r["challenge_present"] is True
    assert "challenge_present" in r["labels"]


# --- a generic interstitial (no specific widget) -> challenge_type_unknown -- #
def test_route_generic_interstitial_is_type_unknown():
    obs = {"text": "Just a moment... Checking your browser before accessing. Ray ID: 0000",
           "title": "Just a moment...", "markers": ""}
    r = cc.route_challenge(obs)
    assert r["challenge_present"] is True
    assert "challenge_type_unknown" in r["labels"]


# --- a non-challenge page emits NO routing labels -------------------------- #
def test_route_non_challenge_emits_nothing():
    r = cc.route_challenge({"text": "Just a normal video page", "title": "Home", "markers": ""})
    assert r["challenge_present"] is False
    assert r["labels"] == []


def test_widget_free_public_sign_in_copy_does_not_require_operator_action():
    observation = {
        "text": "Watch the trailer. Sign in to save this scene to your favourites.",
        "title": "Filthy Kings | Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    assert len(observation["text"].split()) == 12
    assert len(observation["frame_urls"]) == 0
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


def test_public_trailer_copy_does_not_suppress_required_login_wall():
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


def test_public_trailer_copy_preserves_all_appended_login_prompts():
    prefix = "Watch the trailer. Sign in to save this scene to your favourites. "
    for suffix in (
        "Log in to continue watching this scene",
        "Sign in required to view this video",
        "Please authenticate to continue",
    ):
        routed = cc.route_challenge({
            "text": prefix + suffix, "title": "Public trailer", "markers": [], "frame_urls": [],
        })
        assert routed["challenge_present"] is True
        assert routed["type"] == "login-wall"
        assert "manual_handoff_required" in routed["labels"]


def test_public_trailer_optional_sign_in_sentence_is_inert_alone():
    routed = cc.route_challenge({
        "text": "Sign in to save this scene to your favourites.",
        "title": "Public trailer", "markers": [], "frame_urls": [],
    })
    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


def test_public_trailer_sign_in_copy_is_layout_independent():
    observation = {
        "text": "Watch the trailer.\nSign in to save this scene to your favourites.",
        "title": "Filthy Kings | Public trailer",
        "markers": [],
        "frame_urls": [],
    }
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is False
    assert routed["type"] == "unknown"


def test_password_login_wall_still_requires_operator_action():
    observation = {
        "text": "Member login Email address Password Sign in",
        "title": "Member login",
        "markers": [],
        "frame_urls": [],
    }
    assert len(observation["text"].split()) == 7
    routed = cc.route_challenge(observation)
    assert routed["challenge_present"] is True
    assert routed["type"] == "login-wall"
    assert "manual_handoff_required" in routed["labels"]


def test_plain_login_prompts_still_require_operator_action():
    for text in (
        "Log in to continue watching this scene",
        "Sign in required to view this video",
        "Please authenticate to continue",
    ):
        routed = cc.route_challenge({"text": text, "title": "", "markers": [], "frame_urls": []})
        assert routed["challenge_present"] is True
        assert routed["type"] == "login-wall"
        assert "manual_handoff_required" in routed["labels"]


# --- passive-wait TIMEOUT -> manual handoff -------------------------------- #
def test_passive_wait_timeout_routes_to_manual_handoff():
    r = cc.route_challenge(_obs(_load()), passive_wait_timed_out=True)
    assert "passive_wait_timeout" in r["labels"]
    assert "manual_handoff_required" in r["labels"]


# --- a still-present challenge (pre-wait) routes to handoff ----------------- #
def test_present_challenge_routes_to_handoff():
    r = cc.route_challenge(_obs(_load()))
    assert "manual_handoff_required" in r["labels"]


# --- INVARIANT: routing NEVER claims solved -------------------------------- #
def test_routing_never_claims_solved():
    for kw in ({}, {"passive_wait_timed_out": True}):
        r = cc.route_challenge(_obs(_load()), **kw)
        assert r["solved"] is False
        assert "solved" not in r["labels"]


# --- INVARIANT: only the allowed label vocabulary is ever emitted ---------- #
def test_routing_emits_only_allowed_labels():
    allowed = set(cc.ROUTING_LABELS)
    for kw in ({}, {"passive_wait_timed_out": True}):
        r = cc.route_challenge(_obs(_load()), **kw)
        assert set(r["labels"]) <= allowed, f"unexpected label: {set(r['labels']) - allowed}"


# --- INVARIANT: no solver/bypass words or response values in routing out --- #
def test_routing_output_has_no_solver_or_bypass_content():
    r = cc.route_challenge(_obs(_load()), passive_wait_timed_out=True)
    blob = (" ".join(r["labels"]) + " " + r.get("suggested_review_path", "")).lower()
    for w in ("bypass", "solve", "evade", "defeat", "auto-submit", "token-harvest", "solver"):
        assert w not in blob, f"routing leaked forbidden word: {w!r}"
    assert r["clean"] is True


# --- INVARIANT: redaction stores NO raw challenge-response material --------- #
def test_no_raw_challenge_response_material_persists():
    red = car.redact_artifact(_load())
    blob = json.dumps(red)
    # distinctive bodies that appear ONLY inside the synthetic response values:
    for marker in ("TURNSTILE0RESPONSE0Ab3", "FAKE_SIG_not_real",
                   "CHALLENGE0TOKEN0Xy9", "FAKE_response_xxxx"):
        assert marker not in blob, f"raw challenge-response material survived: {marker!r}"
