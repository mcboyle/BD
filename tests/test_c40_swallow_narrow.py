"""C40-SWALLOW -- selector_chains broad excepts narrowed to Playwright's Error.

_check (inside _text_present) caught every exception from page.content() and
returned False, so a closed page (TargetClosedError) was reported as "the text
never appeared" instead of as an error. _pause keeps its time.sleep fallback,
now only for Playwright errors.
"""

from playwright._impl._errors import TargetClosedError
from playwright.sync_api import Error as PWError

from bulk_downloader import selector_chains as sc

BD_GATE_SCOPE = "module"


class _Loc:
    first = None

    def __init__(self):
        self.first = self

    def count(self):
        return 1

    def click(self, timeout=None):
        pass


class _ContentPage:
    """content() raises each queued exception in turn, then returns text."""

    url = "http://t/a"

    def __init__(self, *errors, text="<p>Welcome</p>"):
        self._errors = list(errors)
        self._text = text
        self.content_calls = 0

    def locator(self, sel):
        return _Loc()

    def content(self):
        self.content_calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self._text


def _text_step(**extra):
    raw = {
        "selector": "#s",
        "timeout_ms": 300,
        "post_condition": "text_appeared:Welcome",
        "advance_on": ["no_text", "threw"],
    }
    raw.update(extra)
    return sc.parse_step(raw)


def test_closed_page_in_text_check_is_reported_not_not_found():
    page = _ContentPage(*[TargetClosedError() for _ in range(100)])
    outcome, detail = sc.try_step(_text_step(), page, "click")
    assert detail.startswith(sc.FM_THREW + ":"), (
        f"C40-SWALLOW-CLOSED: closed page reported as {outcome!r} {detail!r}"
    )
    assert "closed" in detail, f"C40-SWALLOW-CLOSED: detail lost error: {detail!r}"
    assert page.content_calls == 1, (
        f"C40-SWALLOW-CLOSED: kept polling a closed page "
        f"({page.content_calls} content() calls)"
    )


def test_closed_page_raises_from_text_present():
    page = _ContentPage(TargetClosedError())
    try:
        sc._text_present(page, "Welcome", 300)
    except TargetClosedError:
        return
    raise AssertionError(
        "C40-SWALLOW-CLOSED: _text_present swallowed TargetClosedError"
    )


def test_transient_playwright_error_still_polls_until_text():
    # Control: a non-closure Playwright error (e.g. page navigating) is
    # still "not yet", and the poll goes on to find the text.
    page = _ContentPage(
        PWError("Unable to retrieve content because the page is navigating")
    )
    outcome, detail = sc.try_step(_text_step(timeout_ms=2000), page, "click")
    assert outcome == "ok", f"C40-SWALLOW-TRANSIENT: {outcome!r} {detail!r}"
    assert page.content_calls == 2


def test_non_playwright_error_in_text_check_is_not_masked():
    # Only Playwright errors are caught; anything else is a bug and surfaces.
    page = _ContentPage(RuntimeError("boom"))
    try:
        outcome, detail = sc.try_step(_text_step(), page, "click")
    except RuntimeError:
        return
    raise AssertionError(f"C40-SWALLOW-OTHER: masked as {outcome!r} {detail!r}")


class _WaitPage:
    def __init__(self, exc):
        self._exc = exc

    def wait_for_timeout(self, ms):
        raise self._exc


def test_pause_falls_back_to_sleep_on_playwright_error(monkeypatch):
    slept = []
    monkeypatch.setattr(sc.time, "sleep", slept.append)
    sc._pause(_WaitPage(PWError("wait failed")), 0.05)
    assert slept == [0.05], f"C40-SWALLOW-PAUSE: no sleep fallback: {slept!r}"


def test_pause_falls_back_to_sleep_on_closed_page(monkeypatch):
    slept = []
    monkeypatch.setattr(sc.time, "sleep", slept.append)
    sc._pause(_WaitPage(TargetClosedError()), 0.05)
    assert slept == [0.05], f"C40-SWALLOW-PAUSE: no sleep fallback: {slept!r}"
