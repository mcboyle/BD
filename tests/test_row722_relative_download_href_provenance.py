"""Row 722 (members.filthykings.com): a host-relative ``window.open`` grant
must be resolved against the page origin before provenance is scored.

Measured on test2, 2026-09-15T06:25:59Z (run-2026-09-15T06:24:11Z-dl3.log and
the bulkdownloader journal): the trigger opened the download modal, the
learned 2160p row was hit, ``page.expect_download`` timed out, and the row 760
popup-grant capture read the URL the site handed to ``window.open`` VERBATIM::

  download: window.open grant captured unconsumed -> mp4
      (/movieaction/download/292639/2160p/mp4?codec=h264&impressionUUID=...)
  needs_review: staging unavailable: cannot derive resource provenance from
      '/movieaction/download/292639/2160p/mp4?...': the media URL has no
      scheme or host

A browser resolves ``window.open("/movieaction/...")`` against
``document.baseURI``; the capture stored ``String(url)`` and handed the raw
attribute to the HTTP leg, where ``staging_claim.resource_identity`` rightly
refuses a URL with no host. The owner of that resolution is the grant reader
(``_arm_popup_grant_capture``), which knows the page: it resolves a relative
grant against ``page.url`` and hands every consumer an absolute URL.

The negative control keeps ``resource_identity``'s refusal intact for a grant
that has no base to resolve against (``javascript:``, ``data:``, empty).

Fixtures only: no browser, no live site.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader import runner_transport as transport
from bulk_downloader import staging_claim


BD_GATE_SCOPE = "module"

PAGE_URL = "https://members.example/en/video/site/scene/292639"
RELATIVE_GRANT = "/movieaction/download/1/2160p/mp4?codec=h264&impressionUUID=u1"
ABSOLUTE_GRANT = "https://members.example" + RELATIVE_GRANT
REFUSAL = "cannot derive resource provenance"


class _ParentPage:
    """The scene page; ``window.open`` receives the host-relative href."""

    url = PAGE_URL

    def __init__(self, grant):
        self.grant = grant
        self.open_intercepted = False
        self.grant_url = None
        self.evaluations = []

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
        assert self.open_intercepted, "the fixture only models the armed page"
        self.grant_url = url              # String(url): the raw attribute

    @contextmanager
    def expect_download(self, *, timeout):
        yield None
        raise PWTimeout("no download event on the parent page")

    def title(self):
        return "Scene 292639"


class _QualityRow:
    def __init__(self, page, grant):
        self.page = page
        self.grant = grant

    def get_attribute(self, name):
        assert name == "href"
        return None                       # click-only grant

    def click(self):
        self.page.window_open(self.grant)


class _Runner(transport.TransportMixin):
    """The HTTP leg scores provenance exactly as ``_http_download`` does."""

    def __init__(self):
        self.site_id = "row722"
        self.config = {"name": "row722", "use_http_dl": True,
                       "verify_hash": False, "verify_integrity": False}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("row722")
        self.fetched = []
        self.failures = []
        self.status = []

    def _http_download(self, _page_url, _page, _ctx, file_url, final_path):
        try:
            staging_claim.resource_identity(file_url)
        except staging_claim.StagingUnavailable as e:
            raise transport._StagingUnavailable(str(e))
        Path(final_path).write_bytes(b"x" * 4096)
        self.fetched.append(file_url)
        return (4096, 4096)

    def _pw_save(self, dl, final_path):   # pragma: no cover - must not be used
        raise AssertionError("no download event exists to save")

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


def _drive(tmp_path, monkeypatch, grant):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity",
                        lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    page = _ParentPage(grant)
    runner = _Runner()
    runner._do_download(
        page, object(), page.url,
        {"locator": _QualityRow(page, grant), "score": 2160, "size": 0,
         "text": "2160p", "_all_candidates": []},
        Path(tmp_path), "2160p")
    return runner


def test_a_host_relative_window_open_grant_is_resolved_against_the_page(
        tmp_path, monkeypatch):
    """RED at 42cb12bd: the raw '/movieaction/...' reaches provenance scoring."""
    runner = _drive(tmp_path, monkeypatch, RELATIVE_GRANT)

    refusals = [m for s, m in runner.status if REFUSAL in m]
    assert not refusals, (
        "the host-relative grant was scored unresolved (filthykings shape): "
        f"{refusals}")
    assert runner.fetched == [ABSOLUTE_GRANT], (
        "the HTTP leg must receive the grant resolved against the page "
        f"origin; got {runner.fetched}, status trail {runner.status}")
    assert not [m for s, m in runner.status if s == "needs_review"], (
        f"a resolvable grant must not be filed needs_review: {runner.status}")
    assert runner.failures == []


def test_resolver_yields_the_page_host_for_a_host_relative_grant():
    """The pure seam: provenance host is the page host, and it is scoreable."""
    resolved = transport._resolve_popup_grant(RELATIVE_GRANT, PAGE_URL)
    assert resolved == ABSOLUTE_GRANT
    assert staging_claim.resource_identity(resolved) == \
        staging_claim.resource_identity(ABSOLUTE_GRANT)
    # An absolute grant (the row 760 Aylo shape) passes through untouched.
    signed = "https://download-private-ht.example/s/x.mp4?hash=one-time"
    assert transport._resolve_popup_grant(signed, PAGE_URL) == signed
    # A protocol-relative grant takes the page scheme.
    assert transport._resolve_popup_grant("//cdn.example/v.mp4", PAGE_URL) \
        == "https://cdn.example/v.mp4"


@pytest.mark.parametrize("grant", ["javascript:void(0)", "data:text/plain,x",
                                   "", "   "])
def test_negative_control_a_grant_with_no_base_still_refuses(grant):
    """A grant nothing can resolve is not promoted to the page origin.

    ``_resolve_popup_grant`` hands it on unchanged (or as no grant at all),
    and ``resource_identity`` keeps refusing it with the same distinctive
    diagnostic -- resolution must never fabricate a host.
    """
    resolved = transport._resolve_popup_grant(grant, PAGE_URL)
    assert resolved in (None, grant), (
        f"{grant!r} has no base to resolve against; got {resolved!r}")
    with pytest.raises(staging_claim.StagingUnavailable) as exc:
        staging_claim.resource_identity(resolved if resolved else grant)
    assert REFUSAL in str(exc.value)
    assert "members.example" not in str(exc.value)
