"""Consolidated DOM-walker depth cap (rec #1).

`dom_serialize.nodes_to_html`, `dom_analyzer._mask_all_text` / `_propagate_mask`,
and the builder's `_node_to_html` / `_walk_node` recursed once per DOM nesting
level with no bound — a deeply-nested node tree (an old/external capture
predating the export truncation cap, or operator-pasted HTML) blew Python's
recursion limit. They now share a depth cap (`_MAX_DOM_DEPTH` / `_MAX_NODE_DEPTH`
= 400). Real DOM (~150) and the ≤250-level truncated-capture ceiling are well
under the cap, so real renders are unchanged; a pathological tree is cut off
instead of crashing. Pristine-source RED was demonstrated against the prior
release (deep `nodes_to_html` raised RecursionError).
"""

import importlib.util
from pathlib import Path


def _deep_nodes(n):
    root = {"type": 2, "tagName": "div", "attributes": {}, "childNodes": []}
    cur = root
    for _ in range(n):
        c = {"type": 2, "tagName": "div", "attributes": {}, "childNodes": []}
        cur["childNodes"] = [c]
        cur = c
    return root


def test_dom_serialize_nodes_to_html_bounded():
    from bulk_downloader.dom_serialize import nodes_to_html
    try:
        out = nodes_to_html(_deep_nodes(5000))
    except RecursionError:
        raise AssertionError("nodes_to_html RecursionError on a deep node tree")
    assert isinstance(out, str)


def test_dom_serialize_shallow_render_unchanged():
    from bulk_downloader.dom_serialize import nodes_to_html
    node = {"type": 2, "tagName": "a",
            "attributes": {"href": "https://x/", "class": "dl"},
            "childNodes": [{"type": 3, "textContent": "Download"}]}
    html = nodes_to_html(node)
    assert html == '<a href="https://x/" class="dl">Download</a>', html


def test_dom_analyzer_mask_walkers_bounded():
    from bulk_downloader.dom_analyzer import _mask_all_text, _propagate_mask
    try:
        _mask_all_text(_deep_nodes(5000))
        _propagate_mask(_deep_nodes(5000))
    except RecursionError:
        raise AssertionError("dom_analyzer mask walk RecursionError on a deep tree")


def test_builder_node_to_html_bounded():
    # Load the builder module from file (tools/ is not on the package path).
    root = Path(__file__).resolve().parent.parent
    p = root / "tools" / "build_template_from_wacz.py"
    spec = importlib.util.spec_from_file_location("_btw_capdepth", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try:
        out = m._node_to_html(_deep_nodes(5000))
    except RecursionError:
        raise AssertionError("builder _node_to_html RecursionError on a deep tree")
    assert isinstance(out, str)
