"""Row 918 -- ADAPTIVE-MULTI-STRATEGY-DOM-SELECTOR-FALLBACK-ENGINE.

``bulk_downloader.site_templates.accessors.resolve_selector_cascade`` tries
a prioritized list of DOM location strategies (CSS -> XPath -> text -> ARIA
-> ancestor, the register's hierarchy) against a Playwright-page-shaped
object, so a minor frontend
markup/class-name refactor that breaks a brittle CSS selector degrades to a
working fallback instead of failing abruptly. See BRIEF-CONFLICT.md in this
worktree: the register row's OWNS assigns this to
``site_templates/accessors.py`` even though the existing three functions
there are template-metadata CRUD with no DOM dependency; implemented here
per the brief's own OWNS line after two unanswered PM/triage reports, as a
self-contained pure function with no new import (duck-types the page
object), so relocating it later is a pure move, not a rewrite.

Acceptance (verbatim from the register row, which is canonical -- O982):
  (1) element resolved when primary CSS class is altered,
  (2) warning emitted to template maintenance log,
  (3) error raised if all strategies fail.
"""
from __future__ import annotations

import logging
import warnings

import pytest

from bulk_downloader.site_templates.accessors import (
    SelectorResolutionError,
    resolve_selector_cascade,
)

BD_GATE_SCOPE = "module"


class _FakeLocator:
    def __init__(self, n: int, page=None, ancestor=0):
        self._n, self._page, self._ancestor = n, page, ancestor

    def count(self) -> int:
        return self._n

    def locator(self, selector):
        """Playwright's Locator.locator: a relative selector from this
        element; the fake records it and resolves 'xpath=..' walks to the
        page's configured ancestor count."""
        if self._page is not None:
            self._page.calls.append(("relative", selector))
        # a chained locator on a non-matching base matches nothing, as in Playwright
        return _FakeLocator(self._ancestor if self._n and selector.startswith("xpath=..") else 0)


class _FakePage:
    """Duck-types just enough of Playwright's Page for the cascade: the
    three locator-producing methods it calls, each recording the call so
    tests can assert traversal order."""

    def __init__(self, css=0, xpath=0, text=0, role=0, anchor=0, ancestor=0):
        self._counts = {"css": css, "xpath": xpath, "text": text, "role": role, "anchor": anchor}
        self._ancestor = ancestor
        self.calls: list[tuple[str, str]] = []

    def locator(self, selector):
        if selector.startswith("xpath="):
            kind = "xpath"
        elif selector == _ANCHOR:
            kind = "anchor"                      # the ancestor strategy's stable descendant
        else:
            kind = "css"
        self.calls.append((kind, selector))
        return _FakeLocator(self._counts[kind], page=self, ancestor=self._ancestor)

    def get_by_text(self, value):
        self.calls.append(("text", value))
        return _FakeLocator(self._counts["text"])

    def get_by_role(self, role):
        self.calls.append(("role", role))
        return _FakeLocator(self._counts["role"])


_ANCHOR = "svg.icon-download"

_STRATEGIES = [
    {"type": "css", "value": ".download-btn"},
    {"type": "xpath", "value": "//button[@data-action='download']"},
    {"type": "text", "value": "Download"},
    {"type": "aria", "value": "button"},
    {"type": "ancestor", "value": _ANCHOR, "levels": 1},
]


def test_resolves_via_fallback_when_primary_class_drifts():
    """Acceptance 1: the primary CSS class is gone (count 0), XPath still
    matches -- resolution succeeds via the fallback, not an exception."""
    page = _FakePage(css=0, xpath=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        locator = resolve_selector_cascade(page, _STRATEGIES)
    assert locator.count() == 1


def test_primary_strategy_succeeding_needs_no_fallback():
    """Negative control: when CSS resolves, later strategies are never tried."""
    page = _FakePage(css=1, xpath=1, text=1, role=1)
    locator = resolve_selector_cascade(page, _STRATEGIES)
    assert locator.count() == 1
    assert page.calls == [("css", ".download-btn")]


def test_fallback_traversal_order_is_css_xpath_text_aria_then_ancestor():
    """The register's hierarchy: every strategy failing except the last --
    traversal order is exactly CSS -> XPath -> text -> ARIA -> ancestor,
    one call each; the ancestor strategy locates the stable descendant and
    walks up one level."""
    page = _FakePage(css=0, xpath=0, text=0, role=0, anchor=1, ancestor=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        locator = resolve_selector_cascade(page, _STRATEGIES)
    assert locator.count() == 1
    assert page.calls == [
        ("css", ".download-btn"),
        ("xpath", "xpath=//button[@data-action='download']"),
        ("text", "Download"),
        ("role", "button"),
        ("anchor", _ANCHOR),
        ("relative", "xpath=.."),
    ]


def test_ancestor_strategy_resolves_the_container_of_a_stable_child():
    """The reviewer's escape: four misses plus an available ancestor must
    resolve, not raise. levels=2 walks two levels; a missing anchor is a
    miss (SelectorResolutionError), not a crash; levels<1 is refused."""
    page = _FakePage(anchor=1, ancestor=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert resolve_selector_cascade(page, _STRATEGIES).count() == 1
        two = _FakePage(anchor=1, ancestor=1)
        resolve_selector_cascade(two, [{"type": "ancestor", "value": _ANCHOR, "levels": 2}])
        assert two.calls == [("anchor", _ANCHOR), ("relative", "xpath=../..")]
        with pytest.raises(SelectorResolutionError):
            resolve_selector_cascade(_FakePage(anchor=0, ancestor=1), _STRATEGIES)
        with pytest.raises(SelectorResolutionError):
            resolve_selector_cascade(_FakePage(anchor=1, ancestor=1),
                                     [{"type": "ancestor", "value": _ANCHOR, "levels": 0}])


def test_diagnostic_warning_emitted_to_the_maintenance_log_only_on_fallback(caplog):
    """Acceptance 2: falling back to a non-primary strategy emits the
    warning to the template maintenance log (a WARNING record on
    bulk_downloader.site_templates.maintenance) and to warnings.warn;
    resolving on the primary strategy emits neither."""
    page = _FakePage(css=0, xpath=1)
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.site_templates.maintenance"):
        with pytest.warns(UserWarning, match="fell back"):
            resolve_selector_cascade(page, _STRATEGIES)
    records = [r for r in caplog.records if r.name == "bulk_downloader.site_templates.maintenance"]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "fell back to strategy #1" in records[0].getMessage() and ".download-btn" in records[0].getMessage()

    caplog.clear()
    page2 = _FakePage(css=1)
    with caplog.at_level(logging.WARNING, logger="bulk_downloader.site_templates.maintenance"):
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning here fails the test
            resolve_selector_cascade(page2, _STRATEGIES)
    assert [r for r in caplog.records if r.name == "bulk_downloader.site_templates.maintenance"] == []


def test_all_strategies_failing_raises_named_error():
    page = _FakePage()  # every count 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(SelectorResolutionError):
            resolve_selector_cascade(page, _STRATEGIES)


def test_empty_strategy_list_is_refused_not_a_silent_none():
    with pytest.raises(SelectorResolutionError):
        resolve_selector_cascade(_FakePage(), [])


def test_a_locator_whose_count_raises_is_treated_as_not_found():
    """A stale/detached locator can raise instead of returning 0; that must
    not crash the cascade, only skip to the next strategy."""

    class _BoomLocator:
        def count(self):
            raise RuntimeError("execution context was destroyed")

    class _BoomThenOkPage(_FakePage):
        def locator(self, selector):
            if selector == ".download-btn":
                self.calls.append(("css", selector))
                return _BoomLocator()
            return super().locator(selector)

    page = _BoomThenOkPage(xpath=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        locator = resolve_selector_cascade(page, _STRATEGIES)
    assert locator.count() == 1
