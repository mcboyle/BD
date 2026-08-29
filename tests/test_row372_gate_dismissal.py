"""Row 372: clear layered content gates without ever choosing an exit."""
from __future__ import annotations

import pytest


BD_GATE_SCOPE = "module"


class _Control:
    def __init__(self, text: str, destination: str | None = None):
        self.text = text
        self.destination = destination
        self.visible = True


class _Locator:
    def __init__(self, page, selector: str):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    @property
    def _control(self):
        return self._page.controls.get(self._selector)

    def count(self):
        return int(bool(self._control and self._control.visible))

    def is_visible(self):
        return bool(self._control and self._control.visible)

    def inner_text(self):
        if not self._control:
            raise AssertionError("inner_text called for an absent control")
        return self._control.text

    def click(self, **kwargs):
        control = self._control
        if not control or not control.visible:
            raise AssertionError("click called for an absent control")
        self._page.clicked.append((self._selector, kwargs))
        control.visible = False
        if control.destination:
            self._page._navigate(control.destination)


class _Page:
    def __init__(self, url: str, controls: dict[str, _Control]):
        self.url = url
        self.controls = controls
        self.clicked: list[tuple[str, dict]] = []
        self.goto_calls: list[tuple[str, dict]] = []
        self.go_back_calls: list[dict] = []
        self._history = [url]

    def locator(self, selector: str):
        return _Locator(self, selector)

    def _navigate(self, url: str):
        self.url = url
        self._history.append(url)

    def goto(self, url: str, **kwargs):
        self.goto_calls.append((url, kwargs))
        self._navigate(url)

    def go_back(self, **kwargs):
        self.go_back_calls.append(kwargs)
        if len(self._history) < 2:
            raise AssertionError("go_back called without navigation history")
        self._history.pop()
        self.url = self._history[-1]

    def visible(self, selector: str) -> bool:
        control = self.controls.get(selector)
        return bool(control and control.visible)


def _clear(page: _Page, **kwargs):
    from bulk_downloader import interstitial

    return interstitial.clear_gates(page, sleep=lambda _seconds: None, **kwargs)


def test_site_gate_precedes_ordered_generic_tiers_and_reports_each_clearance():
    target = "https://example.test/scenes/42"
    controls = {
        ".measured-one": _Control("Continue"),
        ".measured-two": _Control("Continue"),
        "button:has-text('Accept All')": _Control("Accept All"),
        "button:has-text('Accept')": _Control("Accept"),
        "button:has-text('Enter Site')": _Control("Enter Site"),
        "button:has-text('Enter')": _Control("ENTER"),
        "a:has-text('No Thanks')": _Control(
            "No Thanks", "https://example.test/members/home"),
        "[class*='close' i]:visible": _Control("Close offer"),
    }
    page = _Page(target, controls)
    reported: list[str] = []

    assert all(page.visible(selector) for selector in controls)
    result = _clear(
        page,
        site_gates=".measured-one\n.measured-two",
        url=target,
        log=reported.append,
    )

    assert [selector for selector, _ in page.clicked] == [
        ".measured-one",
        "button:has-text('Accept All')",
        "button:has-text('Enter Site')",
        "a:has-text('No Thanks')",
    ]
    assert result == [
        "site: cleared via .measured-one",
        "consent: cleared via button:has-text('Accept All')",
        "age: cleared via button:has-text('Enter Site')",
        "interstitial: cleared via a:has-text('No Thanks')",
        "re-requested the original url after an interstitial",
    ]
    assert reported == result
    assert page.visible(".measured-two")
    assert page.visible("button:has-text('Accept')")
    assert page.visible("button:has-text('Enter')")
    assert page.visible("[class*='close' i]:visible")
    assert page.goto_calls == [
        (target, {"wait_until": "domcontentloaded", "timeout": 45000})]
    assert page.url == target


@pytest.mark.parametrize(
    ("label", "expected_clicks"),
    [
        ("I Disagree, Exit Here", 0),
        ("Exit Here", 0),
        ("I am under 18", 0),
        ("Take me out", 0),
        ("Cancel", 0),
        ("Accept All", 1),
        ("ENTER", 1),
    ],
)
def test_real_exit_denylist_labels_are_refused(label, expected_clicks):
    target = "https://kink.example/scenes/1"
    page = _Page(target, {".candidate": _Control(label)})

    assert page.visible(".candidate")
    assert page.controls[".candidate"].text == label
    result = _clear(page, site_gates=".candidate", url=target)

    assert len(page.clicked) == expected_clicks
    assert result == (["site: cleared via .candidate"] if expected_clicks else [])


def test_off_origin_click_goes_back_and_is_not_reported_as_cleared():
    target = "https://kink.example/scenes/1"
    page = _Page(target, {
        ".age-gate": _Control(
            "ENTER KINK", "https://accounts.google.example/signin"),
    })

    assert page.visible(".age-gate")
    result = _clear(page, site_gates=".age-gate", url=target)

    assert [selector for selector, _ in page.clicked] == [".age-gate"]
    assert len(page.go_back_calls) == 1
    assert page.goto_calls == []
    assert page.url == target
    assert len(result) == 1
    assert "LEFT THE ORIGIN" in result[0]
    assert "cleared" not in result[0]


def test_interstitial_clear_re_requests_the_exact_original_destination():
    target = "https://ultrafilms.example/movies/42?quality=best"
    selector = "[class*='close' i]:visible"
    page = _Page(target, {
        selector: _Control(
            "Close offer", "https://ultrafilms.example/members/home"),
    })

    assert page.visible(selector)
    result = _clear(page, url=target)

    assert [candidate for candidate, _ in page.clicked] == [selector]
    assert page.goto_calls == [
        (target, {"wait_until": "domcontentloaded", "timeout": 45000})]
    assert page.go_back_calls == []
    assert page.url == target
    assert result == [
        "interstitial: cleared via [class*='close' i]:visible",
        "re-requested the original url after an interstitial",
    ]


def test_configured_interstitial_that_lands_home_also_re_requests_destination():
    """A measured selector runs in the site tier before the generic tier.

    Its same-origin navigation away from the requested URL is the observable
    evidence that it swallowed the destination; classification cannot depend on
    the generic tier getting a second chance to click the now-closed control.
    """
    target = "https://ultrafilms.example/movies/measured"
    selector = "button.measured-upsell-close"
    page = _Page(target, {
        selector: _Control(
            "Close offer", "https://ultrafilms.example/members/home"),
    })

    assert page.visible(selector)
    result = _clear(page, site_gates=selector, url=target)

    assert [candidate for candidate, _ in page.clicked] == [selector]
    assert page.goto_calls == [
        (target, {"wait_until": "domcontentloaded", "timeout": 45000})]
    assert page.url == target
    assert result == [
        "site: cleared via button.measured-upsell-close",
        "re-requested the original url after an interstitial",
    ]


def test_page_with_no_gate_performs_exactly_zero_clicks():
    target = "https://example.test/scenes/no-gate"
    page = _Page(target, {"a.download": _Control("Download 4K")})

    assert page.visible("a.download")
    assert page.controls["a.download"].text == "Download 4K"
    result = _clear(page, site_gates="", url=target)

    assert len(page.clicked) == 0
    assert page.go_back_calls == []
    assert page.goto_calls == []
    assert result == []
