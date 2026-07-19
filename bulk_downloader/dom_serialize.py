"""Package-owned rrweb-node → HTML serializer.

``selector_playground.evaluate_selectors`` reads an HTML *string*, but a
capture stores its DOM as a tree of serialized rrweb nodes
(``data.node`` / ``data.adds[].node``), not HTML. The DOM Analyzer Workbench
(:mod:`bulk_downloader.dom_analyzer`) needs an HTML rendering of a capture's
(already-redacted) DOM so the operator can run selector tests against it.

The only existing serializer was ``tools/build_template_from_wacz._node_to_html``
— but that is a ``tools/`` CLI, and a package module importing *up* into
``tools/`` is a dependency inversion (``tools/`` depend on the package, never
the reverse, and ``tools/`` is not on the package import path at runtime). So
this is a small, self-contained, package-level equivalent. ``build_template_
from_wacz`` is left byte-identical; unifying it onto this serializer is a
separate, later refactor.

Structure only: it emits tag + attribute shapes. It performs **no** redaction
of its own — callers MUST serialize an already-redacted node tree (the
analyzer runs ``redact_dom_node`` + ``redact_artifact`` and proves the result
scans clean *before* handing a tree here). Serializing a raw node would emit
raw values; that is the caller's contract to prevent, enforced by the
analyzer's fail-closed gate.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# HTML void elements — no closing tag, no children emitted.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# rrweb NodeType: 0=Document, 1=DocumentType, 2=Element, 3=Text, 5=Comment.
_NT_ELEMENT = 2
_NT_TEXT = 3

# Recursion-depth guard. This serializer recurses once per DOM nesting level;
# a pathologically deep node tree (an old/external capture predating the export
# truncation cap, or operator-pasted HTML) would otherwise blow Python's
# recursion limit. 400 is far above any real DOM (~150) and above the ≤250-level
# ceiling a fresh capture is already truncated to, so real renders are unchanged;
# only a pathological tree is cut off. Mirrors capture_artifact_redact's cap.
_MAX_DOM_DEPTH = 400


def _escape_text(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _escape_attr(s: str) -> str:
    return _escape_text(s).replace('"', "&quot;")


# Structural-name validation (F-COREBD18-02). Text content and attribute VALUES
# are escaped above, but a tag NAME and attribute NAMES are interpolated into the
# tag directly, so a crafted/tampered node tree could otherwise inject raw HTML
# (a ``tagName`` of ``img src=x onerror=alert(1)`` or an attribute NAME of
# ``onload=alert(1) x``). Names are validated — not escaped — because an invalid
# HTML name has no escaped rendering; a non-conforming one is dropped instead.

# A serialized tag name is lowercased first, then matched against the HTML
# tag-name shape (a letter, then letters/digits/hyphens — the hyphen admits
# custom elements). Anything else is not a tag: its wrapper is dropped (children
# still emitted) rather than written out raw.
_VALID_TAG_NAME = re.compile(r"^[a-z][a-z0-9-]*$")

# An HTML attribute name may not contain whitespace, control characters, or any
# of " ' < > / = . A name outside that token set is dropped rather than emitted.
_VALID_ATTR_NAME = re.compile(r"^[^\s\x00-\x1f\"'<>/=]+$")


def nodes_to_html(node: Any, _depth: int = 0) -> str:
    """Serialize a serialized rrweb node (or node subtree) into an HTML
    fragment. Element/Text are emitted; Document/DocType/Comment wrappers are
    transparent (their children are emitted, the wrapper dropped). A non-dict
    or unrecognised node yields an empty string — never raises into the
    caller, which is rendering an operator-facing view. A subtree deeper than
    ``_MAX_DOM_DEPTH`` is cut off (deep-DOM guard).
    """
    if not isinstance(node, dict):
        return ""
    if _depth > _MAX_DOM_DEPTH:
        return ""
    ntype = node.get("type")
    if ntype == _NT_TEXT:
        return _escape_text(str(node.get("textContent") or ""))
    children = node.get("childNodes") or []
    if ntype == _NT_ELEMENT:
        tag = str(node.get("tagName") or "").lower()
        if not tag or not _VALID_TAG_NAME.match(tag):
            # No tag, or an invalid (potentially injecting) tag name: drop the
            # wrapper but still emit children so legitimate content is not lost.
            return "".join(nodes_to_html(c, _depth + 1) for c in children)
        parts = []
        for k, v in (node.get("attributes") or {}).items():
            name = str(k)
            if not _VALID_ATTR_NAME.match(name):
                # Drop an attribute whose name is not a valid HTML attribute
                # token — it would otherwise break out of the tag and inject markup.
                continue
            if v is True or v == "":
                parts.append(name)
            elif v is False or v is None:
                continue
            else:
                parts.append(f'{name}="{_escape_attr(str(v))}"')
        attr_str = (" " + " ".join(parts)) if parts else ""
        if tag in _VOID_TAGS:
            return f"<{tag}{attr_str}>"
        inner = "".join(nodes_to_html(c, _depth + 1) for c in children)
        return f"<{tag}{attr_str}>{inner}</{tag}>"
    # Document / DocType / Comment / unknown — recurse children, drop wrapper.
    return "".join(nodes_to_html(c, _depth + 1) for c in children)
