"""Wave 2 (F2) — input-value redaction in the persisted dom_log.

Two layers close the login-email + hidden-Turnstile cleartext leak:
  * rrweb maskAllInputs:true masks VISIBLE input values in-browser (email/text/
    password/typed) — proven behaviorally, browser-only, not re-tested here.
  * redact_dom_node (sink-side, always-on under redact) masks (A) any
    type=hidden input value, and (B) any token/secret-shaped input value,
    INDEPENDENT of PII class — because rrweb does not mask type=hidden and the
    class-gated path only fires on bd-/rr- classes.

These tests cover the sink-side layer (pure-Python, no browser) plus the
end-to-end record_dom_event ingest path. Structural attributes (id/name/type)
must survive so attribute-based selector derivation is unaffected.

Zero-arg test functions; repo root from __file__ (run_tests.py convention).
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader.dom_capture import redact_dom_node, DomCapture  # noqa: E402

_TOKEN = "eyJhbGciOiJIUzI1NiJ9.cf_turnstile_blob_abcdef1234567890"
_EMAIL = "secret.user@example.com"


def _input(attrs):
    return {"tagName": "input", "attributes": attrs, "childNodes": []}


def test_hidden_input_value_masked_regardless_of_class():
    out = redact_dom_node(_input({"type": "hidden",
                                  "name": "cf-turnstile-response", "value": _TOKEN}))
    assert _TOKEN not in json.dumps(out)
    assert out["attributes"]["value"] == "*" * 8
    assert out.get("_bd_redacted") == "input_value"


def test_token_shaped_value_masked_even_when_not_hidden():
    # (B): a JWT/email/opaque token in a text/email input is masked sink-side
    out = redact_dom_node(_input({"type": "text", "id": "ref", "value": _TOKEN}))
    assert _TOKEN not in json.dumps(out)
    out2 = redact_dom_node(_input({"type": "email", "id": "email", "value": _EMAIL}))
    assert _EMAIL not in json.dumps(out2)


def test_structural_attributes_survive_for_selectors():
    out = redact_dom_node(_input({"type": "hidden", "id": "email",
                                  "name": "cf-turnstile-response", "value": _TOKEN}))
    a = out["attributes"]
    assert a["id"] == "email"          # selector shape preserved
    assert a["name"] == "cf-turnstile-response"
    assert a["type"] == "hidden"


def test_non_secret_visible_input_left_to_rrweb():
    # A non-hidden, non-token input value is not a sink-side concern (rrweb
    # masks visible inputs in-browser); sink-side leaves it untouched.
    out = redact_dom_node(_input({"type": "text", "id": "q", "value": "cats"}))
    assert out["attributes"]["value"] == "cats"
    assert "_bd_redacted" not in out


def test_hidden_short_benign_value_still_masked():
    # (A) masks ALL hidden values regardless of content (conservative F2 choice).
    out = redact_dom_node(_input({"type": "hidden", "name": "page", "value": "3"}))
    assert out["attributes"]["value"] == "*"
    assert out.get("_bd_redacted") == "input_value"


def test_recurses_into_children():
    tree = {"tagName": "form", "attributes": {}, "childNodes": [
        _input({"type": "hidden", "name": "t", "value": _TOKEN})]}
    out = redact_dom_node(tree)
    assert _TOKEN not in json.dumps(out)


def test_end_to_end_record_dom_event_masks_hidden_token():
    # The real ingest path: rrweb emits an UNMASKED hidden input in the
    # full_snapshot (it does not mask type=hidden); record_dom_event must redact
    # it before it lands in dom_log.
    cap = DomCapture(url="https://app.example.com/login", redact=True)
    snapshot_node = {"tagName": "html", "attributes": {}, "childNodes": [
        {"tagName": "body", "attributes": {}, "childNodes": [
            _input({"type": "hidden", "name": "cf-turnstile-response", "value": _TOKEN}),
            _input({"type": "email", "id": "email", "value": _EMAIL}),
        ]}]}
    cap.record_dom_event(source=0, data={"node": snapshot_node}, is_full_snapshot=True)
    persisted = json.dumps(cap.dom_log)
    assert _TOKEN not in persisted, "Turnstile token reached dom_log"
    assert _EMAIL not in persisted, "email reached dom_log"
    # structural selector data still present
    assert "cf-turnstile-response" in persisted and '"id": "email"' in persisted


def test_dev_raw_mode_leaves_values():
    # Consistency with the redaction seam: the raw (bd_dev_inspect) path keeps
    # values, mirroring storage/network behavior.
    cap = DomCapture(url="https://app.example.com/login", redact=False)
    cap.record_dom_event(source=0,
                         data={"node": _input({"type": "hidden", "value": _TOKEN})},
                         is_full_snapshot=True)
    assert _TOKEN in json.dumps(cap.dom_log)
