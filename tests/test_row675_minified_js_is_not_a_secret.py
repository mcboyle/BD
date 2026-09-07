"""Row 675: JavaScript literals do not trip the artifact secret gate."""
BD_GATE_SCOPE = "repo-wide"

import pytest

from bulk_downloader.capture_artifact_redact import _value_findings
from bulk_downloader.dom_analyzer import redacted_dom


def test_javascript_literals_are_not_kv_secrets_but_credentials_remain_detected():
    assert _value_findings("for(var k=0;k<n;k++){}") == []
    assert _value_findings("a.k=null,b.k=void 0") == []
    assert "kv_secret" in _value_findings("k=0123456789abcdef")
    assert "kv_secret" in _value_findings("k=nullify0123456789abcdef")


def _capture_with_script(text):
    return {"dom_log": [{"data": {"node": {
        "type": 1, "tagName": "html", "attributes": {}, "childNodes": [
            {"type": 1, "tagName": "body", "attributes": {}, "childNodes": [
                {"type": 1, "tagName": "script", "attributes": {},
                 "childNodes": [{"type": 3, "textContent": text}]}]}]}}}]}


# The six minified shapes the row is about.  The first two are the literal
# forms the exemption covers; the last four assign IDENTIFIERS, which
# `key`, `code`, `state` and `token` make extremely common in real page
# script and which this cut deliberately does NOT exempt.
_LITERAL_VALUED = ("for(var k=0;k<n;k++){}", "a.k=null,b.k=void 0")
_IDENTIFIER_VALUED = ("var t=e.token,k=e.key;", "e.key=t",
                      "a.state=b.state", "n.code=r.code")


@pytest.mark.parametrize("script", _LITERAL_VALUED + _IDENTIFIER_VALUED)
def test_the_workbench_consumer_never_withholds_the_tree_for_minified_script(script):
    """Row 675's consumer acceptance: a DOM artifact whose only 'secret' is
    minified script must not make the redaction gate fail CLOSED.

    MEASURED, and recorded because it changes what this row can claim: this
    assertion holds on the DEFECTIVE BASE too, for all six shapes.  The
    fail-closed branch (dom_analyzer.redacted_dom, the residual branch) is
    never reached, because the value is SCRUBBED before it is scanned.  So
    this test is a standing guard on the consumer, not RED provenance -- the
    RED for this row is the detector assertion below.
    """
    result = redacted_dom(_capture_with_script(script))
    assert result["ok"] is True, result
    assert result["residual_count"] == 0
    assert result["tree"] is not None


@pytest.mark.parametrize("script", _LITERAL_VALUED)
def test_the_detector_exempts_a_literal_valued_minified_assignment(script):
    assert _value_findings(script) == []


@pytest.mark.parametrize("script", _IDENTIFIER_VALUED)
def test_an_identifier_valued_minified_assignment_still_trips_the_detector(script):
    """RESIDUAL, pinned deliberately: row 675 does NOT close on this cut.

    The exemption covers LITERAL values only.  `key`, `code`, `state` and
    `token` are in the anchored short-key set AND among the commonest
    minified property names, so ordinary page script still reads as a kv
    secret.  This assertion is the boundary, not an endorsement: the day a
    follow-up widens the predicate, this test is what tells us.
    """
    assert "kv_secret" in _value_findings(script)


@pytest.mark.parametrize("script", _LITERAL_VALUED)
def test_the_workbench_still_rewrites_the_literal_script_it_no_longer_flags(script):
    """RESIDUAL, second half, and the reason row 675's consequence is unmet.

    The exemption was added to the DETECTOR (_value_findings).  The DOM
    text path is scrubbed by a different pass, which still rewrites
    `k=0` to the placeholder -- so the workbench shows mangled script even
    for the two shapes this cut exempts.  Measured on this candidate, not
    predicted.  Pinned so the follow-up row has a starting assertion.
    """
    html = redacted_dom(_capture_with_script(script))["html"]
    assert script not in html
    assert "scrubbed" in html
