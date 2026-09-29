"""dl95-hustlerunlimited-1: the download scorer admitted a cookie-consent control.

Measured on test2 (B6-B p1/hustlerunlimited): scene /videos/one-dare-too-many/ -> needs_review "Clicked but no download
started ... Saw: auto(?):Save preferences". The page's Complianz banner offers I Understand / Deny / View preferences /
Save preferences / Manage consent; "Save preferences" carries _DL_WORD_RE's "save", so admission took it as a download
control and it won. GREEN: _candidate_admission refuses a control whose whole visible label is consent/CMP vocabulary.

CMP below is the banner's button markup verbatim from https://hustlerunlimited.com/videos/one-dare-too-many/ (captured
2026-09-29). Hermetic: page.set_content in a real Chromium (the row-508 harness); no network.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download

BD_GATE_SCOPE = "module"

CMP = ('<div class="cmplz-cookiebanner banner-4 optout cmplz-bottom"><div class="cmplz-buttons">'
       '<button class="cmplz-btn cmplz-accept">I Understand</button>'
       '<button class="cmplz-btn cmplz-deny">Deny</button>'
       '<button class="cmplz-btn cmplz-view-preferences">View preferences</button>'
       '<button class="cmplz-btn cmplz-save-preferences">Save preferences</button>'
       '<button class="cmplz-btn cmplz-manage-consent manage-consent-4">Manage consent</button>'
       '</div></div>')
SCENE = '<main><h1>One Dare Too Many</h1><video src="/t.mp4"></video></main>'


def _html(extra=""):
    return f"<!doctype html><html><body>{SCENE}{extra}{CMP}</body></html>"


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


def _text(best):
    return " ".join(str((best or {}).get("text") or "").split())


def test_the_consent_banner_is_not_a_download_candidate():
    with _page(_html()) as page:
        assert page.get_by_text("Save preferences").count() == 1  # the control is on the page
        best = find_best_download(page)
    assert best is None or "preferences" not in _text(best).lower(), _text(best)


@pytest.mark.parametrize("control", [
    '<a class="dl" href="/dl/one-dare-too-many_1080p.mp4">Download 1080p</a>',
    '<button class="dl">Save 1080p</button>',            # "save" still a download word outside consent labels
])
def test_a_real_download_control_still_wins_beside_the_banner(control):
    with _page(_html(control)) as page:
        best = find_best_download(page)
    assert best is not None and "1080p" in _text(best), _text(best)


@pytest.mark.parametrize("label", [
    "Save preferences", "Manage consent", "View preferences", "Deny", "Accept all cookies", "Reject all",
    "Accept & continue", "Cookie settings", "Only necessary cookies", "Do not sell my personal information",
    "save  my\nchoices",
])
def test_consent_vocabulary(label):
    from bulk_downloader.detect import _CONSENT_CONTROL_RE
    assert _CONSENT_CONTROL_RE.fullmatch(" ".join(label.split()))


@pytest.mark.parametrize("label", [
    "Save", "Save 1080p", "Download", "Download 4K", "Save video", "Accept 1080p", "Settings 720p", "1080p",
])
def test_download_labels_are_not_consent(label):
    from bulk_downloader.detect import _CONSENT_CONTROL_RE
    assert not _CONSENT_CONTROL_RE.fullmatch(label)
