"""cloak.open_persistent_context — honest no-display fallback error (offline).

Zero-arg functions for the custom runner; stdlib-only, no real browser/display.
We force the Playwright backend via ``config={"browser_backend": "playwright"}``
(so the cloak path is skipped) and inject a FAKE ``playwright.sync_api`` whose
``launch_persistent_context`` either raises a chosen error or returns a sentinel.
``DISPLAY`` is controlled via ``os.environ`` with try/finally restore.

Guarantees:
  1. A headed launch that fails with no display -> a clear, actionable
     CloakLaunchError, with the original exception preserved as __cause__.
  2. Display-set-but-broken is still classified via the error text.
  3. A non-display failure (display present) re-raises the ORIGINAL unchanged.
  4. A headless failure is NEVER reclassified (behaviour preserved).
  5. The successful Playwright path is unchanged: (context, pw, "playwright").
"""
import os
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.cloak as cloak


# ── fakes / fixtures ─────────────────────────────────────────────────────────
class _FakeChromium:
    def __init__(self, *, raise_exc=None, sentinel=None):
        self._raise = raise_exc
        self._sentinel = sentinel
        self.calls = []

    def launch_persistent_context(self, **kw):
        self.calls.append(kw)
        if self._raise is not None:
            raise self._raise
        return self._sentinel

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


def _install_fake_playwright(*, raise_exc=None, sentinel=None):
    """Override playwright.sync_api with a fake. Returns (restore_fn, fake_pw)."""
    chromium = _FakeChromium(raise_exc=raise_exc, sentinel=sentinel)
    fake_pw = _FakePW(chromium)

    sync_mod = types.ModuleType("playwright.sync_api")

    class _Starter:
        def start(self_inner):
            return fake_pw

    def sync_playwright():
        return _Starter()

    sync_mod.sync_playwright = sync_playwright

    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    if sys.modules.get("playwright") is None:
        pkg = types.ModuleType("playwright")
        pkg.__path__ = []  # mark as package
        sys.modules["playwright"] = pkg
    sys.modules["playwright.sync_api"] = sync_mod

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    return restore, fake_pw


def _set_display(value):
    """Set or clear DISPLAY; return the previous value for restore."""
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


def _open(**kw):
    """Call open_persistent_context forced onto the Playwright backend."""
    return cloak.open_persistent_context(
        user_data_dir="/tmp/_cloak_test_profile",
        config={"browser_backend": "playwright"},
        **kw,
    )


# ── 1. headed + no DISPLAY -> clear CloakLaunchError, cause preserved ─────────
def test_no_display_headed_failure_is_clarified():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly\n<deep playwright noise>")
    restore, _pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert isinstance(raised, cloak.CloakLaunchError), type(raised)
        msg = str(raised)
        assert "requires a display" in msg, msg
        assert "Xvfb/noVNC or run headless" in msg, msg
        # original is preserved (not hidden)
        assert raised.__cause__ is original
        # and survives the callers' [:80] truncation
        assert "Xvfb/noVNC or run headless" in msg[:80], msg[:80]
    finally:
        _restore_display(prev)
        restore()


# ── 2. headed + DISPLAY set but error text says no display -> clarified ──────
def test_no_display_classified_by_error_text_when_display_set():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Missing X server or $DISPLAY")
    restore, _pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(":99")
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert isinstance(raised, cloak.CloakLaunchError), type(raised)
        assert raised.__cause__ is original
    finally:
        _restore_display(prev)
        restore()


# ── 3. headed + DISPLAY set + unrelated failure -> ORIGINAL re-raised ─────────
def test_non_display_failure_with_display_set_reraises_unchanged():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Executable doesn't exist at /path/to/chrome")
    restore, _pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(":99")
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        # NOT reclassified: the original propagates unchanged
        assert raised is original, repr(raised)
        assert not isinstance(raised, cloak.CloakLaunchError)
    finally:
        _restore_display(prev)
        restore()


# ── 4. headless failure is never reclassified (behaviour preserved) ──────────
def test_headless_failure_not_reclassified():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")  # generic crash; not a marker
    restore, _pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _open(headless=True)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)
        assert not isinstance(raised, cloak.CloakLaunchError)
    finally:
        _restore_display(prev)
        restore()


# ── 5. successful Playwright path unchanged ──────────────────────────────────
def test_successful_playwright_path_unchanged():
    cloak.reset_cache_for_tests()
    sentinel = object()
    restore, fake_pw = _install_fake_playwright(sentinel=sentinel)
    prev = _set_display(None)
    try:
        ctx, pw, backend = _open(headless=False)
        assert ctx is sentinel
        assert pw is fake_pw
        assert backend == cloak.PLAYWRIGHT
        # launch was attempted with the forwarded headless flag
        assert fake_pw.chromium.calls and fake_pw.chromium.calls[0]["headless"] is False
    finally:
        _restore_display(prev)
        restore()


# ── 6. classifier unit: returns None vs CloakLaunchError per inputs ──────────
def test_clarify_helper_classification():
    prev = _set_display(None)
    try:
        # headless -> None regardless of text
        assert cloak._clarify_launch_error(RuntimeError("Missing X server"),
                                           headless=True) is None
        # headed + no DISPLAY -> CloakLaunchError
        err = cloak._clarify_launch_error(RuntimeError("boom"), headless=False)
        assert isinstance(err, cloak.CloakLaunchError)
    finally:
        _restore_display(prev)
    # headed + DISPLAY set + non-display text -> None
    prev = _set_display(":99")
    try:
        assert cloak._clarify_launch_error(
            RuntimeError("Executable doesn't exist"), headless=False) is None
    finally:
        _restore_display(prev)


# ── 7. marker heuristic (display-specific only) ──────────────────────────────
def test_looks_like_no_display_markers():
    assert cloak._looks_like_no_display(RuntimeError("Missing X server or $DISPLAY"))
    assert cloak._looks_like_no_display(RuntimeError("cannot open display :0"))
    # generic crash strings are deliberately NOT display markers (they would
    # misclassify ordinary DISPLAY-set headed crashes); see _DISPLAY_ERROR_MARKERS.
    assert not cloak._looks_like_no_display(RuntimeError("Browser closed unexpectedly"))
    assert not cloak._looks_like_no_display(RuntimeError("Target page, context or browser has been closed"))
    assert not cloak._looks_like_no_display(RuntimeError("Executable doesn't exist"))
    assert not cloak._looks_like_no_display(RuntimeError("net::ERR_CONNECTION_REFUSED"))


# ── 8. GRAY ZONE (regression): headed + DISPLAY set + generic crash ──────────
# HIGH VERIFY found the prior marker set reclassified an ordinary DISPLAY-set
# headed crash ("Browser closed unexpectedly") as a missing-display problem.
# After narrowing the markers, such a crash must re-raise the ORIGINAL unchanged.
def test_headed_display_set_generic_crash_reraises_unchanged():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")
    restore, _pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(":99")
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)
        assert not isinstance(raised, cloak.CloakLaunchError)
    finally:
        _restore_display(prev)
        restore()


# ── Finding B: pw.stop() on persistent-context launch failure ────────────────
# After sync_playwright().start() succeeds, a launch_persistent_context failure
# previously left the started Playwright instance (its node/driver subprocess)
# leaked, because the caller only receives ``pw`` on the success path. The fix
# stops ``pw`` in the failure branch, guarded so it never masks the original
# error and without altering which error is raised.
def test_launch_failure_stops_pw_on_clarified_path():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")  # headed+no-display -> clarified
    restore, fake_pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        # error behaviour unchanged ...
        assert isinstance(raised, cloak.CloakLaunchError), type(raised)
        assert raised.__cause__ is original
        # ... AND the leaked Playwright instance was stopped
        assert fake_pw.stopped is True
    finally:
        _restore_display(prev)
        restore()


def test_launch_failure_stops_pw_on_reraise_path():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Executable doesn't exist at /path/to/chrome")  # re-raised unchanged
    restore, fake_pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(":99")
    try:
        raised = None
        try:
            _open(headless=False)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)          # original propagates unchanged
        assert not isinstance(raised, cloak.CloakLaunchError)
        assert fake_pw.stopped is True                   # still stopped on the re-raise path
    finally:
        _restore_display(prev)
        restore()


def test_launch_failure_stops_pw_headless():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Browser closed unexpectedly")  # headless -> never reclassified
    restore, fake_pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(None)
    try:
        try:
            _open(headless=True)
        except BaseException:  # noqa: BLE001
            pass
        assert fake_pw.stopped is True
    finally:
        _restore_display(prev)
        restore()


def test_successful_path_does_not_stop_pw():
    # On success ``pw`` is handed to the caller, who owns its lifecycle; the
    # failure-path stop must NOT fire.
    cloak.reset_cache_for_tests()
    sentinel = object()
    restore, fake_pw = _install_fake_playwright(sentinel=sentinel)
    prev = _set_display(None)
    try:
        ctx, pw, backend = _open(headless=False)
        assert ctx is sentinel and pw is fake_pw
        assert fake_pw.stopped is False
    finally:
        _restore_display(prev)
        restore()


# ── Finding B (twin): launch_browser stops pw on launch() failure ────────────
def _launch(**kw):
    return cloak.launch_browser(config={"browser_backend": "playwright"}, **kw)


def test_launch_browser_failure_stops_pw():
    cloak.reset_cache_for_tests()
    original = RuntimeError("Executable doesn't exist at /path/to/chrome")
    restore, fake_pw = _install_fake_playwright(raise_exc=original)
    prev = _set_display(None)
    try:
        raised = None
        try:
            _launch(headless=True)
        except BaseException as e:  # noqa: BLE001
            raised = e
        assert raised is original, repr(raised)   # original re-raised unchanged
        assert fake_pw.stopped is True            # leaked pw stopped
    finally:
        _restore_display(prev)
        restore()


def test_launch_browser_success_does_not_stop_pw():
    cloak.reset_cache_for_tests()
    sentinel = object()
    restore, fake_pw = _install_fake_playwright(sentinel=sentinel)
    prev = _set_display(None)
    try:
        browser, pw, backend = _launch(headless=True)
        assert browser is sentinel and pw is fake_pw and backend == cloak.PLAYWRIGHT
        assert fake_pw.stopped is False
    finally:
        _restore_display(prev)
        restore()
