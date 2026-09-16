"""Exercise the DOM consumer and literal/identifier boundary independently."""
from html import escape

import pytest

from bulk_downloader.capture_artifact_redact import _value_findings, redact_value
from bulk_downloader.dom_analyzer import redacted_dom

BD_GATE_SCOPE = "module"


def _capture(text, parent="body", tag="script"):
    node = {"type": 1, "tagName": tag, "attributes": {},
            "childNodes": [{"type": 3, "textContent": text}]}
    for wrapper in (parent, "html"):
        node = {"type": 1, "tagName": wrapper, "attributes": {}, "childNodes": [node]}
    return {"dom_log": [{"data": {"node": node}}]}


@pytest.mark.parametrize("parent", ["body", "head"])
@pytest.mark.parametrize("script", ["for(var k=0;k<n;k++){}", "a.k=null,b.k=void 0"])
def test_literal_script_survives_the_complete_dom_rewriter(parent, script):
    result = redacted_dom(_capture(script, parent))
    assert result["ok"] and result["residual_count"] == 0
    assert result["tree"] is not None
    assert escape(script) in result["html"]
    assert "scrubbed" not in result["html"]


@pytest.mark.parametrize("script", [
    "var t=e.token,k=e.key;", "e.key=t", "a.state=b.state", "n.code=r.code",
    "k=null\u03bb;", "k=true\u03c0;", "k=void\u03b1;", "k=123\u03b4;", r"k=null\u0061;",
    "k=null\u0301;", "k=true\u200c;", "k=void\u200d;",
])
def test_identifier_assignments_remain_detected_and_rewritten(script):
    assert "kv_secret" in _value_findings(script), "identifier misclassified as literal"
    result = redacted_dom(_capture(script))
    assert result["ok"] and result["residual_count"] == 0
    assert "scrubbed" in result["html"]
    assert escape(script) not in result["html"]


@pytest.mark.parametrize("tag", ["div", "p"])
@pytest.mark.parametrize("secret", [
    "fixture_not_a_real_credential", "null-fixture_secret", "123-fixture_secret",
])
def test_real_dom_secret_is_still_removed(tag, secret):
    result = redacted_dom(_capture("apikey=" + secret, tag=tag))
    assert result["ok"] and result["residual_count"] == 0
    assert secret not in result["html"]
    assert "scrubbed" in result["html"]


def test_signed_query_redaction_precedes_literal_exemption():
    text = "https://fixture.invalid/video?token=0&format=mp4"
    redacted = redact_value(text)
    assert redacted != text
    assert "token=0" not in redacted
    assert "fixture.invalid/video" in redacted
