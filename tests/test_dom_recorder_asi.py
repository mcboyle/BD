"""Regression tests for the DOM-recorder ASI concatenation bug (Finding D).

Root cause (confirmed by runtime diagnostic): ``dom_recorder.recorder_script()``
joined the vendored rrweb UMD bundle and the record bootstrap with a bare
newline::

    rrweb_js() + "\\n" + _BOOTSTRAP

The rrweb bundle's last statement is ``...}))`` with **no** terminating
semicolon, and ``_BOOTSTRAP`` begins with an IIFE ``(function(){...})()``.
JavaScript ASI then parses the boundary as ``}))(function(){...})()`` — calling
the bundle's trailing value as a function, throwing
``"(intermediate value)(...) is not a function"``. The bootstrap IIFE is
consumed as that call's argument and never runs, so ``rrweb.record`` is never
started and ``dom_log`` stays empty (``dom_log_count == 0``) on **every**
backend. The fix is a single explicit statement separator::

    rrweb_js() + "\\n;\\n" + _BOOTSTRAP

These tests come in two tiers:
  * **static** (stdlib only, always run) — pin the separator and that the join
    is not the broken bare-newline form, and that no CDN/runtime-network
    dependency is introduced and assets are still read from disk.
  * **behavioral** (Playwright; skipped cleanly when no browser is available) —
    prove the bootstrap actually executes, recording starts, and a fixture
    capture yields ``dom_log_count >= 1`` with a full snapshot; plus a direct
    regression that the OLD join leaves recording un-started while the NEW one
    starts it.
"""
import pytest

from bulk_downloader.dom_recorder import (
    recorder_script, rrweb_js, snapdom_js, _BOOTSTRAP,
    _RRWEB_JS, _SNAPDOM_JS, attach_dom_recorder,
    arm_dom_recorder, drain_dom_events,
)
from bulk_downloader.dom_capture import DomCapture

try:  # behavioral tier is optional
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - env without playwright
    sync_playwright = None

_FIXTURE_URL = "http://dom-recorder-fixture.test/asi"
_FIXTURE_HTML = (
    "<body><h1 id='h'>asi-fixture</h1><div id='root'></div>"
    "<script>setTimeout(function(){var d=document.createElement('div');"
    "d.id='added';d.textContent='m';document.getElementById('root')"
    ".appendChild(d);},80);</script></body>"
)


def _install_fixture_route(page):
    """Serve the fixture as an intercepted HTTP document, never a data URL."""
    page.route(
        _FIXTURE_URL,
        lambda route: route.fulfill(
            status=200, content_type="text/html", body=_FIXTURE_HTML),
    )


# ---------------------------------------------------------------- static tier

def test_recorder_script_has_explicit_separator_before_bootstrap():
    """The bootstrap must be preceded by an explicit ``;`` statement
    terminator (ignoring whitespace), so the rrweb tail can't swallow it."""
    s = recorder_script()
    i = s.rindex(_BOOTSTRAP)
    prefix = s[:i]
    assert prefix.rstrip().endswith(";"), (
        "recorder_script() must terminate the rrweb bundle with ';' before "
        "the bootstrap IIFE; got tail: " + repr(prefix.rstrip()[-40:])
    )


def test_recorder_script_is_not_the_broken_bare_newline_join():
    """Pin the exact regression: the join must NOT be the bare-newline form
    that ASI parses as a UMD-tail function call."""
    broken = rrweb_js() + "\n" + _BOOTSTRAP
    assert recorder_script() != broken, (
        "recorder_script() regressed to the bare-newline join (ASI bug)"
    )


def test_bundle_tail_lacks_semicolon_so_separator_is_required():
    """Document why the separator is load-bearing: the vendored bundle ends
    without a ';' (so the fix is not redundant). Guards against a future
    bundle swap silently changing this assumption without notice."""
    tail = rrweb_js().rstrip()
    # The bundle ends with the UMD close (possibly followed by a sourceMap
    # comment). The key invariant: it does not itself terminate with ';'.
    last_code_line = [ln for ln in tail.splitlines() if not ln.strip().startswith("//")]
    assert last_code_line, "rrweb bundle unexpectedly empty"
    assert not last_code_line[-1].rstrip().endswith(";"), (
        "rrweb bundle now ends with ';' — if a future bundle does, the "
        "separator is harmless but this assumption note should be revisited"
    )


def test_no_cdn_or_runtime_network_in_recorder_script():
    """No CDN host / remote loader is introduced — assets are vendored."""
    s = recorder_script()
    for host in ("unpkg", "jsdelivr", "cdnjs", "cdn.skypack", "//cdn"):
        assert host not in s, f"unexpected CDN reference: {host}"
    # The bootstrap (the part we author) must not fetch anything at runtime.
    for net in ("fetch(", "XMLHttpRequest", "import(", "importScripts("):
        assert net not in _BOOTSTRAP, f"bootstrap must not perform network I/O: {net}"


def test_vendored_assets_loaded_from_disk():
    """rrweb/snapdom are read from the on-disk vendored copies, not remote."""
    assert _RRWEB_JS.is_file() and _RRWEB_JS.stat().st_size > 0
    assert _SNAPDOM_JS.is_file() and _SNAPDOM_JS.stat().st_size > 0
    assert rrweb_js() == _RRWEB_JS.read_text(encoding="utf-8")
    assert snapdom_js() == _SNAPDOM_JS.read_text(encoding="utf-8")
    # rrweb bundle is actually present in the injected script.
    assert rrweb_js() in recorder_script()


# ------------------------------------------------------------ behavioral tier

def _launch():
    if sync_playwright is None:
        pytest.skip("playwright not available in this environment")
    p = None
    try:
        p = sync_playwright().start()
        # Explicit timeout + CI-safe flags so a degraded/sandbox host SKIPS
        # (raises TimeoutError) rather than hanging the suite indefinitely.
        browser = p.chromium.launch(
            headless=True, timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return p, browser
    except Exception as e:  # pragma: no cover - env without a usable browser
        # Rule 45: a launch failure must not orphan an already-started p --
        # otherwise its event loop leaks into whatever test the xdist worker
        # picks up next, since pytest.skip() does not run this cleanup itself.
        if p is not None:
            p.stop()
        pytest.skip(f"chromium not launchable here: {e}")


def test_bootstrap_executes_and_recording_starts_on_fixture():
    """With the fix, arming after load runs the bootstrap, rrweb.record starts,
    and draining the in-page buffer yields at least one DOM event (the type-2
    full snapshot). v3.66.165: arm post-load (add_script_tag) + drain the buffer
    — no document-start add_init_script, no __bd_dom_event binding."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        page.set_default_navigation_timeout(20000)
        _install_fixture_route(page)
        cap = DomCapture(url=_FIXTURE_URL, redact=True)
        attach_dom_recorder(page, cap, redact=True)  # registrar (logs once)
        page.goto(_FIXTURE_URL, wait_until="load")
        armed = arm_dom_recorder(page)          # post-load, via add_script_tag
        page.wait_for_timeout(900)
        started = page.evaluate("!!window.__bd_rrweb_started")
        rrweb_present = page.evaluate("typeof window.rrweb === 'object'")
        buf_present = page.evaluate("Array.isArray(window.__bd_dom_buf)")
        drained = drain_dom_events(page, cap)   # pull the buffer into cap
        page.close()
    finally:
        browser.close(); p.stop()
    assert rrweb_present, "window.rrweb not defined"
    assert buf_present, "in-page __bd_dom_buf buffer not present"
    assert armed and started, "rrweb.record never started (arm did not run)"
    assert drained >= 1, f"drain returned {drained} events"
    assert len(cap.dom_log) >= 1, f"expected >=1 DOM event, got {len(cap.dom_log)}"
    assert any(ev.get("type") == "full_snapshot" for ev in cap.dom_log), (
        "no rrweb full snapshot (type 2) captured"
    )


def test_old_bare_newline_join_does_not_start_recording():
    """Direct regression for the exact ASI failure: the OLD bare-newline join
    leaves recording un-started, while the fixed recorder_script() starts it.
    The join is injected after document navigation, matching the production
    arm-after-load path; a separate benign init script proves the intercepted
    HTTP fixture retains document-start init-script semantics."""
    p, browser = _launch()
    try:
        old_join = rrweb_js() + "\n" + _BOOTSTRAP

        def started_with(script):
            page = browser.new_page()
            page.set_default_navigation_timeout(20000)
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            # v3.66.165: the bootstrap's readiness gate is now window.rrweb only
            # (no __bd_dom_event binding), so nothing extra need be exposed —
            # the join is the only variable under test.
            page.add_init_script("window.__bd_fixture_init = true")
            _install_fixture_route(page)
            page.goto(_FIXTURE_URL, wait_until="load")
            assert page.evaluate("window.__bd_fixture_init") is True
            page.add_script_tag(content=script)
            page.wait_for_timeout(700)
            val = page.evaluate("!!window.__bd_rrweb_started")
            page.close()
            return val, errs

        old_started, old_errs = started_with(old_join)
        new_started, _ = started_with(recorder_script())
    finally:
        browser.close(); p.stop()
    assert old_started is False, "OLD bare-newline join unexpectedly started recording"
    assert any("is not a function" in e for e in old_errs), (
        "expected the ASI '(...) is not a function' pageerror on the OLD join"
    )
    assert new_started is True, "fixed recorder_script() failed to start recording"


def test_direct_rrweb_record_still_works():
    """Sanity: rrweb itself is fine — a minimal record({emit}) emits events."""
    p, browser = _launch()
    try:
        page = browser.new_page()
        page.set_default_navigation_timeout(20000)
        _install_fixture_route(page)
        page.goto(_FIXTURE_URL, wait_until="load")
        page.add_script_tag(content=rrweb_js())
        kinds = page.evaluate(
            "() => { const ev=[]; const stop=window.rrweb.record({emit:e=>ev.push(e.type)});"
            " return new Promise(r=>setTimeout(()=>{ if(stop)stop(); r(ev); }, 300)); }"
        )
        page.close()
    finally:
        browser.close(); p.stop()
    assert len(kinds) >= 1, "direct rrweb.record emitted nothing"
    assert 2 in kinds, "direct rrweb.record produced no full snapshot (type 2)"


# Rule 45: exercise each real launcher body with a real driver, without
# starting the E2E server or depending on a usable Chromium executable.
_LAUNCH_FAILURE_SEAMS = [
    ("test_dom_recorder_asi.py", "_launch"),
    ("test_e2e_smoke.py", "setUpClass"),
    ("test_row373_login_trigger.py", "launch_for_test"),
    ("test_v3_66_1016_login_interstitial.py", "_fake"),
    ("test_v3_66_1020_residues.py", "_fake"),
]


def _check_launch_failure_cleanup(monkeypatch, tmp_path, filename, function, *, delete_stop=False):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    import unittest
    import urllib.request
    from playwright.sync_api import sync_playwright as real_playwright

    source = Path(__file__).with_name(filename).read_text()
    matches = [n for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.FunctionDef) and n.name == function]
    assert len(matches) == 1, "PW-PROBE-AMBIGUOUS-SUBJECT"
    node = matches[0]
    node.decorator_list = []  # bind setUpClass explicitly below
    if delete_stop:
        class DeleteStop(ast.NodeTransformer):
            removed = 0

            def visit_Expr(self, statement):
                call = statement.value
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "stop"):
                    self.removed += 1
                    return ast.copy_location(ast.Pass(), statement)
                return self.generic_visit(statement)

        mutation = DeleteStop()
        node = mutation.visit(node)
        assert mutation.removed == 1, "PW-MUTANT-INVALID-STOP-CENSUS"

    counts = {"start": 0, "stop": 0, "launch": 0}
    active = []

    class Driver:
        def __init__(self, real):
            self.real = real
            self.closed = False
            self.chromium = self

        def launch(self, **kwargs):
            counts["launch"] += 1
            kwargs["executable_path"] = str(tmp_path / "nonexistent-chromium")
            return self.real.chromium.launch(**kwargs)

        def stop(self):
            counts["stop"] += 1
            self.real.stop()
            self.closed = True

    class Manager:
        def start(self):
            driver = Driver(real_playwright().start())
            active.append(driver)
            counts["start"] += 1
            return driver

    class Harness:
        base_url = "http://fixture.invalid"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    namespace = {"sync_playwright": Manager, "pytest": pytest,
                 "unittest": unittest, "_BDServerHarness": Harness}
    # These are executable fixture bodies, not comment/substring anchors.
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(urllib.request, "urlopen", lambda *a, **kw: Response())
            expected = pytest.skip.Exception if function == "_launch" else Exception
            with pytest.raises(expected, match="[Ee]xecutable"):
                if function == "setUpClass":
                    namespace[function](SimpleNamespace())
                else:
                    namespace[function]()
        assert counts["start"] == counts["launch"] == 1, "PW-PROBE-NOT-REACHED"
        second_error = None
        try:
            with real_playwright():
                pass
        except Exception as exc:
            second_error = str(exc)
        assert counts["stop"] == 1 and second_error is None, (
            f"PW-LAUNCH-LEAK: {filename}; stop={counts['stop']}; next={second_error}")
    finally:
        # Also clean deliberately broken BASE/mutant cases before the next
        # pytest test runs in this worker. This happens AFTER the assertions.
        for driver in active:
            if not driver.closed:
                driver.real.stop()
                driver.closed = True


@pytest.mark.parametrize("filename,function", _LAUNCH_FAILURE_SEAMS)
def test_launch_failure_releases_playwright(monkeypatch, tmp_path, filename, function):
    _check_launch_failure_cleanup(monkeypatch, tmp_path, filename, function)


@pytest.mark.parametrize("filename,function", _LAUNCH_FAILURE_SEAMS)
def test_launch_cleanup_deletion_is_caught(monkeypatch, tmp_path, filename, function):
    with pytest.raises(AssertionError, match="PW-LAUNCH-LEAK"):
        _check_launch_failure_cleanup(
            monkeypatch, tmp_path, filename, function, delete_stop=True)


@pytest.mark.parametrize("remove_guard", [False, True])
def test_driver_start_failure_preserves_original_skip(monkeypatch, remove_guard):
    import ast
    import inspect

    starts = []

    class Manager:
        def start(self):
            starts.append(1)
            raise RuntimeError("PW-START-FAILURE")

    subject = _launch
    if remove_guard:
        tree = ast.parse(inspect.getsource(_launch))
        guards = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "p is not None"]
        assert len(guards) == 1, "PW-MUTANT-INVALID-GUARD-CENSUS"
        guards[0].test = ast.Constant(True)
        namespace = {"pytest": pytest, "sync_playwright": Manager}
        exec(compile(ast.fix_missing_locations(tree), __file__, "exec"), namespace)
        subject = namespace["_launch"]

    def check():
        with monkeypatch.context() as scoped:
            scoped.setitem(subject.__globals__, "sync_playwright", Manager)
            outcome = None
            try:
                subject()
            except pytest.skip.Exception as exc:
                outcome = str(exc)
            except Exception as exc:
                outcome = type(exc).__name__
            assert outcome and "PW-START-FAILURE" in outcome, (
                f"PW-START-CLEANUP-MASKED-ERROR: {outcome}")
        assert len(starts) == 1

    if remove_guard:
        with pytest.raises(AssertionError, match="PW-START-CLEANUP-MASKED-ERROR"):
            check()
        assert len(starts) == 1
    else:
        check()
