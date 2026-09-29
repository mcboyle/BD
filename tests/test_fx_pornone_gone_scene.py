"""fx-pornone-gone-scene (O1568d, results/spare12/pornone.md): a removed scene must fail, not save a placeholder.

pornone.com/babes/aubrey-sinclair-realtor/280984893/ answers HTTP 410 with a "Video not found" page. The app
went on past ``page.goto``, page-media picked the page's 30 s promo clip, and the job finished ``done`` with
"Video not found — PornOne ex vPorn.mp4" (1.6 MB, 240p). Nothing on that page is the scene.

Fix: ``_process_one`` reads the navigation response; 404/410 is a permanent failure and nothing after goto runs.
"""

from __future__ import annotations

from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_URL = "https://pornone.com/babes/aubrey-sinclair-realtor/280984893/"


class _Reached(BaseException):
    """Raised by the first step after goto (BaseException: that step's own `except Exception` is non-fatal)."""


class _Response:
    def __init__(self, status):
        self.status = status


class _Page:
    def __init__(self, status):
        self.url = "about:blank"
        self._status = status
        self.gotos = 0

    def on(self, event, handler):
        handler._pw_impl_instance_ = None

    def goto(self, url, *_args, **_kwargs):
        self.gotos += 1
        self.url = url
        return None if self._status is None else _Response(self._status)

    def evaluate(self, *_args, **_kwargs):
        return None

    def screenshot(self, *_args, **_kwargs):
        return b""

    def close(self):
        return None


class _Context:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def _run(tmp_path, status):
    import bulk_downloader.runner as runner_module
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        "fxpornone",
        {"name": "pornone", "download_dir": str(tmp_path), "wait": 0,
         "verify_integrity": False, "embed_metadata": False},
    )
    runner.jobs[_URL] = {}
    runner._dedup_preflight = lambda *_a, **_k: ""
    runner._handle_auto_teach_check = lambda *_a, **_k: False
    runner._check_cookies_or_relogin = lambda *_a, **_k: True
    runner._stash_dedup_check = lambda *_a, **_k: False
    runner._try_plugin_extractor = lambda *_a, **_k: False
    runner._apply_stealth_library_to_page = lambda *_a, **_k: None
    runner._install_event_listeners = lambda *_a, **_k: None
    runner._warm_session = lambda *_a, **_k: None
    runner._handle_captcha_check = lambda *_a, **_k: True
    runner._check_redirect = lambda *_a, **_k: ""
    runner.failures = []
    runner._handle_failure = lambda url, msg, *a, **k: runner.failures.append((url, msg))

    def _settle(_page):
        raise _Reached()

    runner._settle_after_navigation = _settle
    page = _Page(status)
    reached = False
    with (
        mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None),
        mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None),
    ):
        try:
            runner._process_one(None, _URL, persistent_ctx=_Context(page))
        except _Reached:
            reached = True
    return runner, page, reached


@pytest.mark.parametrize("status,word", [(410, "Gone"), (404, "Not Found")])
def test_gone_scene_fails_permanent_and_reads_nothing(tmp_path, status, word):
    runner, page, reached = _run(tmp_path, status)
    assert not reached, (
        f"FX_PORNONE_GONE_SCENE_PROCESSED: HTTP {status} scene page was read on past goto; "
        "page-media saves the 'Video not found' promo clip as the scene"
    )
    assert page.gotos == 1
    assert runner.failures == [
        (_URL, f"Scene page answered HTTP {status} ({word}): removed by the site, nothing downloaded")
    ]
    assert runner._classify_error(runner.failures[0][1]) == "permanent"
    media = [f for f in tmp_path.rglob("*") if f.suffix in (".mp4", ".part", ".webm", ".m4v")]
    assert media == [], media


@pytest.mark.parametrize("status", [200, 403, 503, None])
def test_control_other_statuses_still_read_the_page(tmp_path, status):
    """200 is a scene; 403/503 are challenge pages the captcha/turnstile path handles; None is a same-doc nav."""
    runner, _page, reached = _run(tmp_path, status)
    assert reached, f"status {status}: the flow stopped at goto (failures={runner.failures!r})"
    assert runner.failures == []
