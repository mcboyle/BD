"""Harvest the name shown by a scene page without inventing one from disk."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


_TITLE_LIMIT = 1000
_TEMPLATE_SEPARATOR = re.compile(r" (?:/|\||-) ")

_PAGE_TITLE_JS = """() => {
    const og = document.querySelector(
        'meta[property="og:title"], meta[name="og:title"]'
    );
    const heading = document.querySelector('h1');
    return {
        og_title: og ? (og.getAttribute('content') || '') : '',
        document_title: document.title || '',
        h1: heading ? (heading.textContent || '') : '',
    };
}"""


def _clean_title(value) -> str:
    """Collapse page whitespace and bound the value stored in SQLite."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:_TITLE_LIMIT]


def harvest_page_title(page, *, listing_title: str = "") -> tuple[str, str]:
    """Return ``(raw_title, source)`` in the operator-required order.

    The detail page is already open, so this reads the DOM only and performs no
    navigation or network work. A failed DOM evaluation still tries Playwright's
    dedicated ``page.title()`` accessor before falling through to the listing
    card supplied by the caller.
    """
    values: dict = {}
    try:
        measured = page.evaluate(_PAGE_TITLE_JS)
        if isinstance(measured, dict):
            values = measured
    except Exception:
        values = {}

    og_title = _clean_title(values.get("og_title"))
    if og_title:
        return og_title, "og:title"

    document_title = _clean_title(values.get("document_title"))
    if not document_title:
        try:
            document_title = _clean_title(page.title())
        except Exception:
            document_title = ""
    if document_title:
        return document_title, "document.title"

    h1 = _clean_title(values.get("h1"))
    if h1:
        return h1, "h1"

    listing = _clean_title(listing_title)
    if listing:
        return listing, "listing_card"
    return "", ""


def history_title_kwargs(runner, url: str) -> dict[str, str]:
    """Resolve completion kwargs without ever costing the history write.

    Runner mixins are also used directly by adapters and lightweight test
    hosts. Those callers may not inherit :class:`SiteRunner`'s title methods;
    the pre-existing completion contract must remain intact for them.
    """
    resolver = getattr(runner, "_history_title_fields", None)
    if not callable(resolver):
        return {}
    try:
        fields = resolver(url)
    except Exception:
        return {}
    if not isinstance(fields, dict):
        return {}
    return {
        "title": _clean_title(fields.get("title")),
        "title_source": _clean_title(fields.get("title_source")),
    }


def _observation_values(observations: Mapping | Iterable[str]) -> list[str]:
    values = observations.values() if isinstance(observations, Mapping) else observations
    cleaned = (_clean_title(value) for value in values)
    return [value for value in cleaned if value]


def strip_repeated_title_template(
    raw_title: str,
    prior_scene_titles: Mapping | Iterable[str],
) -> str:
    """Strip only a leading template repeated on another scene from the site.

    Candidate splits are considered from right to left, so the longest repeated
    leading sequence wins. If only ``"Brand | "`` repeats, a legitimate suffix
    such as ``"Movie - Part Two"`` remains intact; a dash is never stripped
    merely because it appears in one title.
    """
    title = _clean_title(raw_title)
    if not title:
        return ""
    prior = _observation_values(prior_scene_titles)
    if not prior:
        return title

    matches = list(_TEMPLATE_SEPARATOR.finditer(title))
    for match in reversed(matches):
        prefix = title[: match.start()].strip()
        suffix = title[match.end() :].strip()
        separator = match.group(0)
        if not prefix or not suffix:
            continue
        expected_start = (prefix + separator).casefold()
        for other in prior:
            if other.casefold() == title.casefold():
                continue
            if not other.casefold().startswith(expected_start):
                continue
            other_suffix = other[len(prefix + separator) :].strip()
            if other_suffix and other_suffix.casefold() != suffix.casefold():
                return suffix
    return title
