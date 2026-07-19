"""Characterization test for the manual-download mixin (PHASE 3 runner cut 3).

runner_manual.py (ManualMixin + the _ManualDownloadSession class) had ZERO
dedicated test coverage before the v3.66.399 extraction. A decomposition is pure
code motion, so these assertions must hold identically before and after the move
(DECOMPOSITION_PLAYBOOK section 6: a coupling-heavy section with no behavioral
coverage needs a characterization test added with the cut).

Scope is deliberately the no-browser control-flow branches (bad url,
already-in-progress, no pending session) plus the import/surface. The live
takeover path drives Playwright/httpx/vpn on a dedicated thread and is NOT
sandbox-runnable; it is covered by the on-stash suite and the operator flow.
"""
import inspect

from bulk_downloader import runner
from bulk_downloader.runner_manual import ManualMixin, _ManualDownloadSession


class _Stub(ManualMixin):
    """Minimal carrier for the mixin methods. Provides only what the
    no-browser branches read; never constructs a real session."""
    def __init__(self):
        self.site_id = "char_test"

    def _teach_base_url(self):
        return "https://example.test"


def test_manual_methods_resolve_via_manual_mixin():
    for m in ("start_manual_download", "finish_manual_download",
              "cancel_manual_download", "is_awaiting_manual_download"):
        q = getattr(runner.SiteRunner, m).__qualname__
        assert q.startswith("ManualMixin."), f"{m} owned by {q}, expected ManualMixin"


def test_manual_download_session_re_exported_and_identical():
    # surface parity: runner._ManualDownloadSession still resolves and is the
    # very object now defined in runner_manual.
    assert runner._ManualDownloadSession is _ManualDownloadSession
    params = list(inspect.signature(_ManualDownloadSession.__init__).parameters)
    assert params == ["self", "runner", "target_url", "teach_base_url"], params
    pub = {x for x in vars(_ManualDownloadSession) if not x.startswith("__")}
    assert pub >= {"ready", "error", "finalize", "verify", "test_download",
                   "commit", "cancel", "snapshot_cookies"}, pub


def test_is_awaiting_reflects_session_presence():
    s = _Stub()
    assert s.is_awaiting_manual_download() is False
    s._manual_download_session = object()
    assert s.is_awaiting_manual_download() is True


def test_start_rejects_bad_url_without_launching():
    s = _Stub()
    ok, msg = s.start_manual_download("not-a-url")
    assert ok is False and "http(s)" in msg
    ok, msg = s.start_manual_download("")
    assert ok is False and "http(s)" in msg


def test_start_refuses_when_session_already_active():
    s = _Stub()
    s._manual_download_session = object()
    ok, msg = s.start_manual_download("https://example.test/video")
    assert ok is False and "Already a manual download" in msg


def test_finish_and_cancel_noop_without_session():
    s = _Stub()
    ok, msg = s.finish_manual_download()
    assert ok is False and msg == "No pending manual download"
    ok, msg = s.cancel_manual_download()
    assert ok is False and msg == "No pending manual download"
