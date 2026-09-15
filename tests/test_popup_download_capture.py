"""Row 760: a download grant handed to ``window.open`` must be taken unconsumed.

Aylo/project1content (bangbros, brazzers) answers a click on a quality row in
the download modal with ``window.open`` on a signed mp4 at
``download-private-{ht,fl}.project1content.com``. That URL is one-time /
request-context bound: the popup's own first request SPENDS it and every later
request answers 474. The runner watches only the parent page it clicked, so
``page.expect_download`` times out, the job is filed ``needs_review`` as a
"modal-trigger button", and the grant is already gone.

The fix recorded as verified for both sites is to override ``window.open``
before the click, so the signed URL is captured UNCONSUMED and no popup opens;
the ordinary HTTP leg then fetches it once with the session's cookies. The
fixture below is that shape and nothing else.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader import runner_transport as transport


BD_GATE_SCOPE = "module"

SIGNED_URL = (
    "https://download-private-ht.project1content.com/"
    "s/scene-2160p.mp4?validfrom=1757&validto=1757&hash=one-time"
)
PAYLOAD = b"x" * 4096


class _SingleUseGrant:
    """The signed URL. The FIRST request wins; a second one is the 474."""

    def __init__(self):
        self.mints = 0
        self.gets = 0

    def mint(self):
        """Site JS builds the signed URL and hands it to window.open."""
        self.mints += 1
        return SIGNED_URL

    def get(self):
        self.gets += 1
        if self.gets > 1:
            raise AssertionError(
                "474: the signed URL was already spent by the popup")
        return PAYLOAD


class _ParentPage:
    """The page the runner clicked. window.open is the whole seam."""

    url = "https://www.bangbros.com/video/12345"

    def __init__(self, grant):
        self.grant = grant
        self.open_intercepted = False
        self.grant_url = None
        self.popups = 0
        self.evaluations = []
        self.timeouts = []

    # ── the JS side of the seam ──
    def evaluate(self, script):
        self.evaluations.append(script)
        if script == transport._POPUP_GRANT_ARM:
            self.open_intercepted = True
            self.grant_url = None
            return None
        if script == transport._POPUP_GRANT_READ:
            return self.grant_url
        if script == transport._POPUP_GRANT_DISARM:
            self.open_intercepted = False
            self.grant_url = None
            return None
        raise AssertionError(f"unexpected page script: {script!r}")

    def window_open(self, url):
        if self.open_intercepted:
            self.grant_url = url          # captured, and nothing requested it
            return
        self.popups += 1
        self.grant.get()                  # the popup spends the grant

    @contextmanager
    def expect_download(self, *, timeout):
        self.timeouts.append(timeout)     # the runner's budget, asserted below
        yield None                        # the body clicks
        raise PWTimeout("no download event on the parent page")

    def title(self):
        return "Scene 12345"


class _QualityRowLocator:
    """The 2160p row in Aylo's modal: no href, and the click is a window.open."""

    def __init__(self, page):
        self.page = page
        self.clicks = 0

    def get_attribute(self, name):
        assert name == "href"
        return None                       # click-only grant

    def click(self):
        self.clicks += 1
        self.page.window_open(self.page.grant.mint())


class _DeadModalTrigger(_QualityRowLocator):
    """A real modal-trigger button: the click opens nothing at all."""

    def click(self):
        self.clicks += 1


class _Runner(transport.TransportMixin):
    def __init__(self, grant):
        self.site_id = "row760"
        self.config = {"name": "row760", "use_http_dl": True,
                       "verify_hash": False, "verify_integrity": False}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("row760")
        self.grant = grant
        self.fetched = []
        self.failures = []
        self.status = []

    def _http_download(self, _page_url, _page, _ctx, file_url, final_path):
        body = self.grant.get()           # 474 here if a popup already spent it
        Path(final_path).write_bytes(body)
        self.fetched.append(file_url)
        return (len(body), len(body))

    def _pw_save(self, dl, final_path):   # pragma: no cover - must not be used
        raise AssertionError(
            "the browser has no download event to save; the grant went to "
            "window.open")

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _screenshot(self, *_args, **_kwargs):
        return ""

    def _update_job(self, _url, state, message="", **_kwargs):
        self.status.append((state, message))

    def _handle_failure(self, url, message):
        self.failures.append((url, message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def _drive(tmp_path, monkeypatch, runner, page, locator):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity",
                        lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    runner._do_download(
        page, object(), page.url,
        {"locator": locator, "score": 0, "size": 0, "text": "2160p",
         "_all_candidates": []},
        Path(tmp_path), "2160p")


# ── the fixture really builds the shape, before any verdict about the runner ──

def test_precondition_an_unintercepted_popup_spends_the_grant():
    """Nonzero shape proof: the popup consumes the URL and a re-fetch is 474."""
    grant = _SingleUseGrant()
    page = _ParentPage(grant)
    locator = _QualityRowLocator(page)

    locator.get_attribute("href")
    locator.click()

    assert page.popups == 1, "an unintercepted window.open opens one popup"
    assert grant.gets == 1, "the popup requested the signed URL"
    try:
        grant.get()
    except AssertionError as exc:
        assert "474" in str(exc)
    else:                                 # pragma: no cover - fixture guard
        raise AssertionError("the grant must be single-use")

    fired = []
    try:
        with page.expect_download(timeout=1):
            fired.append("clicked")
    except PWTimeout:
        pass
    else:                                 # pragma: no cover - fixture guard
        raise AssertionError("the parent must never see a download event")
    assert fired == ["clicked"], "the parent's wait must still run the click"


def test_a_window_open_grant_is_captured_unconsumed_and_downloaded(
        tmp_path, monkeypatch):
    """RED at 367f0f42: the parent times out and the popup eats the grant."""
    grant = _SingleUseGrant()
    page = _ParentPage(grant)
    locator = _QualityRowLocator(page)
    runner = _Runner(grant)

    _drive(tmp_path, monkeypatch, runner, page, locator)

    assert page.timeouts == [60000], (
        "the runner's own download budget is unchanged by the rescue")
    assert locator.clicks == 1, "the quality row is clicked exactly once"
    assert page.popups == 0, "no popup may open: a popup spends the grant"
    assert grant.mints == 1, "the signed URL is minted exactly once"
    assert len(runner.fetched) == 1, (
        "the captured grant must be fetched exactly once; status trail: "
        f"{runner.status}")
    assert runner.fetched[0] == SIGNED_URL
    assert runner.failures == []
    assert not [m for s, m in runner.status if s == "needs_review"], (
        f"a captured grant must not be filed needs_review: {runner.status}")


def test_the_window_open_override_is_removed_after_the_click(
        tmp_path, monkeypatch):
    """The interception is scoped to the click, not left on the page."""
    grant = _SingleUseGrant()
    page = _ParentPage(grant)

    _drive(tmp_path, monkeypatch, _Runner(grant), page, _QualityRowLocator(page))

    assert page.open_intercepted is False, (
        "window.open stayed overridden after the download it was armed for")
    assert page.evaluations[-1] == transport._POPUP_GRANT_DISARM


def test_no_window_open_still_reports_the_modal_trigger_review(
        tmp_path, monkeypatch):
    """Negative control: with nothing captured the timeout is still review.

    This fails for the INTENDED reason if the rescue is widened into a blanket
    'a timeout is fine': nothing called window.open, no grant exists anywhere,
    and the operator must still be told the click produced no download.
    """
    grant = _SingleUseGrant()
    page = _ParentPage(grant)
    runner = _Runner(grant)

    _drive(tmp_path, monkeypatch, runner, page, _DeadModalTrigger(page))

    assert grant.mints == 0, "the control must mint no grant"
    assert runner.fetched == [], "nothing was granted, so nothing was fetched"
    review = [m for s, m in runner.status if s == "needs_review"]
    assert len(review) == 1, f"expected exactly one needs_review: {runner.status}"
    assert "modal-trigger button" in review[0], review[0]
