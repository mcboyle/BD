"""Row 1033 deep lane (O809): RED spec for the failure advisor's matcher.

Written by the RED author (bd-worker-W5-A), not the builder. Sources:
RULING-2212-row1033-r2-BOUNCE.md (F2-B), and the refutes on the prior tree
f3c565bc (N2-B E1, N3-B A/B).

1. Numeric ids are not HTTP statuses (RULING-2212, HIGH). On fb49cd17 a
   status code matches on a digit boundary only, so "429.jpg",
   "/gallery/429", "photo-403.png" come back as rate-limit / auth with
   confidence "high" and a remedy command. The product's own status shape is
   "HTTP {status_code}" (runner_transport.py), which must keep classifying.
2. "authentic" is not an auth failure (RULING-2212). The exclusion has to
   stop at "authentic"/"authenticity": "authenticate"/"authentication" share
   that prefix and must stay auth (N2-B E1).
3. The fixes of the prior generations stay fixed: compound exceptions and
   the product's own shapes classify (N2-B E1, N3-B A), "author*" and
   "undergone" do not (row claim), and every row asserts its rule id so a
   hardcoded id cannot pass (N3-B B / mutant M2).

"HTTP429" / "e404" are deliberately absent: RULING-2212 drops them from the
positive corpus (not product shapes), and this spec does not pin them either
way.
"""
from __future__ import annotations

import pytest

from bulk_downloader.doctor import diagnose_failure

BD_GATE_SCOPE = "module"

_RULES = {"rate-limit", "captcha", "auth", "selector-drift", "disk",
          "network", "content-removed"}


def _diag(text):
    d = diagnose_failure(text)
    return d.get("rule"), d.get("confidence"), d


# ── 1 + 2: RED on fb49cd17 ───────────────────────────────────────────────
# Each string names a file, a path segment or an English word; none of them
# is evidence of throttling, auth or removal.
_NOT_A_DIAGNOSIS = (
    "GET /gallery/429 returned HTTP 500",
    "429.jpg",
    "photo-403.png saved (1.2 MB)",
    "/images/401/thumb.jpg could not be decoded",
    "clip-410.mp4 queued",
    "the page looks authentic but is empty",
    "authenticity check skipped",
)


@pytest.mark.parametrize("text", _NOT_A_DIAGNOSIS)
def test_numeric_id_or_authentic_is_not_a_confident_diagnosis(text):
    rule, conf, d = _diag(text)
    assert (rule, conf) == (None, "none"), (
        f"row1033 deep lane: {text!r} diagnosed as rule={rule!r} "
        f"confidence={conf!r} remedy={d.get('remedy')!r}; a numeric id / "
        f"'authentic' is not a status code or an auth failure")
    assert d.get("matched") is False and d.get("remedy") is None


def test_numeric_filename_does_not_shadow_the_real_cause():
    # F2-B: first match wins, so a 429 in a filename also HIDES the network
    # cause that the text actually states.
    rule, conf, _ = _diag("file 429.jpg failed: connection reset by peer")
    assert rule == "network", (
        f"row1033 deep lane: '429.jpg' shadowed the connection reset; "
        f"got rule={rule!r} confidence={conf!r}, expected network")


# ── Positive controls: must pass on fb49cd17 AND after the fix ──────────
_MUST_CLASSIFY = (
    # the product's status shape, runner_transport.py "HTTP {status_code}"
    ("HTTP 429", "rate-limit"),
    ("HTTP 429 Too Many Requests", "rate-limit"),
    ("chunk 3: HTTP 403 (no Range support?)", "auth"),
    ("HTTP 401 Unauthorized", "auth"),
    ("HTTP 404", "content-removed"),
    ("HTTP 410 Gone", "content-removed"),
    ("direct_http: HTTP 404 from https://example.test/v/1", "content-removed"),
    # N2-B E1: compound / CamelCase forms and the product's tag:Exception shapes
    ("Authentication failed", "auth"),
    ("authentication required", "auth"),
    ("could not authenticate", "auth"),
    ("authorization header missing", "auth"),
    ("AuthenticationError: invalid token", "auth"),
    ("OAuth token rejected", "auth"),
    ("LoginError: bad credentials", "auth"),
    ("TimeoutError", "network"),
    ("asyncio.TimeoutError", "network"),
    ("httpx.ConnectTimeout", "network"),
    ("NetworkError when attempting to fetch resource", "network"),
    ("OpenSSL error: certificate verify failed", "network"),
    ("SSLError: certificate verify failed", "network"),
    ("DNSError", "network"),
    ("browser_unavailable:TimeoutError", "network"),
    ("solver error: TimeoutError: ", "network"),
    ("waiting for selector .item timed out", "selector-drift"),
    # N3-B A: real needs_review text from runner.py / runner_teach.py
    ("Auto-teach: take over to teach download selectors. Click 'Take over' "
     "on this row, then complete the download in the popup browser and "
     "click 'I'm Done'.", "selector-drift"),
)


@pytest.mark.parametrize("text, expected", _MUST_CLASSIFY)
def test_real_failure_text_still_classifies_with_its_own_rule(text, expected):
    rule, conf, d = _diag(text)
    assert rule == expected, (
        f"row1033 deep lane: {text!r} -> rule={rule!r}, expected {expected!r}")
    assert d["matched"] is True and conf in ("high", "medium")


_STAYS_UNRECOGNIZED = (
    "GET /author/feed returned HTTP 500",
    "Upload failed: authored content rejected",
    "authority record missing",
    "the file has undergone re-encoding",
    "downloaded 4290 bytes",
    "seamlessly resumed",
)


@pytest.mark.parametrize("text", _STAYS_UNRECOGNIZED)
def test_prior_false_positives_stay_fixed(text):
    rule, conf, _ = _diag(text)
    assert (rule, conf) == (None, "none"), (
        f"row1033 deep lane: {text!r} -> rule={rule!r} confidence={conf!r}")


def test_every_diagnosis_carries_a_known_rule_and_remedy_shape():
    # Row acceptance: a stable rule id and a machine-readable remedy (or None)
    # on every answer, matched or not.
    for text, _ in _MUST_CLASSIFY:
        d = diagnose_failure(text)
        assert d["rule"] in _RULES, (text, d["rule"])
        assert d["remedy"] is None or set(d["remedy"]) == {"action", "command"}
    for text in _STAYS_UNRECOGNIZED + ("", None):
        d = diagnose_failure(text)
        assert (d["rule"], d["remedy"], d["matched"]) == (None, None, False)


# ── r4 (RULING-2258, N6-A E1/E2): RED on 7062e925 ────────────────────────
# E1: the product's own byte-count messages (runner_transport.py "truncated:
# received {downloaded} of {total} bytes", file_assembler.py "expected N
# bytes, copied M") carry standalone numbers that are not status codes.
# E2: multi-word and single-word signatures fired inside longer words.
_NOT_A_STATUS_OR_SIGNATURE = (
    "truncated: received 429 of 1048576 bytes (peer closed)",
    "parts/0003.bin: expected 403 bytes, copied 401",
    "the .part is 404 bytes but the server reports 1048576",
    "chunk 7: HTTP 500 (no Range support?) after 429 bytes",
    "HTTP 200 downloaded 429 bytes",
    "design in progress",
    "assign in batch 3 failed",
    "resign in queue",
    "slow downstream link",
    "accurate limit exceeded",
    "no spacecraft",
    "challenged by moderator",
    "diskette",
)


@pytest.mark.parametrize("text", _NOT_A_STATUS_OR_SIGNATURE)
def test_byte_count_or_word_fragment_is_not_a_confident_diagnosis(text):
    rule, conf, d = _diag(text)
    assert (rule, conf) == (None, "none"), (
        f"row1033 deep lane r4: {text!r} diagnosed as rule={rule!r} "
        f"confidence={conf!r} remedy={d.get('remedy')!r}; a byte count or a "
        f"signature inside a longer word is not evidence of that cause")
    assert d.get("matched") is False and d.get("remedy") is None


# Controls for r4: a status code in a status context, and the signatures as
# whole words / known exception suffixes, keep classifying.
_STATUS_CONTEXT_CLASSIFIES = (
    ("status 403, giving up", "auth"),
    ("code: 429", "rate-limit"),
    ("error 410 from origin", "content-removed"),
    ("HTTP/1.1 429 Too Many Requests", "rate-limit"),
    ("server said 401 Unauthorized", "auth"),
    ("rate limit exceeded, slow down", "rate-limit"),
    ("session expired, please sign in", "auth"),
    ("ENOSPC: no space left on device", "disk"),
    ("Turnstile captcha challenge detected", "captcha"),
    ("challenges remaining: 2", "captcha"),
    ("disk full", "disk"),
    # product text (app.py, failure_reasons.py): the right-hand boundary must
    # not cost the inflected forms the product actually emits
    ("rate limited, try again in 5 seconds", "rate-limit"),
    ("Rate limited by the site", "rate-limit"),
    ("rate limiting in effect", "rate-limit"),
)


@pytest.mark.parametrize("text, expected", _STATUS_CONTEXT_CLASSIFIES)
def test_status_context_and_whole_word_signatures_still_classify(text,
                                                                 expected):
    rule, conf, d = _diag(text)
    assert rule == expected, (
        f"row1033 deep lane r4: {text!r} -> rule={rule!r}, "
        f"expected {expected!r}")
    assert d["matched"] is True and conf in ("high", "medium")
