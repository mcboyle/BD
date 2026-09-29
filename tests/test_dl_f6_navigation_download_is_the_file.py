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


# -- dl-f6-dp13 (integrator REDIFF, DP-13 ratchet): the two dl-f6 handlers are narrowed and logged --------------


def _events(runner, kind):
    return [e for e in runner._event_log if e.get("kind") == kind]


def test_a_noop_cancel_of_the_unwanted_download_is_logged(tmp_path, monkeypatch):
    download = _Download()

    def cancel():
        raise PWError("Target page, context or browser has been closed")

    download.cancel = cancel
    runner = _runner(tmp_path)
    runner._resolve_write_dir = lambda: ""
    _run(tmp_path, monkeypatch, _Page("Page.goto: Download is starting", download), runner)
    assert runner.failures == [
        (_URL, "URL is a file download but no download directory resolves")
    ]
    events = _events(runner, "download_cancel")
    assert events and "Target page, context or browser has been closed" in events[-1]["message"], (
        f"DL_F6_DP13_CANCEL_SILENT: {list(runner._event_log)[-3:]}")


def test_a_failed_download_hook_is_logged_and_the_event_is_still_taken(tmp_path, monkeypatch):
    download = _Download()
    page = _Page("Page.goto: Download is starting", None)

    def on(_event, _handler):
        raise PWError("Target page, context or browser has been closed")

    page.on = on
    page.wait_for_event = lambda *_a, **_k: download
    runner, _logged = _run(tmp_path, monkeypatch, page)
    assert runner.jobs[_URL].get("status") == "done", runner.jobs[_URL]
    events = _events(runner, "nav_download")
    assert events and "download hook not installed" in events[-1]["message"], (
        f"DL_F6_DP13_HOOK_SILENT: {list(runner._event_log)[-3:]}")


def test_a_page_without_on_still_reaches_goto(tmp_path, monkeypatch):
    """Lens B15-B R1: the lane's handler absorbed AttributeError (a duck-typed page with no .on(),
    as in test_row778's fakes). Narrowing keeps that page on its way to goto and logs the missing hook."""
    download = _Download()
    inner = _Page("Page.goto: Download is starting", None)
    inner.wait_for_event = lambda *_a, **_k: download

    class _NoOnPage:
        def __getattr__(self, name):
            if name == "on":
                raise AttributeError("'_NoOnPage' object has no attribute 'on'")
            return getattr(inner, name)

    runner, _logged = _run(tmp_path, monkeypatch, _NoOnPage())
    assert inner.gotos == 1, "DL_F6_DP13_NO_ON_NEVER_NAVIGATED"
    assert runner.jobs[_URL].get("status") == "done", runner.jobs[_URL]
    events = _events(runner, "nav_download")
    assert events and "has no attribute 'on'" in events[-1]["message"], (
        f"DL_F6_DP13_HOOK_SILENT: {list(runner._event_log)[-3:]}")


def test_the_dl_f6_handlers_add_no_swallowed_exception(tmp_path):
    """No pass/log-only handler on the try that cancels the unwanted download or installs the hook.
    Positive control: the same probe sees a pass-only handler."""
    import ast
    import json
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    scan = root / "toolchain" / "bin" / "bd-defect-scan"

    def dp13(path):
        out = subprocess.run([sys.executable, str(scan), "--file", str(path), "--json"],
                             capture_output=True, text=True, check=True, cwd=root).stdout
        return {f["line"] for f in json.loads(out) if f["dp"] == "DP-13"}

    control = tmp_path / "control.py"
    control.write_text("def f(d):\n    try: d.cancel()\n    except Exception: pass\n")
    assert dp13(control) == {3}, "probe cannot see a pass-only handler"

    src_path = root / "bulk_downloader" / "runner.py"
    src = src_path.read_text(encoding="utf-8")
    handlers = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Try):
            body = "".join(ast.get_source_segment(src, s) or "" for s in node.body)
            if body in ('dl.cancel()', 'page.on("download",lambda d: _nav_downloads.append(d))'):
                handlers |= {h.lineno for h in node.handlers}
    assert len(handlers) == 2, f"the two dl-f6 try blocks moved: {sorted(handlers)}"
    swallowed = handlers & dp13(src_path)
    assert not swallowed, f"DL_F6_DP13_SWALLOWED: lines {sorted(swallowed)}"
