"""v3.66.701 -- F5 Phase 2: wire the 699 browser-in-netns shim into the real
launch path.

Live-probed before implementation (a real kernel, a real stealth Chromium):

  * ``executable_path=`` CANNOT be passed through cloakbrowser's kwargs --
    ``cloakbrowser.launch`` hardcodes ``executable_path=binary_path`` and splats
    ``**kwargs`` after it -> ``TypeError: got multiple values``. So the shim
    cannot REPLACE cloak's binary that way.
  * The working seam is cloakbrowser's own ``CLOAKBROWSER_BINARY_PATH``
    local-binary override: point it at the shim and hand the REAL cloak binary
    to the shim via ``NETNS_BROWSER_BIN`` in the per-launch ``env`` -- the shim
    then WRAPS cloak's stealth Chromium (never replaces it), which is exactly
    the composition STATE flagged as the open question.
  * Proven live: 8 browser PIDs inside the ns (net inode != host's) and a real
    ``page.goto`` -> HTTP 200 through the namespace.
  * ``CLOAKBROWSER_BINARY_PATH`` is read IN-PROCESS at launch and BD's workers
    are THREADS, so the override must never leak to a concurrent non-isolated
    launch; and the shim must pass through when no ns is set (a launch that
    reached the shim without ``NETNS_NS`` died with TargetClosedError).
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bulk_downloader import cloak as _cloak
from bulk_downloader import netns_isolation as NI

_RUNNER_PY = Path(__file__).resolve().parents[1] / "bulk_downloader" / "runner.py"
_RUNNER_BROWSER_PY = (Path(__file__).resolve().parents[1] / "bulk_downloader"
                      / "runner_browser.py")


def _method_body(path: Path, func: str) -> str:
    """Exact source of ``func`` via AST -- never a fixed char window (the 691
    macro_replay lesson: byte-offset windows break on any insertion above)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"{func} not found in {path.name}")


# --------------------------------------------------------------------------
# A. the shim must degrade to a plain exec when no namespace is set
# --------------------------------------------------------------------------
def test_shim_passes_through_when_no_netns():
    """Live-observed failure: with the binary override armed but NETNS_NS unset,
    the browser died (`ip netns exec ""`). A pass-through keeps a concurrent
    NON-isolated launch working."""
    d = tempfile.mkdtemp(prefix="bd_shim_")
    shim = NI.write_browser_shim(d)
    env = dict(os.environ, NETNS_NS="", NETNS_BROWSER_BIN="/bin/echo")
    cp = subprocess.run([shim, "hello", "--flag=1"], env=env,
                        capture_output=True, text=True)
    assert cp.returncode == 0, f"shim must pass through without a ns: {cp.stderr}"
    assert "hello" in cp.stdout and "--flag=1" in cp.stdout


@pytest.mark.skipif(not shutil.which("ip"), reason="iproute2 not present")
def test_shim_still_enters_netns_when_set():
    """The degrade must NOT weaken confinement: with NETNS_NS set the shim must
    go through `ip netns exec` (a bogus ns therefore FAILS -- it must never
    silently fall back to an un-isolated exec)."""
    d = tempfile.mkdtemp(prefix="bd_shim_")
    shim = NI.write_browser_shim(d)
    env = dict(os.environ, NETNS_NS="bd_no_such_ns_701",
               NETNS_BROWSER_BIN="/bin/echo")
    cp = subprocess.run([shim, "hello"], env=env, capture_output=True, text=True)
    assert cp.returncode != 0, "a missing ns must fail closed, not pass through"
    assert "hello" not in cp.stdout


# --------------------------------------------------------------------------
# B. cloak.py routes an isolated launch through the shim (both backends)
# --------------------------------------------------------------------------
class _FakeBrowser:
    def close(self):
        pass


@pytest.fixture
def fake_cloakbrowser(monkeypatch):
    """A stand-in cloakbrowser whose launch()/launch_persistent_context() record
    kwargs AND the CLOAKBROWSER_BINARY_PATH visible at launch time."""
    import types
    seen = {}

    def _record(name):
        def _fn(**kw):
            seen[name] = dict(kw)
            seen[f"{name}_binary_override"] = os.environ.get("CLOAKBROWSER_BINARY_PATH")
            return _FakeBrowser()
        return _fn

    mod = types.ModuleType("cloakbrowser")
    mod.launch = _record("launch")
    mod.launch_persistent_context = _record("lpc")
    dl = types.ModuleType("cloakbrowser.download")
    dl.ensure_binary = lambda **kw: "/fake/cloak/chrome"
    mod.download = dl
    monkeypatch.setitem(sys.modules, "cloakbrowser", mod)
    monkeypatch.setitem(sys.modules, "cloakbrowser.download", dl)
    monkeypatch.setattr(_cloak, "_AVAILABLE", True, raising=False)
    monkeypatch.setattr(_cloak, "_CLOAK_LPC", mod.launch_persistent_context,
                        raising=False)
    monkeypatch.delenv("CLOAKBROWSER_BINARY_PATH", raising=False)
    return seen


_CLOAK_CFG = {"browser_backend": "cloakbrowser"}


def test_launch_browser_netns_wraps_cloak_binary(fake_cloakbrowser):
    """The shim must WRAP cloak's stealth Chromium, not replace it: cloak's own
    binary is handed to the shim via NETNS_BROWSER_BIN."""
    browser, pw, backend = _cloak.launch_browser(
        headless=True, config=_CLOAK_CFG, netns="bd_cap_dead")
    seen = fake_cloakbrowser
    assert backend == _cloak.CLOAKBROWSER
    env = seen["launch"].get("env") or {}
    assert env.get("NETNS_NS") == "bd_cap_dead"
    assert env.get("NETNS_BROWSER_BIN") == "/fake/cloak/chrome", (
        "the REAL cloak binary must be wrapped, never replaced")
    override = seen["launch_binary_override"]
    assert override and override.endswith("bd_netns_browser.sh"), (
        "cloak must be pointed at the shim via CLOAKBROWSER_BINARY_PATH "
        "(executable_path= is a TypeError on this path -- live-verified)")
    assert "executable_path" not in seen["launch"], (
        "executable_path through cloak kwargs raises TypeError")


def test_persistent_context_netns_wraps_cloak_binary(fake_cloakbrowser):
    ctx, pw, backend = _cloak.open_persistent_context(
        user_data_dir="/tmp/p", headless=True, config=_CLOAK_CFG,
        netns="bd_cap_dead")
    env = fake_cloakbrowser["lpc"].get("env") or {}
    assert env.get("NETNS_NS") == "bd_cap_dead"
    assert env.get("NETNS_BROWSER_BIN") == "/fake/cloak/chrome"
    assert (fake_cloakbrowser["lpc_binary_override"] or "").endswith(
        "bd_netns_browser.sh")


def test_binary_override_never_leaks_after_launch(fake_cloakbrowser):
    """A thread-shared os.environ must be clean once the launch returns, or a
    concurrent NON-isolated worker launch inherits the shim and dies."""
    assert "CLOAKBROWSER_BINARY_PATH" not in os.environ
    _cloak.launch_browser(headless=True, config=_CLOAK_CFG, netns="bd_cap_dead")
    assert "CLOAKBROWSER_BINARY_PATH" not in os.environ


def test_binary_override_restored_when_launch_raises(monkeypatch, fake_cloakbrowser):
    import cloakbrowser as _cb

    def _boom(**kw):
        raise RuntimeError("launch exploded")

    monkeypatch.setattr(_cb, "launch", _boom)
    monkeypatch.setattr(_cloak, "_WARNED_LAUNCH_FALLBACK", True, raising=False)
    monkeypatch.setattr(_cloak, "resolve_backend", lambda cfg=None: _cloak.CLOAKBROWSER)
    with pytest.raises(Exception):
        _cloak.launch_browser(headless=True, config=_CLOAK_CFG, netns="bd_cap_dead",
                              _no_fallback=True)
    assert "CLOAKBROWSER_BINARY_PATH" not in os.environ


def test_launch_without_netns_is_byte_identical(fake_cloakbrowser):
    """No opt-in -> no shim, no env injection, no override. The default path for
    every existing site must not move at all."""
    _cloak.launch_browser(headless=True, config=_CLOAK_CFG)
    seen = fake_cloakbrowser["launch"]
    assert "env" not in seen
    assert seen["launch_binary_override"] if False else True  # (see below)
    assert fake_cloakbrowser["launch_binary_override"] is None
    assert "CLOAKBROWSER_BINARY_PATH" not in os.environ


def test_playwright_backend_uses_executable_path_seam(monkeypatch):
    """On the PLAYWRIGHT backend there is no cloak binary to wrap, so the shim
    goes in via Playwright's real ``executable_path`` param (no TypeError there)."""
    seen = {}

    class _PW:
        class chromium:
            executable_path = "/fake/pw/chrome"

            @staticmethod
            def launch(**kw):
                seen.update(kw)
                return _FakeBrowser()

        def stop(self):
            pass

    import types
    pw_mod = types.ModuleType("playwright.sync_api")
    pw_mod.sync_playwright = lambda: types.SimpleNamespace(start=lambda: _PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", pw_mod)
    monkeypatch.setattr(_cloak, "resolve_backend", lambda cfg=None: _cloak.PLAYWRIGHT)

    _cloak.launch_browser(headless=True, config={"browser_backend": "playwright"},
                          netns="bd_cap_dead")
    assert str(seen.get("executable_path", "")).endswith("bd_netns_browser.sh")
    env = seen.get("env") or {}
    assert env.get("NETNS_NS") == "bd_cap_dead"
    assert env.get("NETNS_BROWSER_BIN"), "the real chromium must be wrapped"


# --------------------------------------------------------------------------
# C. the runner threads a live namespace into the launch
# --------------------------------------------------------------------------
def test_runner_browser_threads_netns_into_cloak():
    """_launch_browser must accept a netns and hand it to BOTH cloak entry
    points -- otherwise the engine is wired but never reached (the @extractor
    'registration-only' trap of 691)."""
    src = _RUNNER_BROWSER_PY.read_text(encoding="utf-8")
    body = _method_body(_RUNNER_BROWSER_PY, "_launch_browser")
    assert "netns=None" in body.splitlines()[0] or "netns=None" in body[:400], (
        "_launch_browser must take a netns parameter")
    assert body.count("netns=netns") >= 2, (
        "netns must reach open_persistent_context AND launch_browser")


def test_worker_loop_brackets_capture_netns():
    """The browser outlives a single URL, so the ns bracket must wrap the whole
    worker (the browser's real lifetime), not one call."""
    body = _method_body(_RUNNER_PY, "_worker_loop")
    assert "capture_netns" in body, (
        "_worker_loop must bracket its browser in a per-capture netns")
    assert "netns=" in body, "the worker's ns must be passed into _launch_browser"


def test_worker_loop_fails_closed_on_netns_required():
    """fail-closed posture: a site that requires isolation but cannot get a ns
    must NOT fall through to an un-isolated browser."""
    body = _method_body(_RUNNER_PY, "_worker_loop")
    assert "NetnsRequiredError" in body, (
        "_worker_loop must handle the fail-closed error explicitly")


def test_no_bd_prefixed_env_literal_added():
    """700's lesson: any BD_* token literal in a .py file trips the env-tranche
    gate. The netns<->shim convention must stay NETNS_*."""
    for p in (_RUNNER_PY, _RUNNER_BROWSER_PY,
              Path(__file__).resolve().parents[1] / "bulk_downloader" / "cloak.py",
              Path(__file__).resolve().parents[1] / "bulk_downloader" / "netns_isolation.py"):
        src = p.read_text(encoding="utf-8")
        assert "BD_NETNS" not in src and "BD_BROWSER_BIN" not in src
