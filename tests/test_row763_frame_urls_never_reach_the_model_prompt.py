"""arm3 SAFETY CONTRACT: a frame URL must never reach the advisory LLM prompt.

`challenge_classify.classify` builds TWO blobs and only one of them is safe to
send anywhere:

    blob        text + title + markers   -> GOES TO A MODEL, as `blob[:1000]`
    frame_blob  observation["frame_urls"] -> `_detect_widget` ONLY

A frame URL carries a SIGNED TOKEN IN ITS QUERY STRING, so the separation of
those two names is the only thing keeping live tokens out of a third-party
prompt. Truncation is not a defence: a signed token sits EARLY in a query, not
late, so `blob[:1000]` would carry it. Folding `frame_blob` into `blob` "for
better classification" is a one-line change a reasonable person would make, it
ships live tokens off the host, and before this file every other test still
passed.

WHERE THIS ASSERTS, AND WHY THERE. It captures the prompt at `_call`, the
process boundary llm_exec actually hands to a provider -- not at `execute`, and
never at classify()'s return value. A return value cannot see what left the
process, and patching `execute` would step over `llm_exec`'s own
`looks_like_secret` guard, which is a SECOND line of defence and not this one.
The fixture token is deliberately shaped so that guard does NOT match it: its
patterns key on `api_key=`, `secret=`, `password=`, `access_token=`,
`auth_token=`, `bearer`, `client_secret=`, `private_key=`, `sk-`, `AKIA`,
`@cred:` and PEM headers, and a real widget signature is a bare `sig=`. So this
gate measures the SEPARATION, which is the only thing standing between a frame
URL and the provider -- not the DLP scanner that would not have fired anyway.
"""
import pytest

from bulk_downloader import challenge_classify

BD_GATE_SCOPE = "repo-wide"

# ZERO-ENTROPY FIXTURE CREDENTIAL. Every character is a repeat of one letter per
# group and the host is in the reserved .invalid TLD (RFC 2606), so this string
# is not a credential, cannot become one, and cannot resolve. Do NOT replace it
# with a realistic-looking token: a plausible fake in a test file is a
# secret-scanner incident. Named `sig` on purpose -- see the module docstring.
_FAKE_TOKEN = "AAAA-BBBB-CCCC-DDDD"
_FRAME_HOST = "challenges.cloudflare.invalid"
_FRAME_PATH = "/turnstile/v0/api.js"
_FRAME_URL = "https://%s%s?sig=%s" % (_FRAME_HOST, _FRAME_PATH, _FAKE_TOKEN)

# The POSITIVE CONTROL string. It lives in `text`, which IS meant to be
# summarized, so it MUST appear in the prompt. Without it a prompt that was
# never captured -- or captured empty -- would satisfy every absence assertion
# below, which is the most common way a boundary test is silently dead.
_CANARY = "ROW763-PROMPT-CANARY"


class _Reply:
    """The minimal shape llm_exec.execute requires of a provider result."""
    ok = True
    text = "a neutral summary"
    latency_ms = 1
    error = ""


@pytest.fixture
def observation():
    """text/title/markers deliberately match NO signature, so classification
    falls through to the frame-URL path -- the path this gate is about."""
    return {
        "text": "This page is holding the request. %s" % _CANARY,
        "title": "Please wait",
        "markers": "interstitial",
        "frame_urls": [_FRAME_URL],
    }


def test_row763_the_frame_url_and_its_token_never_enter_the_model_prompt(observation):
    prompts = []

    def _capture(prompt, **kwargs):
        prompts.append(prompt)
        return _Reply()

    out = challenge_classify.classify(observation, model="claude-x", _call=_capture)

    # PRECONDITION 1 -- the frame URL was actually consulted, so the hazard is
    # live in this run. Without this the test could pass on an observation whose
    # frame_urls were ignored entirely.
    assert out["type"] == "turnstile", (
        "the fixture no longer reaches _detect_widget (got type=%r); this gate is "
        "not measuring the frame-URL path any more" % out["type"])

    # PRECONDITION 2 -- exactly one prompt left the process. A zero here means
    # nothing was captured and every absence assertion below is vacuous; a two
    # means a retry, and the count is what the assertions are read against.
    assert len(prompts) == 1, (
        "expected exactly 1 prompt at the provider boundary, captured %d -- with 0 "
        "the absence assertions below prove nothing" % len(prompts))
    sent = prompts[0]

    # POSITIVE CONTROL -- the prompt is real and carries what it is supposed to.
    assert _CANARY in sent, (
        "the positive control %r is absent from the captured prompt, so the capture "
        "is not the prompt this gate thinks it is: %r" % (_CANARY, sent[:200]))

    # THE BOUNDARY. Token, host and path, each named separately so the failure
    # says WHICH part of the URL escaped.
    assert _FAKE_TOKEN not in sent, (
        "THE SIGNED TOKEN FROM A FRAME URL REACHED THE MODEL PROMPT. frame_blob has "
        "been folded into blob; blob[:1000] does not save you because a signed token "
        "sits early in a query string. prompt=%r" % sent)
    assert _FRAME_HOST not in sent, (
        "the frame URL's host reached the model prompt: %r" % sent)
    assert _FRAME_PATH not in sent, (
        "the frame URL's path reached the model prompt: %r" % sent)


def test_row763_the_two_blobs_are_built_from_disjoint_observation_keys():
    """The separation stated as source structure, so a fold is visible even to a
    reader who never runs the model path: `frame_urls` is read exactly once in
    the module, and not on the line that builds `blob`."""
    import inspect

    src = inspect.getsource(challenge_classify.classify)
    blob_lines = [ln for ln in src.splitlines() if ln.strip().startswith("blob = ")]
    assert len(blob_lines) == 1, (
        "expected exactly 1 assignment to `blob` in classify(), found %d: %r"
        % (len(blob_lines), blob_lines))
    assert "frame_urls" not in blob_lines[0], (
        "`frame_urls` is being read on the line that builds the LLM-bound blob: %r"
        % blob_lines[0])
    assert src.count("frame_urls") == 1, (
        "`frame_urls` is read %d times in classify(); this gate is written against "
        "exactly one read, on the frame_blob line" % src.count("frame_urls"))
