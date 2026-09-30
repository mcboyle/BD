"""fx-harden-kmm-reauth-redirect -- a scene page that re-authenticates lands on the site root; go back to the scene.

Live on bd2 (T168, 2026-09-30 08:12Z, results/bd2/HARDEN-T165-H3-B.md): kellymadisonmedia's stored jar holds
only a live Laravel remember_web_* cookie (kmm_session expired). The first request re-authenticates and the
site redirects to the members HOME, dropping the intended URL: /episodes/<id> ends at "/". A second request
with the fresh session opens the episode (3 .mp4 links). ``_process_one`` read the home page as the scene,
harvested the home list's tour links and parked the job "Best is 720p ... no identity proof".

Fix: when goto of a scene URL whose path is not "/" lands on the SAME host's root, navigate to the scene once
more. Controls: a scene that loads is navigated once; a root URL is navigated once; a redirect to another
path, or to another host's root, is left alone; a site that sends every visit home is re-tried only once.
"""

from __future__ import annotations

from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SCENE = "https://members.example.test/episodes/200933743?h3b=1"
_ROOT = "https://members.example.test/"


class _Reached(BaseException):
    """Raised by the first step after navigation (BaseException: that step's `except Exception` is non-fatal)."""


class _Response:
    status = 200


class _Page:
    def __init__(self, landings):
        self.url = "about:blank"
        self._landings = list(landings)
        self.gotos = []

    def on(self, event, handler):
        handler._pw_impl_instance_ = None

    def goto(self, url, *_args, **_kwargs):
        self.gotos.append(url)
        self.url = self._landings.pop(0) if self._landings else url
        return _Response()

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


def _run(tmp_path, url, landings):
    import bulk_downloader.runner as runner_module
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        "fxkmmredirect",
        {"name": "kellymadisonmedia", "download_dir": str(tmp_path), "wait": 0,
         "verify_integrity": False, "embed_metadata": False},
    )
    runner.jobs[url] = {}
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
    runner._handle_failure = lambda u, msg, *a, **k: runner.failures.append((u, msg))
    seen = {}

    def _settle(page):
        seen["url"] = page.url
        raise _Reached()

    runner._settle_after_navigation = _settle
    page = _Page(landings)
    with (
        mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None),
        mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None),
    ):
        try:
            runner._process_one(None, url, persistent_ctx=_Context(page))
        except _Reached:
            pass
    return runner, page, seen.get("url")


def test_reauth_redirect_to_site_root_goes_back_to_the_scene(tmp_path):
    runner, page, read_url = _run(tmp_path, _SCENE, [_ROOT, _SCENE])
    assert read_url == _SCENE, (
        f"FX_HARDEN_KMM_REAUTH_REDIRECT: the scene URL landed on the site root ({_ROOT}) and the flow read "
        f"{read_url!r} as the scene (gotos={page.gotos}); on kellymadisonmedia that parks the job 720p/no identity")
    assert page.gotos == [_SCENE, _SCENE]
    assert runner.failures == []


def test_control_scene_that_loads_is_navigated_once(tmp_path):
    _runner, page, read_url = _run(tmp_path, _SCENE, [_SCENE])
    assert (page.gotos, read_url) == ([_SCENE], _SCENE)


def test_control_site_that_always_sends_home_is_retried_once(tmp_path):
    _runner, page, read_url = _run(tmp_path, _SCENE, [_ROOT, _ROOT, _ROOT])
    assert page.gotos == [_SCENE, _SCENE], "the root redirect is re-tried exactly once, never looped"
    assert read_url == _ROOT


@pytest.mark.parametrize("url,landing", [
    (_ROOT, _ROOT),                                                    # a root URL is not a scene
    (_SCENE, "https://members.example.test/login"),                    # another path: _check_redirect's job
    (_SCENE, "https://www.other.test/"),                               # another host's root
])
def test_control_other_landings_are_left_alone(tmp_path, url, landing):
    _runner, page, read_url = _run(tmp_path, url, [landing])
    assert (page.gotos, read_url) == ([url], landing)
