"""v3.66.171 — cloak.launch_browser no-display parity (offline, stdlib-only).

Mirrors the open_persistent_context fallback tests but for the NON-persistent
launch_browser path. Forces the Playwright backend via
config={"browser_backend": "playwright"} and injects a FAKE playwright.sync_api
whose chromium.launch raises a chosen error. Guarantees:
  1. headed + no display -> CloakLaunchError ("requires a display"), original as
     __cause__, and the leaked Playwright instance is stopped.
  2. headed + a generic (non-display) crash -> the ORIGINAL re-raises unchanged,
     instance still stopped (Finding-B leak fix preserved).
  3. headless failure is NEVER reclassified.
"""
import os
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.cloak as cloak


class _FakeChromium:
    def __init__(self, raise_exc=None, sentinel=None):
        self._raise = raise_exc
        self._sentinel = sentinel
        self.calls = []

    def launch(self, **kw):
        self.calls.append(kw)
        if self._raise is not None:
            raise self._raise
        return self._sentinel


class _FakePW:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


def _install(raise_exc=None, sentinel=None):
    ch = _FakeChromium(raise_exc=raise_exc, sentinel=sentinel)
    pw = _FakePW(ch)
    sync_mod = types.ModuleType("playwright.sync_api")

    class _Starter:
        def start(self_inner):
            return pw

    sync_mod.sync_playwright = lambda: _Starter()
    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    if sys.modules.get("playwright") is None:
        pkg = types.ModuleType("playwright")
        pkg.__path__ = []
        sys.modules["playwright"] = pkg
    sys.modules["playwright.sync_api"] = sync_mod

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return restore, pw


def _set_display(value):
    prev = os.environ.get("DISPLAY")
    if value is None:
        os.environ.pop("DISPLAY", None)
    else:
        os.environ["DISPLAY"] = value
    return prev


def _restore_display(prev):
    if prev is None:
        os.environ.pop("DISPLAY", None)
    else:
        os.environ["DISPLAY"] = prev


def _launch(**kw):
    return cloak.launch_browser(config={"browser_backend": "playwright"}, **kw)


def test_launch_browser_no_display_is_clarified():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")
    restore, pw = _install(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _launch(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert isinstance(raised, cloak.CloakLaunchError), repr(raised)
        assert "requires a display" in str(raised)
        assert raised.__cause__ is original
        assert pw.stopped is True
    finally:
        _restore_display(prev)
        restore()


def test_launch_browser_generic_crash_reraises_unchanged():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Executable doesn't exist at /path/to/chrome")
    restore, pw = _install(raise_exc=original)
    prev = _set_display(":99")
    try:
        raised = None
        try:
            _launch(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)
        assert not isinstance(raised, cloak.CloakLaunchError)
        assert pw.stopped is True
    finally:
        _restore_display(prev)
        restore()


def test_launch_browser_headless_failure_never_reclassified():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")
    restore, pw = _install(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _launch(headless=True)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)
        assert not isinstance(raised, cloak.CloakLaunchError)
        assert pw.stopped is True
    finally:
        _restore_display(prev)
        restore()
