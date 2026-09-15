"""Row 778: an extractor completion must retain the already-open page title."""
from __future__ import annotations

from unittest import mock

import pytest


# _process_one is the shared completion boundary for every page-native
# extractor, so a regression can be introduced by any extractor integration.
BD_GATE_SCOPE = "repo-wide"
pytestmark = pytest.mark.bd_module_wipe

_URL = "https://members.blacked.example/videos/black-title"


class _Page:
    def __init__(self, document_title: str):
        self.document_title = document_title
        self.title_queries = 0
        self.url = _URL

    def goto(self, *_args, **_kwargs):
        return None

    def evaluate(self, script):
        if "document.title" in script:
            self.title_queries += 1
            return {
                "og_title": "",
                "document_title": self.document_title,
                "h1": "",
            }
        return None

    def close(self):
        return None


class _Context:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def _runner(tmp_path):
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("blacked", {
        "name": "Blacked",
        "download_dir": str(tmp_path),
        "wait": 0,
        "use_vixen_extractor": True,
    })
    runner.jobs[_URL] = {}
    runner._dedup_preflight = lambda *_args: ""
    runner._handle_auto_teach_check = lambda *_args: False
    runner._check_cookies_or_relogin = lambda *_args: True
    runner._stash_dedup_check = lambda *_args: False
    runner._try_plugin_extractor = lambda *_args: False
    runner._apply_stealth_library_to_page = lambda *_args: None
    runner._install_event_listeners = lambda *_args: None
    runner._warm_session = lambda *_args: None
    runner._handle_captcha_check = lambda *_args: True
    runner._check_redirect = lambda *_args: ""
    return runner


@pytest.mark.parametrize(
    ("document_title", "expected"),
    [
        ("BLACKED: X", {"title": "BLACKED: X", "title_source": "document.title"}),
        ("", {"title": "", "title_source": ""}),
    ],
)
def test_vixen_completion_via_process_one_uses_only_the_document_title(
        tmp_path, document_title, expected):
    """The Vixen handoff happens after navigation but before its done record."""
    import bulk_downloader.runner as runner_module
    from bulk_downloader.website_title import history_title_kwargs

    runner = _runner(tmp_path)
    page = _Page(document_title)
    recorded = {}

    def completed_vixen(url, _page):
        recorded.update(history_title_kwargs(runner, url))
        return True

    runner._try_vixen_extractor = completed_vixen
    fake_vixen = type("Vixen", (), {"is_vixen_url": staticmethod(lambda _url: True)})()
    with mock.patch.object(runner_module, "_vixen", fake_vixen), \
         mock.patch.object(runner_module, "_VIXEN_AVAILABLE", True), \
         mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_args: None), \
         mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_args, **_kwargs: None):
        runner._process_one(None, _URL, persistent_ctx=_Context(page))

    assert recorded == expected
    # Two parametrized completions are the whole fixture population: a title
    # is retained once, and an empty page cannot manufacture one from the URL.
    assert page.title_queries == 1


def test_worker_dispatch_reaches_the_title_harvesting_process_one(tmp_path):
    """A claimed URL must reach the completion path that owns this handoff."""
    runner = _runner(tmp_path)
    persistent_context = object()
    called = []
    runner._claim_worker_item = lambda *_args: ("claimed", 7)
    runner._update_job = lambda *_args, **_kwargs: None
    runner._process_one = lambda browser, url, persistent_ctx=None: called.append(
        (browser, url, persistent_ctx))

    result = runner._process_worker_url(
        0, None, _URL, persistent_ctx=persistent_context, run_generation=7)

    assert result == runner._WORKER_CLAIM_PROCESSED
    assert called == [(None, _URL, persistent_context)]
