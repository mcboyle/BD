"""RED-first guard for F-CBD03-02.

element_pick._scrub_dom_excerpt strips credential VALUES from the live-DOM
excerpt before it is written to DOM_RESULT.json and surfaced to the SPA / pick
panel. Its ad-hoc _CRED_SUBS table covered only JWT / token= / Signature=, so
the CAP-01 I0008 floor (csrf, api_key, oauth code+state, Authorization header,
otp, nonce) leaked into the excerpt. The fix reuses the canonical floor
(capture_artifact_redact) plus DOM-specific passes (Authorization: headers and
<input>/<meta> values keyed by a floor-secret name), so no floor credential
survives -- while the tags / classes / data-* keys the selectors need stay.

Pre-fix: the floor forms below survive _scrub_dom_excerpt unredacted -> fails.
"""
from bulk_downloader import element_pick as ep

_LEAK = "SECRETVALUE0123456789"

# floor-class credential strings in the DOM forms the finding calls out
_FLOOR_FORMS = {
    "oauth_code":    f"https://site/cb?code={_LEAK}&state=X",
    "oauth_state":   f"https://site/cb?code=X&state={_LEAK}",
    "api_key":       f"https://api/v1?api_key={_LEAK}",
    "nonce":         f"https://api/v1?nonce={_LEAK}",
    "otp_query":     f"https://api/v1?otp={_LEAK}",
    "csrf_kv":       f"csrf={_LEAK}",
    "csrf_input":    f'<input name="csrf_token" value="{_LEAK}">',
    "otp_input":     f'<input type="text" name="otp" value="{_LEAK}">',
    "csrf_meta":     f'<meta name="csrf-token" content="{_LEAK}">',
    "authorization": f"Authorization: Bearer {_LEAK}",
}


def test_scrub_covers_i0008_floor_forms():
    leaked = [n for n, s in _FLOOR_FORMS.items() if _LEAK in ep._scrub_dom_excerpt(s)]
    assert not leaked, f"_scrub_dom_excerpt leaked floor credentials for: {leaked}"


def test_scrub_still_redacts_covered_controls():
    # classes already covered pre-fix must stay redacted (no regression)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sigblob"
    assert "eyJhbGci" not in ep._scrub_dom_excerpt(f"<span>{jwt}</span>")
    assert _LEAK not in ep._scrub_dom_excerpt(f"token={_LEAK}")


def test_scrub_preserves_selector_structure():
    # tags / classes / data-* keys the AI + selectors reason over must survive
    html = ('<div class="content_download"><button class="dl" id="go" '
            'data-kind="video" data-token="x">Go</button></div>')
    out = ep._scrub_dom_excerpt(html)
    for keep in ('class="content_download"', 'class="dl"', 'id="go"',
                 'data-kind="video"', 'data-token'):
        assert keep in out, f"scrub dropped structural token {keep!r}: {out}"


def test_scrub_fails_closed_on_error():
    # a scrub failure / empty input must return '' (never leak an unscrubbed blob)
    assert ep._scrub_dom_excerpt(None) == ""
    assert ep._scrub_dom_excerpt("") == ""
