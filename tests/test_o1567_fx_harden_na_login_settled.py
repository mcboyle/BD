"""fx-harden-na-login-settled -- a re-login inside _process_one must reach the worker's persistent context.

Live on fresh149 (T166, 2026-09-30 06:08-06:22Z, results/fresh149/naughtyamerica_HARDEN-H4-B.md): the stored
naughtyamerica jar had expired. ``_check_cookies_or_relogin`` logged in ("login: attempt 1 settled ok") and
published the fresh jar, but ``_process_one`` then opened the scene in the worker's PERSISTENT context, which
still held the stale cookies (the worker only re-injects the jar before it pulls the NEXT url). The scene
landed on /login, ``_handle_auth_required`` spent a second live login, and the url only passed on its requeue.

Fix: a jar published while this url was being processed is injected into the persistent context before the
scene is opened. Controls: no re-login -> no injection; the legacy owned-context path is unchanged;
a failing injection is reported and the url still proceeds.
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SCENE = "https://members.example.test/scene/sage-hunter-33799?h4b=1"
_STALE = [{"name": "sess", "value": "stale", "domain": "members.example.test", "path": "/"}]
_FRESH = [{"name": "sess", "value": "fresh", "domain": "members.example.test", "path": "/"}]


class _Reached(BaseException):
    """Raised by the first step after navigation (BaseException: that step's `except Exception` is non-fatal)."""


class _Response:
    status = 200


class _Context:
    def __init__(self, jar, fail_add=False):
        self.jar = {c["name"]: c["value"] for c in jar}
        self.add_calls = 0
        self._fail_add = fail_add
        self.page = _Page(self)

    def add_cookies(self, cookies):
        self.add_calls += 1
        if self._fail_add:
            raise RuntimeError("cdp gone")
        self.jar.update({c["name"]: c["value"] for c in cookies})

    def new_page(self):
        return self.page


class _Page:
    def __init__(self, ctx):
        self._ctx = ctx
        self.url = "about:blank"
        self.jar_at_goto = []

    def on(self, event, handler):
        handler._pw_impl_instance_ = None

    def goto(self, url, *_args, **_kwargs):
        self.jar_at_goto.append(dict(self._ctx.jar))
        self.url = url
        return _Response()

    def evaluate(self, *_args, **_kwargs):
        return None

    def screenshot(self, *_args, **_kwargs):
        return b""

    def close(self):
        return None


def _run(tmp_path, relogin, ctx):
    import bulk_downloader.runner as runner_module
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        "fxnaloginsettled",
        {"name": "naughtyamerica", "download_dir": str(tmp_path), "wait": 0,
         "verify_integrity": False, "embed_metadata": False},
    )
    runner.jobs[_SCENE] = {}
    runner.cookies = list(_STALE)
    runner._cookies_updated_at = time.time() - 36000  # the worker injected this jar at its last pull

    def _check(_url):
        if relogin:  # the inline re-login publishes a new jar (runner.set_cookies' effect)
            runner.cookies = list(_FRESH)
            runner._cookies_updated_at = time.time()
        return True

    runner._dedup_preflight = lambda *_a, **_k: ""
    runner._handle_auto_teach_check = lambda *_a, **_k: False
    runner._check_cookies_or_relogin = _check
    runner._stash_dedup_check = lambda *_a, **_k: False
    runner._try_plugin_extractor = lambda *_a, **_k: False
    runner._apply_stealth_library_to_page = lambda *_a, **_k: None
    runner._install_event_listeners = lambda *_a, **_k: None
    runner._warm_session = lambda *_a, **_k: None
    runner._handle_captcha_check = lambda *_a, **_k: True
    runner._check_redirect = lambda *_a, **_k: ""
    runner.failures = []
    runner._handle_failure = lambda u, msg, *a, **k: runner.failures.append((u, msg))

    def _settle(page):
        raise _Reached()

    runner._settle_after_navigation = _settle
    with (
        mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None),
        mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None),
    ):
        try:
            runner._process_one(None, _SCENE, persistent_ctx=ctx)
        except _Reached:
            pass
    return runner


def test_inline_relogin_jar_reaches_the_persistent_context_before_the_scene(tmp_path):
    ctx = _Context(_STALE)
    runner = _run(tmp_path, relogin=True, ctx=ctx)
    assert ctx.page.jar_at_goto, "the scene was never opened"
    assert ctx.page.jar_at_goto[0].get("sess") == "fresh", (
        "FX_HARDEN_NA_LOGIN_SETTLED: _check_cookies_or_relogin logged in and published a fresh jar, but the "
        f"scene was opened in the persistent context with {ctx.page.jar_at_goto[0]!r} -- the site answers "
        "/login and _handle_auth_required spends a second live login (fresh149 naughtyamerica 06:09-06:12Z)")
    assert runner.failures == []


def test_control_no_relogin_no_injection(tmp_path):
    ctx = _Context(_STALE)
    _run(tmp_path, relogin=False, ctx=ctx)
    assert ctx.add_calls == 0, "a jar the worker already injected at pull time is not re-injected"
    assert ctx.page.jar_at_goto[0].get("sess") == "stale"


def test_control_failed_injection_is_reported_and_the_url_proceeds(tmp_path, capsys):
    ctx = _Context(_STALE, fail_add=True)
    runner = _run(tmp_path, relogin=True, ctx=ctx)
    assert ctx.add_calls == 1
    assert ctx.page.jar_at_goto, "a failed injection must not stop the url; _check_redirect still judges it"
    assert "cookie refresh failed" in capsys.readouterr().err
    assert runner.failures == []
