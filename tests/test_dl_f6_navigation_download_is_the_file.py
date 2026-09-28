"""dl-f6 (findings/APP-DOWNLOAD-TEST-20260928.md F6): a URL that IS a file must be saved, not retried.

O1508 part C queued 8 plain AVI/MOV/WMV URLs. Chromium does not render those types, so ``page.goto`` raised
Playwright's ``Error: Page.goto: Download is starting``. ``_process_one`` caught only ``PWTimeout``; the error
escaped to the worker loop as ``[BD-GEN-000]`` and every job sat in a 1 h retry backoff with nothing saved.

Fix: the Download that navigation started is handed to ``_do_download`` as the file (browser transfer) and the
job finishes ``done``. Any other navigation error still propagates unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from playwright.sync_api import Error as PWError

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_URL = "https://files.example.test/storage/file_example_AVI_480_750kB.avi"
_BYTES = b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 64


class _Download:
    def __init__(self):
        self.url = _URL
        self.suggested_filename = "file_example_AVI_480_750kB.avi"
        self.saved_to: list[str] = []
        self.cancelled = False

    def save_as(self, path):
        Path(path).write_bytes(_BYTES)
        self.saved_to.append(str(path))

    def cancel(self):
        self.cancelled = True

    def failure(self):
        return None


class _Page:
    """goto behaves like Chromium on a non-renderable media URL: fire 'download', then raise."""

    def __init__(self, goto_error: str, download: _Download | None):
        self.url = "about:blank"
        self._handlers: dict[str, list] = {}
        self._goto_error = goto_error
        self._download = download
        self.gotos = 0

    def on(self, event, handler):
        # Playwright's sync API tags every handler (setattr); a builtin such as list.append refuses
        # that with AttributeError, so the fake does the same.
        handler._pw_impl_instance_ = None
        self._handlers.setdefault(event, []).append(handler)

    def goto(self, *_args, **_kwargs):
        self.gotos += 1
        if self._download is not None:
            for handler in self._handlers.get("download", []):
                handler(self._download)
        raise PWError(self._goto_error)

    def wait_for_event(self, *_args, **_kwargs):
        raise PWError('Timeout 10000ms exceeded while waiting for event "download"')

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


def _runner(tmp_path):
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        "o1508",
        {
            "name": "o1508-file-examples",
            "download_dir": str(tmp_path),
            "wait": 0,
            # hermetic: the fixture bytes are not real media, and these reach ffprobe / a satellite RPC
            "verify_integrity": False,
            "embed_metadata": False,
        },
    )
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
    runner.failures = []
    runner._handle_failure = lambda url, msg, *a, **k: runner.failures.append(
        (url, msg)
    )
    return runner


def _run(tmp_path, monkeypatch, page, runner=None):
    import bulk_downloader.runner as runner_module
    import bulk_downloader.runner_transport as transport

    runner = runner or _runner(tmp_path)
    logged = []
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *a, **k: logged.append((a, k)))
    monkeypatch.setattr(
        transport.staging_claim,
        "reserve",
        lambda path, _identity: (path, path.with_suffix(".part")),
    )
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    with (
        mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None),
        mock.patch.object(
            runner_module._interstitial, "clear_gates", lambda *_a, **_k: None
        ),
    ):
        runner._process_one(None, _URL, persistent_ctx=_Context(page))
    return runner, logged


def test_navigation_download_is_saved_as_the_file(tmp_path, monkeypatch):
    download = _Download()
    page = _Page("Page.goto: Download is starting", download)
    try:
        runner, logged = _run(tmp_path, monkeypatch, page)
    except PWError as e:
        pytest.fail(
            f"DL_F6_NAV_DOWNLOAD_ESCAPED: _process_one let the navigation download escape as a "
            f"worker error ({e}); the job is retried in 1 h and nothing is saved"
        )
    job = runner.jobs[_URL]
    assert job.get("status") == "done", (
        f"DL_F6_NOT_DONE: job={job!r} failures={runner.failures!r}"
    )
    saved = [Path(p) for p in download.saved_to]
    assert (
        len(saved) == 1 and saved[0].parent == tmp_path and saved[0].suffix == ".avi"
    ), saved
    assert saved[0].read_bytes() == _BYTES
    assert page.gotos == 1, "the file URL was navigated again"
    assert runner.failures == []
    done_rows = [a for a, _k in logged if len(a) > 3 and a[3] == "done"]
    assert len(done_rows) == 1 and done_rows[0][4] == saved[0].name, logged


def test_download_event_after_the_error_is_still_taken(tmp_path, monkeypatch):
    """The event arrives AFTER goto raises -- measured with headless Chromium 151 on a loopback .avi
    (harness-work/PLAN-2040/dl-f6-negctl/probe/PROBE.txt: 0 events at raise). The arm must wait for it."""
    download = _Download()
    page = _Page("Page.goto: Download is starting", None)
    page.wait_for_event = lambda *_a, **_k: download
    runner, _logged = _run(tmp_path, monkeypatch, page)
    assert runner.jobs[_URL].get("status") == "done", runner.jobs[_URL]
    assert len(download.saved_to) == 1


def test_control_no_download_event_is_a_named_failure(tmp_path, monkeypatch):
    page = _Page("Page.goto: Download is starting", None)
    runner, _logged = _run(tmp_path, monkeypatch, page)
    assert runner.failures == [
        (_URL, "Navigation started a download but no download event arrived")
    ]


def test_control_no_write_dir_cancels_and_fails(tmp_path, monkeypatch):
    download = _Download()
    runner = _runner(tmp_path)
    runner._resolve_write_dir = lambda: ""
    _run(
        tmp_path,
        monkeypatch,
        _Page("Page.goto: Download is starting", download),
        runner,
    )
    assert download.cancelled and download.saved_to == []
    assert runner.failures == [
        (_URL, "URL is a file download but no download directory resolves")
    ]


def test_control_other_navigation_errors_still_propagate(tmp_path, monkeypatch):
    page = _Page("Page.goto: net::ERR_NAME_NOT_RESOLVED at " + _URL, None)
    with pytest.raises(PWError) as raised:
        _run(tmp_path, monkeypatch, page)
    assert "ERR_NAME_NOT_RESOLVED" in str(raised.value), (
        f"DL_F6_WRONG_ERROR: {raised.value!r}"
    )
    assert not list(tmp_path.glob("*.avi")), (
        f"DL_F6_SAVED_ON_ERROR: {list(tmp_path.iterdir())}"
    )
