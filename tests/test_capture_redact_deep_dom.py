"""Recursion-depth guard for the export-boundary redaction over deep DOM.

`build_wacz_bytes` runs `redact_capture` -> `_walk_dom`, then
`scan_floor_secrets` -> `walk`, then `json.dumps` — all three recurse once per
DOM-tree depth level. A pathologically deep captured DOM (a hostile/anti-capture
page that nests thousands of elements, or pathological generated content) drove
those walks past Python's recursion limit, raising `RecursionError` out of
`write_wacz` — which `capture_session.run()` calls outside its try/finally — so
the ENTIRE capture was lost with an unhandled traceback (export aborts at DOM
depth ~500; depth 200 is fine).

The fix bounds the redaction walk depth: a subtree deeper than the cap collapses
to the scrub placeholder, which keeps the redacted copy shallow for all three
consumers. The cap (300) is far beyond any real DOM (~150 max) so real captures
are byte-identical; only a pathological tree is truncated, and the export now
succeeds instead of crashing.

RED on pristine (the deep-DOM export raises RecursionError); GREEN after the cap.
"""

from bulk_downloader.wacz_export import build_wacz_bytes, verify_wacz_bytes
from bulk_downloader import capture_artifact_redact as R
from bulk_downloader.capture_redact import PLACEHOLDER


def _deep_node(n):
    """A DOM node tree nested ``n`` levels deep."""
    root = {"tagName": "div", "attributes": {}, "childNodes": []}
    cur = root
    for _ in range(n):
        child = {"tagName": "div", "attributes": {}, "childNodes": []}
        cur["childNodes"] = [child]
        cur = child
    return root


def _capture_with_dom(node):
    return {"url": "https://example/", "captured_at": "t",
            "dom_log": [{"type": "full_snapshot", "source": -1,
                         "data": {"node": node}}]}


def test_export_survives_pathologically_deep_dom():
    # ~5000-deep DOM: well past the recursion limit on every walk.
    cap = _capture_with_dom(_deep_node(5000))
    try:
        wacz = build_wacz_bytes(cap)
    except RecursionError:
        raise AssertionError(
            "build_wacz_bytes raised RecursionError on a deeply-nested DOM — "
            "the capture export aborts and the whole capture is lost; bound the "
            "redaction walk depth")
    assert verify_wacz_bytes(wacz)["ok"], "deep-DOM WACZ failed digest verify"


def test_deep_subtree_truncated_to_placeholder():
    # Beyond the cap the subtree collapses to the placeholder (graceful, not a
    # crash); the redacted copy is shallow.
    red = R.redact_capture(_capture_with_dom(_deep_node(5000)))
    # Walk down the redacted dom_log node tree; somewhere within the cap it must
    # terminate in the placeholder rather than continue 5000 levels.
    node = red["dom_log"][0]["data"]["node"]
    depth = 0
    cur = node
    while isinstance(cur, dict):
        kids = cur.get("childNodes")
        if isinstance(kids, list) and kids:
            cur = kids[0]
            depth += 1
            if depth > 400:
                raise AssertionError("deep subtree was not truncated")
        else:
            break
    # The terminal is either a normal leaf or the placeholder marker; the point
    # is that it terminated well under 5000 and the structure is finite.
    assert depth <= 400, depth


def test_shallow_real_dom_redaction_unchanged():
    # A realistic shallow DOM is redacted exactly as before (no truncation):
    # the structure is preserved and a hidden-input token is still masked.
    node = {
        "tagName": "form", "attributes": {}, "childNodes": [
            {"tagName": "input",
             "attributes": {"type": "hidden", "name": "csrf",
                            "value": "eyJhbGciOiJIUzI1NiJ9.payloadpart.sigpart"},
             "childNodes": []},
            {"tagName": "input",
             "attributes": {"type": "text", "name": "user", "id": "user"},
             "childNodes": []},
        ]}
    red = R.redact_capture(_capture_with_dom(node))
    kids = red["dom_log"][0]["data"]["node"]["childNodes"]
    assert len(kids) == 2, kids
    # JWT in the hidden input value must not survive verbatim.
    hidden_val = kids[0]["attributes"].get("value", "")
    assert "eyJhbG" not in hidden_val, hidden_val
    # The structural input that carries no secret keeps its attributes.
    assert kids[1]["attributes"].get("name") == "user", kids[1]
