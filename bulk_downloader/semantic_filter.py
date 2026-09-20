"""Semantic accessibility evaluator for web crawling and link extraction (Row 943).

Accessibility trees explicitly mark decorative, hidden, or non-interactive trap
anchors with `aria-hidden=true` and `tabindex=-1`.

This module evaluates elements and links, discarding elements concealed from
assistive navigation while preserving genuine navigable controls.
Fleet Rule 21 compliant: zero site logins touched.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Mapping, Optional, Sequence, Union


_ARIA_HIDDEN_ATTR_RE = re.compile(
    r'(?<![\w-])aria-hidden(?![\w-])(?:\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]*)))?', re.IGNORECASE)


def _aria_hidden_value_hides(val: Any) -> bool:
    """The attribute's VALUE, once present, hides unless it says "false"
    (or an explicit falsy value). A bare `aria-hidden` (HTMLParser yields
    None), `aria-hidden=""` and True all hide: the presence is the intent,
    and a filter that fails open on the bare form leaks every hidden trap."""
    if isinstance(val, bool):
        return val
    if val is None:
        return True
    text = str(val).strip().lower()
    return text not in ("false", "0", "no", "off")


def is_aria_hidden(element: Union[Mapping[str, Any], str, Any]) -> bool:
    """Check if an element is concealed via aria-hidden attribute.

    True when the attribute is present and not explicitly "false": that is
    aria-hidden="true", aria-hidden="1", aria-hidden="" and the bare boolean
    form `<a aria-hidden>`. Accepts attribute dictionaries (HTMLParser gives
    None for a bare attribute), HTML strings, or objects with a get().
    """
    if isinstance(element, str):
        m = _ARIA_HIDDEN_ATTR_RE.search(element)
        if not m:
            return False
        if m.group(1) is None and m.group(2) is None and m.group(3) is None:
            return True                                    # bare attribute
        val = next(g for g in m.groups() if g is not None)
        return _aria_hidden_value_hides(val)

    if isinstance(element, Mapping):
        for key in ("aria-hidden", "aria_hidden"):
            if key in element:
                return _aria_hidden_value_hides(element[key])
        return False

    # Object with attributes
    get = getattr(element, "get", None)
    if callable(get):
        sentinel = object()
        for key in ("aria-hidden", "aria_hidden"):
            val = get(key, sentinel)
            if val is not sentinel:
                return _aria_hidden_value_hides(val)
    return False


def is_focusable(element: Union[Mapping[str, Any], str, Any]) -> bool:
    """Check if an element is keyboard/assistive focusable.

    Returns False if tabindex is negative (e.g. tabindex="-1"), else True.
    """
    if isinstance(element, str):
        m = re.search(r'\btabindex\s*=\s*["\']?(-?\d+)', element, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1)) >= 0
            except ValueError:
                return True
        return True

    if isinstance(element, Mapping):
        val = element.get("tabindex")
        if val is None:
            return True
        try:
            return int(val) >= 0
        except (ValueError, TypeError):
            return True

    if hasattr(element, "get"):
        val = element.get("tabindex")
        if val is not None:
            try:
                return int(val) >= 0
            except (ValueError, TypeError):
                return True
    return True


def classify_element(element: Union[Mapping[str, Any], str, Any]) -> dict[str, Any]:
    """Return a detailed semantic accessibility classification for an element."""
    hidden = is_aria_hidden(element)
    focusable = is_focusable(element)

    if hidden:
        navigable = False
        reason = "aria-hidden element concealed from assistive tree"
    elif not focusable:
        navigable = False
        reason = "negative tabindex removes element from sequential navigation"
    else:
        navigable = True
        reason = "navigable interactive control"

    return {
        "navigable": navigable,
        "aria_hidden": hidden,
        "focusable": focusable,
        "reason": reason,
    }


def is_navigable(element: Union[Mapping[str, Any], str, Any]) -> bool:
    """True if the element is navigable (not aria-hidden and focusable)."""
    return classify_element(element)["navigable"]


def filter_navigable_links(
    elements: Sequence[Union[Mapping[str, Any], Any]]
) -> list[Union[Mapping[str, Any], Any]]:
    """Filter a sequence of link elements, preserving only navigable controls."""
    return [el for el in elements if is_navigable(el)]


class _AnchorExtractor(HTMLParser):
    """HTML parser extracting only accessible, navigable anchor tags."""

    def __init__(self) -> None:
        super().__init__()
        self.navigable_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if is_navigable(attr_dict):
            href = attr_dict.get("href")
            if href:
                self.navigable_urls.append(href)


def extract_navigable_urls(html_text: str) -> list[str]:
    """Parse HTML and extract URLs from all navigable <a> elements."""
    parser = _AnchorExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return parser.navigable_urls
