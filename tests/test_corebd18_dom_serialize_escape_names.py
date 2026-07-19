"""RED-first repro for F-COREBD18-02.

``dom_serialize.nodes_to_html`` escapes TEXT content and attribute VALUES but
emits the TAG NAME and ATTRIBUTE NAMES unescaped, so a crafted/tampered rrweb
node tree injects raw HTML (a ``tagName`` of ``img src=x onerror=alert(1)`` or an
attribute NAME of ``onload=alert(1) x`` breaks out of the intended structure).

After the injection-hardening fix, an invalid tag name has its wrapper dropped
(children still emitted) and an attribute whose name is not a valid HTML
attribute token is skipped, while legitimate structure — and the existing
attribute-VALUE escaping — renders exactly as before.

Pristine-source RED: the three malicious node trees below emit raw markup, so
the "not in" assertions fail until names are validated.
"""
from bulk_downloader.dom_serialize import nodes_to_html


def test_tag_and_attr_names_are_escaped_or_validated():
    # (1) a malicious TAG NAME must not inject raw markup
    evil_tag = {"type": 2, "tagName": "img src=x onerror=alert(1)",
                "attributes": {}, "childNodes": []}
    out_tag = nodes_to_html(evil_tag)
    assert "onerror=alert(1)" not in out_tag, out_tag
    assert "<img src=x" not in out_tag, out_tag

    # (2) a malicious ATTRIBUTE NAME (value form) must not inject raw markup
    evil_attr = {"type": 2, "tagName": "div",
                 "attributes": {"onload=alert(1) x": "y"}, "childNodes": []}
    out_attr = nodes_to_html(evil_attr)
    assert "onload=alert(1)" not in out_attr, out_attr

    # (3) a malicious ATTRIBUTE NAME (boolean form) must not inject raw markup
    evil_bool = {"type": 2, "tagName": "div",
                 "attributes": {"x><script>alert(1)</script>": True},
                 "childNodes": []}
    out_bool = nodes_to_html(evil_bool)
    assert "<script>" not in out_bool, out_bool

    # (4) an invalid tag wrapper is dropped but its children still render
    evil_wrap = {"type": 2, "tagName": "bad tag>",
                 "attributes": {},
                 "childNodes": [{"type": 3, "textContent": "keep me"}]}
    assert nodes_to_html(evil_wrap) == "keep me", nodes_to_html(evil_wrap)

    # (5) regression: legitimate tag + attributes render unchanged; VALUE escaping preserved
    ok = {"type": 2, "tagName": "a",
          "attributes": {"href": "https://x/", "data-id": "5"},
          "childNodes": [
              {"type": 3, "textContent": "Download"},
              {"type": 2, "tagName": "span",
               "attributes": {"title": '"><b>'}, "childNodes": []},
          ]}
    out_ok = nodes_to_html(ok)
    assert out_ok.startswith('<a href="https://x/" data-id="5">Download'), out_ok
    assert '&quot;&gt;&lt;b&gt;' in out_ok, out_ok
