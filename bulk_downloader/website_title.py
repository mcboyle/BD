"""Harvest the name shown by a scene page without inventing one from disk."""
from __future__ import annotations

import html as _html
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
    """Decode HTML entities, collapse page whitespace, bound the value.

    Sites that entity-escape their <title>/og:title source (e.g. beeg:
    "&amp;lbrack;AI-generated&amp;rsqb;") surface the raw "&lbrack;" text
    in the DOM. Decoding here keeps entities out of the saved filename
    without touching titles that carry no entities."""
    if not isinstance(value, str):
        return ""
    return " ".join(_html.unescape(value).split())[:_TITLE_LIMIT]


def harvest_title_candidates(
    page, *, listing_title: str = ""
) -> list[tuple[str, str]]:
    """Every non-empty ``(raw_title, source)`` in the operator-required order.

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

    document_title = _clean_title(values.get("document_title"))
    if not document_title:
        try:
            document_title = _clean_title(page.title())
        except Exception:
            document_title = ""
    candidates = [
        (_clean_title(values.get("og_title")), "og:title"),
        (document_title, "document.title"),
        (_clean_title(values.get("h1")), "h1"),
        (_clean_title(listing_title), "listing_card"),
    ]
    return [(value, source) for value, source in candidates if value]


def harvest_page_title(page, *, listing_title: str = "") -> tuple[str, str]:
    """Return the first ``(raw_title, source)`` in the operator-required order."""
    candidates = harvest_title_candidates(page, listing_title=listing_title)
    return candidates[0] if candidates else ("", "")


def choose_scene_title(
    candidates: Iterable[tuple[str, str]],
    other_scene_titles: Mapping | Iterable[str],
) -> tuple[str, str]:
    """Return ``(title, source)``: the first candidate that names the scene.

    fx-newsensations-generic-title: a whole title that another scene of the
    site repeats verbatim ("New Sensations Premium Access" on every members
    page) names the site, not the scene, so the next source (h1, then the
    listing card) is used. With no other source the title is empty rather
    than the site's name. A partly repeated title keeps the template rule.
    """
    others = {value.casefold() for value in _observation_values(other_scene_titles)}
    for value, source in candidates:
        value = _clean_title(value)
        if not value or value.casefold() in others:
            continue
        return strip_repeated_title_template(value, other_scene_titles), source
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
